"""
AgentLens tracer: decorators that turn plain functions into DAG nodes.

    lens = AgentLens(endpoint="http://localhost:7430")

    @lens.trace("research_agent", max_cost_usd=0.10)
    def research_agent(query): ...

    @lens.span("retrieve_docs", kind=SpanKind.RETRIEVAL)
    def retrieve_docs(query): ...

Sync and async functions are supported identically. Export happens on a
background thread so tracing never blocks the agent.
"""

from __future__ import annotations

import functools
import inspect
import traceback
from typing import Any, Callable, Optional

from . import context as ctx
from .cost import estimate_cost_usd
from .exporters import Exporter, HttpExporter, ConsoleExporter
from .models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus, _preview


class BudgetExceeded(RuntimeError):
    """Raised when a run trips its token or cost budget guard."""

    def __init__(self, run: AgentRun, reason: str):
        super().__init__(f"AgentLens budget guard: {reason} (run={run.name})")
        self.run = run
        self.reason = reason


class AgentLens:
    """Client that owns configuration and produces tracing decorators."""

    def __init__(
        self,
        endpoint: Optional[str] = None,
        api_key: Optional[str] = None,
        exporter: Optional[Exporter] = None,
        cost_table: Optional[dict[str, tuple[float, float]]] = None,
        on_budget: str = "raise",  # "raise" | "pause" | "warn"
    ):
        if exporter is not None:
            self.exporter = exporter
        elif endpoint:
            self.exporter = HttpExporter(endpoint, api_key=api_key)
        else:
            self.exporter = ConsoleExporter()
        self.cost_table = cost_table or {}
        if on_budget not in ("raise", "pause", "warn"):
            raise ValueError("on_budget must be 'raise', 'pause', or 'warn'")
        self.on_budget = on_budget

    # ------------------------------------------------------------------ #
    # decorators
    # ------------------------------------------------------------------ #

    def trace(
        self,
        name: Optional[str] = None,
        tags: Optional[list[str]] = None,
        max_total_tokens: Optional[int] = None,
        max_cost_usd: Optional[float] = None,
        metadata: Optional[dict[str, Any]] = None,
    ) -> Callable:
        """Mark a function as an agent entrypoint. Creates an AgentRun."""

        def decorator(fn: Callable) -> Callable:
            run_name = name or fn.__name__

            def _start() -> tuple[AgentRun, Span, Any, Any]:
                run = AgentRun(
                    name=run_name,
                    tags=list(tags or []),
                    max_total_tokens=max_total_tokens,
                    max_cost_usd=max_cost_usd,
                    metadata=dict(metadata or {}),
                )
                root = Span(name=run_name, kind=SpanKind.AGENT)
                run.spans.append(root)
                run_token = ctx.set_run(run)
                span_token = ctx.set_span(root)
                return run, root, run_token, span_token

            def _finish(run, root, run_token, span_token, error: Optional[BaseException]):
                if error is None:
                    root.finish(SpanStatus.SUCCESS)
                    run.finish(SpanStatus.SUCCESS)
                elif isinstance(error, BudgetExceeded):
                    root.finish(SpanStatus.PAUSED, error=str(error))
                    run.finish(SpanStatus.PAUSED, error=str(error))
                else:
                    tb = "".join(traceback.format_exception(error)).strip()
                    root.finish(SpanStatus.ERROR, error=tb)
                    run.finish(SpanStatus.ERROR, error=str(error))
                ctx.reset_span(span_token)
                ctx.reset_run(run_token)
                try:
                    self.exporter.export(run)
                except Exception:
                    pass  # tracing must never take the agent down

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*args, **kwargs):
                    run, root, rt, st = _start()
                    root.inputs = _preview({"args": args, "kwargs": kwargs})
                    try:
                        result = await fn(*args, **kwargs)
                        root.outputs = _preview(result)
                        _finish(run, root, rt, st, None)
                        return result
                    except BaseException as e:
                        _finish(run, root, rt, st, e)
                        raise

                return async_wrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                run, root, rt, st = _start()
                root.inputs = _preview({"args": args, "kwargs": kwargs})
                try:
                    result = fn(*args, **kwargs)
                    root.outputs = _preview(result)
                    _finish(run, root, rt, st, None)
                    return result
                except BaseException as e:
                    _finish(run, root, rt, st, e)
                    raise

            return wrapper

        return decorator

    def span(
        self,
        name: Optional[str] = None,
        kind: SpanKind = SpanKind.CUSTOM,
        retries: int = 0,
    ) -> Callable:
        """Mark a function as a child step (DAG node) of the current run."""

        def decorator(fn: Callable) -> Callable:
            span_name = name or fn.__name__

            def _open(retry_of: Optional[str]) -> tuple[Optional[Span], Any]:
                run = ctx.current_run()
                if run is None:
                    return None, None  # untraced call: pass through
                parent = ctx.current_span()
                span = Span(
                    name=span_name,
                    kind=kind,
                    parent_id=parent.span_id if parent else None,
                    retry_of=retry_of,
                )
                run.spans.append(span)
                token = ctx.set_span(span)
                return span, token

            def _close(span: Span, token, error: Optional[BaseException]) -> None:
                if error is None:
                    span.finish(SpanStatus.SUCCESS)
                else:
                    tb = "".join(traceback.format_exception(error)).strip()
                    span.finish(SpanStatus.ERROR, error=tb)
                ctx.reset_span(token)
                self._check_budget()

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*args, **kwargs):
                    attempt, retry_of = 0, None
                    while True:
                        span, token = _open(retry_of)
                        if span is None:
                            return await fn(*args, **kwargs)
                        span.inputs = _preview({"args": args, "kwargs": kwargs})
                        try:
                            result = await fn(*args, **kwargs)
                            span.outputs = _preview(result)
                            _close(span, token, None)
                            return result
                        except BudgetExceeded:
                            _close(span, token, None)
                            raise
                        except BaseException as e:
                            _close(span, token, e)
                            if attempt < retries:
                                attempt += 1
                                retry_of = span.span_id
                                continue
                            raise

                return async_wrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                attempt, retry_of = 0, None
                while True:
                    span, token = _open(retry_of)
                    if span is None:
                        return fn(*args, **kwargs)
                    span.inputs = _preview({"args": args, "kwargs": kwargs})
                    try:
                        result = fn(*args, **kwargs)
                        span.outputs = _preview(result)
                        _close(span, token, None)
                        return result
                    except BudgetExceeded:
                        _close(span, token, None)
                        raise
                    except BaseException as e:
                        _close(span, token, e)
                        if attempt < retries:
                            attempt += 1
                            retry_of = span.span_id
                            continue
                        raise

            return wrapper

        return decorator

    def tool(self, name: Optional[str] = None, retries: int = 0) -> Callable:
        """Shorthand for a TOOL-kind span."""
        return self.span(name, kind=SpanKind.TOOL, retries=retries)

    def llm_call(self, name: Optional[str] = None, model: str = "", provider: str = "") -> Callable:
        """
        Trace an LLM call. Token usage is auto-extracted from OpenAI- and
        Anthropic-style response objects; cost is estimated from the model name.
        """

        def decorator(fn: Callable) -> Callable:
            inner = self.span(name or fn.__name__, kind=SpanKind.LLM)(fn)

            def _record(args, kwargs, result):
                span = None
                run = ctx.current_run()
                if run and run.spans:
                    # the span we just closed is the latest LLM span with this name
                    for s in reversed(run.spans):
                        if s.kind == SpanKind.LLM and s.name == (name or fn.__name__):
                            span = s
                            break
                if span is None:
                    return
                meta = LLMMetadata(model=model, provider=provider)
                prompt = kwargs.get("prompt") or (args[0] if args else "")
                meta.prompt_preview = _preview(prompt)
                usage = getattr(result, "usage", None) or (result.get("usage") if isinstance(result, dict) else None)
                if usage is not None:
                    get = (lambda k: getattr(usage, k, None)) if not isinstance(usage, dict) else usage.get
                    meta.input_tokens = int(get("prompt_tokens") or get("input_tokens") or 0)
                    meta.output_tokens = int(get("completion_tokens") or get("output_tokens") or 0)
                rmodel = getattr(result, "model", None) or (result.get("model") if isinstance(result, dict) else None)
                if rmodel:
                    meta.model = str(rmodel)
                meta.response_preview = _preview(result)
                meta.cost_usd = estimate_cost_usd(meta.model, meta.input_tokens, meta.output_tokens, self.cost_table)
                span.llm = meta
                self._check_budget()

            if inspect.iscoroutinefunction(fn):

                @functools.wraps(fn)
                async def async_wrapper(*args, **kwargs):
                    result = await inner(*args, **kwargs)
                    _record(args, kwargs, result)
                    return result

                return async_wrapper

            @functools.wraps(fn)
            def wrapper(*args, **kwargs):
                result = inner(*args, **kwargs)
                _record(args, kwargs, result)
                return result

            return wrapper

        return decorator

    # ------------------------------------------------------------------ #

    def _check_budget(self) -> None:
        run = ctx.current_run()
        if run is None:
            return
        reason = run.over_budget()
        if reason is None:
            return
        if self.on_budget == "raise":
            raise BudgetExceeded(run, reason)
        if self.on_budget == "pause":
            run.status = SpanStatus.PAUSED
            run.error = reason
        else:
            print(f"[agentlens] WARNING: {reason}")


# ---------------------------------------------------------------------- #
# zero-config module-level decorators (console exporter)
# ---------------------------------------------------------------------- #

_default = AgentLens()


def trace(name: Optional[str] = None, **kw) -> Callable:
    return _default.trace(name, **kw)


def tool(name: Optional[str] = None, **kw) -> Callable:
    return _default.tool(name, **kw)

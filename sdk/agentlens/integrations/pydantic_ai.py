"""
Pydantic AI integration.

Pydantic AI is already OpenTelemetry-instrumented, so the cleanest path for
an existing setup is the OTLP bridge:

    agent.instrument_all()          # emits OTel spans
    # then point your collector at http://localhost:7430/api/ingest/otlp

This module is for the direct route — no collector, no OTel dependency:

    from agentlens import AgentLens
    from agentlens.integrations.pydantic_ai import trace_agent

    lens = AgentLens(endpoint="http://localhost:7430")
    agent = trace_agent(lens, Agent("openai:gpt-4o", tools=[...]))

    result = await agent.run("what is the weather in Lisbon?")

Tool functions registered on the agent are wrapped too, so a tool call
shows up as its own node rather than being hidden inside the model step.
"""

from __future__ import annotations

import inspect
from typing import Any, Optional

from .. import context as ctx
from ..compat import format_exception
from ..cost import estimate_cost
from ..models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus, _preview


def _usage_of(result: Any) -> tuple[int, int, str]:
    """Read Pydantic AI's usage object, tolerating naming differences."""
    usage = getattr(result, "usage", None)
    if callable(usage):
        try:
            usage = usage()
        except Exception:
            usage = None
    if usage is None:
        return 0, 0, ""
    get = usage.get if isinstance(usage, dict) else (lambda k, d=0: getattr(usage, k, d))
    request = int(get("request_tokens", 0) or get("input_tokens", 0) or get("prompt_tokens", 0) or 0)
    response = int(get("response_tokens", 0) or get("output_tokens", 0) or get("completion_tokens", 0) or 0)
    model = str(getattr(result, "model_name", "") or getattr(result, "model", "") or "")
    return request, response, model


class TracedAgent:
    """Wraps a Pydantic AI Agent so runs and tool calls become spans."""

    def __init__(self, lens, agent: Any, run_name: Optional[str] = None, tags: Optional[list[str]] = None):
        self._lens = lens
        self._agent = agent
        self._run_name = run_name or getattr(agent, "name", None) or "pydantic_ai_agent"
        self._tags = list(tags or []) + ["pydantic-ai"]
        self._wrap_tools()

    def __getattr__(self, item: str) -> Any:
        return getattr(self._agent, item)

    def _wrap_tools(self) -> None:
        """
        Wrap already-registered tool functions so their execution is its own
        span. Pydantic AI has moved this registry around between versions,
        so several locations are tried and a miss is not fatal — you still
        get the run, just without per-tool nodes.
        """
        for attr in ("_function_tools", "_tools", "tools"):
            registry = getattr(self._agent, attr, None)
            if not registry:
                continue
            items = registry.items() if isinstance(registry, dict) else enumerate(registry)
            for key, tool in items:
                fn = getattr(tool, "function", None) or getattr(tool, "func", None)
                if fn is None or getattr(fn, "__agentlens_wrapped__", False):
                    continue
                name = getattr(tool, "name", None) or getattr(fn, "__name__", str(key))
                wrapped = self._lens.tool(name)(fn)
                wrapped.__agentlens_wrapped__ = True
                for target in ("function", "func"):
                    if hasattr(tool, target):
                        try:
                            setattr(tool, target, wrapped)
                        except Exception:
                            pass
            break

    # -- run wrappers --------------------------------------------------- #

    def _start(self, prompt: Any) -> tuple[AgentRun, Span, Any, Any]:
        run = AgentRun(name=self._run_name, tags=list(self._tags))
        root = Span(name=self._run_name, kind=SpanKind.AGENT)
        root.inputs = _preview(prompt)
        run.spans.append(root)
        return run, root, ctx.set_run(run), ctx.set_span(root)

    def _finish(self, run, root, rt, st, result: Any, error: Optional[BaseException]) -> None:
        if error is None:
            output = getattr(result, "output", None) or getattr(result, "data", None) or result
            root.outputs = _preview(output)

            request, response, model = _usage_of(result)
            if request or response or model:
                model_span = Span(name=model or "model", kind=SpanKind.LLM, parent_id=root.span_id)
                model_span.outputs = _preview(output)
                _cost = estimate_cost(model, request, response, self._lens.cost_table)
                model_span.llm = LLMMetadata(
                    model=model,
                    provider=str(model).split(":")[0] if ":" in str(model) else "",
                    input_tokens=request,
                    output_tokens=response,
                    cost_usd=_cost[0],
                    cost_source=_cost[1],
                    response_preview=_preview(output),
                )
                model_span.finish(SpanStatus.SUCCESS)
                run.spans.append(model_span)

            root.finish(SpanStatus.SUCCESS)
            run.finish(SpanStatus.SUCCESS)
        else:
            root.finish(SpanStatus.ERROR, error=format_exception(error))
            run.finish(SpanStatus.ERROR, error=str(error))

        ctx.reset_span(st)
        ctx.reset_run(rt)
        export = getattr(self._lens, "_export", None)
        try:
            if export is not None:
                export(run)
            else:
                self._lens.exporter.export(run)
        except Exception:
            pass

    async def run(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        run, root, rt, st = self._start(prompt)
        try:
            result = self._agent.run(prompt, *args, **kwargs)
            if inspect.isawaitable(result):
                result = await result
            self._finish(run, root, rt, st, result, None)
            return result
        except BaseException as e:
            self._finish(run, root, rt, st, None, e)
            raise

    def run_sync(self, prompt: Any, *args: Any, **kwargs: Any) -> Any:
        run, root, rt, st = self._start(prompt)
        try:
            result = self._agent.run_sync(prompt, *args, **kwargs)
            self._finish(run, root, rt, st, result, None)
            return result
        except BaseException as e:
            self._finish(run, root, rt, st, None, e)
            raise


def trace_agent(
    lens, agent: Any, run_name: Optional[str] = None, tags: Optional[list[str]] = None
) -> TracedAgent:
    """Wrap a Pydantic AI Agent so its runs and tool calls become spans."""
    return TracedAgent(lens, agent, run_name, tags)

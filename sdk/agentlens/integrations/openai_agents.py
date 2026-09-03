"""
OpenAI Agents SDK integration.

The SDK emits its own trace/span tree — agent runs, model generations,
function calls, handoffs, guardrails — through a `TracingProcessor`
interface. This adapts that stream into AgentLens runs, so agents built on
it get the DAG, run diffing, budget views, and eval scoring without any
change to the agent code itself.

    from agents import add_trace_processor
    from agentlens import AgentLens
    from agentlens.integrations.openai_agents import AgentLensTracingProcessor

    lens = AgentLens(endpoint="http://localhost:7430")
    add_trace_processor(AgentLensTracingProcessor(lens))

`add_trace_processor` keeps OpenAI's dashboard receiving traces too;
`set_trace_processors([...])` replaces it.

Span data is read by duck typing rather than by importing the SDK's types:
the shapes are still moving, and a missing attribute should cost one field,
not the whole trace.
"""

from __future__ import annotations

import threading
from typing import Any, Optional

from ..cost import estimate_cost
from ..models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus, _preview

# The SDK names its span_data classes; map the recognizable part to a kind.
KIND_BY_DATA_TYPE = {
    "agent": SpanKind.AGENT,
    "generation": SpanKind.LLM,
    "response": SpanKind.LLM,
    "function": SpanKind.TOOL,
    "handoff": SpanKind.CHAIN,
    "guardrail": SpanKind.CHAIN,
    "mcp": SpanKind.MCP,
    "custom": SpanKind.CUSTOM,
    "speech": SpanKind.CUSTOM,
    "transcription": SpanKind.CUSTOM,
}


def _data_kind(span_data: Any) -> SpanKind:
    name = type(span_data).__name__.lower().replace("spandata", "")
    for key, kind in KIND_BY_DATA_TYPE.items():
        if key in name:
            return kind
    return SpanKind.CUSTOM


def _span_name(span_data: Any, kind: SpanKind) -> str:
    for attr in ("name", "tool_name", "agent_name", "model", "type"):
        value = getattr(span_data, attr, None)
        if isinstance(value, str) and value:
            return value
    return kind.value


def _get(obj: Any, *names: str) -> Any:
    for n in names:
        value = getattr(obj, n, None)
        if value is not None:
            return value
    return None


class AgentLensTracingProcessor:
    """
    Implements the SDK's TracingProcessor interface.

    One SDK trace becomes one AgentLens run. Spans are buffered until the
    trace ends, because a run is exported whole — and because a partially
    built tree with dangling parents is worse than a slightly later one.
    Concurrent traces are kept separate under a lock, since the SDK may run
    several workflows at once in one process.
    """

    def __init__(self, lens, tags: Optional[list[str]] = None, capture_content: bool = True):
        self.lens = lens
        self.tags = list(tags or []) + ["openai-agents"]
        self.capture_content = capture_content
        self._runs: dict[str, AgentRun] = {}
        self._spans: dict[str, Span] = {}
        self._lock = threading.Lock()

    # -- traces --------------------------------------------------------- #

    def on_trace_start(self, trace: Any) -> None:
        trace_id = str(_get(trace, "trace_id", "id") or "")
        name = str(_get(trace, "name", "workflow_name") or "openai_agent")
        run = AgentRun(name=name, tags=list(self.tags))
        if trace_id:
            run.trace_id = trace_id.replace("trace_", "")[:32] or run.trace_id
            run.metadata["openai_trace_id"] = trace_id
        group_id = _get(trace, "group_id")
        if group_id:
            run.metadata["group_id"] = str(group_id)
        root = Span(name=name, kind=SpanKind.AGENT)
        run.spans.append(root)
        with self._lock:
            self._runs[trace_id] = run
            self._spans[trace_id] = root

    def on_trace_end(self, trace: Any) -> None:
        trace_id = str(_get(trace, "trace_id", "id") or "")
        with self._lock:
            run = self._runs.pop(trace_id, None)
            root = self._spans.pop(trace_id, None)
            # drop this trace's span index
            for key in [k for k, v in self._spans.items() if v is not None and k.startswith(f"{trace_id}:")]:
                self._spans.pop(key, None)
        if run is None:
            return
        failed = any(s.status == SpanStatus.ERROR for s in run.spans)
        if root is not None:
            root.finish(SpanStatus.ERROR if failed else SpanStatus.SUCCESS)
        run.finish(SpanStatus.ERROR if failed else SpanStatus.SUCCESS)
        export = getattr(self.lens, "_export", None)
        try:
            if export is not None:
                export(run)
            else:
                self.lens.exporter.export(run)
        except Exception:
            pass  # never let export failure surface into the agent

    # -- spans ---------------------------------------------------------- #

    def on_span_start(self, span: Any) -> None:
        trace_id = str(_get(span, "trace_id") or "")
        span_id = str(_get(span, "span_id") or "")
        with self._lock:
            run = self._runs.get(trace_id)
            if run is None:
                return
            parent_id = _get(span, "parent_id")
            parent = self._spans.get(f"{trace_id}:{parent_id}") if parent_id else None
            if parent is None:
                parent = self._spans.get(trace_id)

            data = _get(span, "span_data")
            kind = _data_kind(data) if data is not None else SpanKind.CUSTOM
            ours = Span(
                name=_span_name(data, kind) if data is not None else kind.value,
                kind=kind,
                parent_id=parent.span_id if parent else None,
            )
            ours.attributes["openai.span_id"] = span_id
            run.spans.append(ours)
            self._spans[f"{trace_id}:{span_id}"] = ours

    def on_span_end(self, span: Any) -> None:
        trace_id = str(_get(span, "trace_id") or "")
        span_id = str(_get(span, "span_id") or "")
        with self._lock:
            ours = self._spans.get(f"{trace_id}:{span_id}")
        if ours is None:
            return

        data = _get(span, "span_data")
        if data is not None:
            self._apply_data(ours, data)

        error = _get(span, "error")
        if error is not None:
            message = error.get("message") if isinstance(error, dict) else str(error)
            ours.finish(SpanStatus.ERROR, error=str(message)[:1000])
        else:
            ours.finish(SpanStatus.SUCCESS)

    def _apply_data(self, span: Span, data: Any) -> None:
        """Pull whatever this span_data variant happens to carry."""
        if self.capture_content:
            for attr, target in (("input", "inputs"), ("output", "outputs")):
                value = getattr(data, attr, None)
                if value is not None:
                    setattr(span, target, _preview(value))

        if span.kind == SpanKind.LLM:
            model = str(_get(data, "model") or "")
            usage = _get(data, "usage") or {}
            get = usage.get if isinstance(usage, dict) else (lambda k, d=None: getattr(usage, k, d))
            input_tokens = int(get("input_tokens", 0) or get("prompt_tokens", 0) or 0)
            output_tokens = int(get("output_tokens", 0) or get("completion_tokens", 0) or 0)
            _cost = estimate_cost(model, input_tokens, output_tokens, self.lens.cost_table)
            span.llm = LLMMetadata(
                model=model,
                provider="openai",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cost_usd=_cost[0],
                cost_source=_cost[1],
                prompt_preview=span.inputs if self.capture_content else "",
                response_preview=span.outputs if self.capture_content else "",
            )

        for attr in ("from_agent", "to_agent", "tool_name", "triggered", "type"):
            value = getattr(data, attr, None)
            if value is not None:
                span.attributes[f"openai.{attr}"] = (
                    value if isinstance(value, (str, int, float, bool)) else str(value)
                )

    # -- lifecycle ------------------------------------------------------ #

    def shutdown(self) -> None:
        """Export whatever is still open rather than losing it on exit."""
        with self._lock:
            pending = list(self._runs.items())
            self._runs.clear()
            self._spans.clear()
        for _trace_id, run in pending:
            for s in run.spans:
                if s.ended_at is None:
                    s.finish(SpanStatus.CANCELLED)
            run.finish(SpanStatus.CANCELLED)
            try:
                self.lens.exporter.export(run)
            except Exception:
                pass

    def force_flush(self) -> None:
        flush = getattr(self.lens.exporter, "flush", None)
        if flush is not None:
            try:
                flush()
            except Exception:
                pass

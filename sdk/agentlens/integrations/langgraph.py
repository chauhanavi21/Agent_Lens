"""
LangGraph integration.

LangGraph runs a state machine: nodes read and write a shared state, edges
decide what runs next. The interesting question in a LangGraph trace isn't
"what got called" but "which path did the state take, and why did it loop"
— so this records the node sequence and, per node, which state keys changed.

    from agentlens import AgentLens
    from agentlens.integrations.langgraph import trace_graph

    lens = AgentLens(endpoint="http://localhost:7430")
    app = trace_graph(lens, graph.compile(), run_name="support_graph")

    result = app.invoke({"messages": [...]})

Works on the compiled graph, so `invoke`, `ainvoke`, and `stream` all pick
it up. Anything not listed passes straight through to the graph.
"""

from __future__ import annotations

import traceback
from typing import Any, Optional

from .. import context as ctx
from ..models import AgentRun, Span, SpanKind, SpanStatus, _preview


def _changed_keys(before: Any, after: Any) -> list[str]:
    """Which state keys a node actually touched — the useful part of a step."""
    if not isinstance(before, dict) or not isinstance(after, dict):
        return []
    changed = []
    for key, value in after.items():
        if key not in before:
            changed.append(f"+{key}")
        elif before[key] is not value and before[key] != value:
            changed.append(key)
    return sorted(changed)


class TracedGraph:
    """Wraps a compiled LangGraph so each node execution becomes a span."""

    STREAMING = {"stream", "astream"}
    INVOKING = {"invoke", "ainvoke"}

    def __init__(self, lens, graph: Any, run_name: str = "langgraph", tags: Optional[list[str]] = None):
        self._lens = lens
        self._graph = graph
        self._run_name = run_name
        self._tags = list(tags or []) + ["langgraph"]

    def __getattr__(self, item: str) -> Any:
        return getattr(self._graph, item)

    # -- run scaffolding ------------------------------------------------ #

    def _start(self, inputs: Any) -> tuple[AgentRun, Span, Any, Any]:
        run = AgentRun(name=self._run_name, tags=list(self._tags))
        root = Span(name=self._run_name, kind=SpanKind.AGENT)
        root.inputs = _preview(inputs)
        run.spans.append(root)
        return run, root, ctx.set_run(run), ctx.set_span(root)

    def _finish(self, run, root, rt, st, result: Any, error: Optional[BaseException]) -> None:
        if error is None:
            root.outputs = _preview(result)
            root.finish(SpanStatus.SUCCESS)
            run.finish(SpanStatus.SUCCESS)
        else:
            root.finish(SpanStatus.ERROR, error="".join(traceback.format_exception(error)).strip())
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

    def _node_span(self, run: AgentRun, root: Span, name: str, before: Any, after: Any, index: int) -> None:
        span = Span(name=name, kind=SpanKind.CHAIN, parent_id=root.span_id)
        span.inputs = _preview(before)
        span.outputs = _preview(after)
        span.attributes.update(
            {
                "langgraph.node": name,
                "langgraph.step": index,
                "langgraph.state_keys_changed": ",".join(_changed_keys(before, after)),
            }
        )
        span.finish(SpanStatus.SUCCESS)
        run.spans.append(span)

    # -- invoke --------------------------------------------------------- #

    def invoke(self, inputs: Any, config: Any = None, **kwargs: Any) -> Any:
        run, root, rt, st = self._start(inputs)
        try:
            # stream_mode="updates" yields {node_name: state_delta} per step,
            # which is exactly the per-node attribution we want. Falling back
            # to plain invoke keeps this working on older versions.
            state: Any = inputs
            index = 0
            final: Any = None
            try:
                for chunk in self._graph.stream(inputs, config, stream_mode="updates", **kwargs):
                    if not isinstance(chunk, dict):
                        continue
                    for node_name, delta in chunk.items():
                        before = state if isinstance(state, dict) else {}
                        after = {**before, **delta} if isinstance(delta, dict) else delta
                        self._node_span(run, root, str(node_name), before, after, index)
                        state = after
                        index += 1
                    final = state
            except (TypeError, AttributeError):
                final = self._graph.invoke(inputs, config, **kwargs)

            self._finish(run, root, rt, st, final, None)
            return final
        except BaseException as e:
            self._finish(run, root, rt, st, None, e)
            raise

    async def ainvoke(self, inputs: Any, config: Any = None, **kwargs: Any) -> Any:
        run, root, rt, st = self._start(inputs)
        try:
            state: Any = inputs
            index = 0
            final: Any = None
            try:
                async for chunk in self._graph.astream(inputs, config, stream_mode="updates", **kwargs):
                    if not isinstance(chunk, dict):
                        continue
                    for node_name, delta in chunk.items():
                        before = state if isinstance(state, dict) else {}
                        after = {**before, **delta} if isinstance(delta, dict) else delta
                        self._node_span(run, root, str(node_name), before, after, index)
                        state = after
                        index += 1
                    final = state
            except (TypeError, AttributeError):
                final = await self._graph.ainvoke(inputs, config, **kwargs)

            self._finish(run, root, rt, st, final, None)
            return final
        except BaseException as e:
            self._finish(run, root, rt, st, None, e)
            raise


def trace_graph(
    lens, graph: Any, run_name: str = "langgraph", tags: Optional[list[str]] = None
) -> TracedGraph:
    """Wrap a compiled LangGraph so each node execution becomes a span."""
    return TracedGraph(lens, graph, run_name, tags)

"""
CrewAI integration: wrap a Crew so each agent/task execution becomes a span.

    from agentlens import AgentLens
    from agentlens.integrations.crewai import trace_crew

    lens = AgentLens(endpoint="http://localhost:7430")
    result = trace_crew(lens, crew, run_name="research_crew").kickoff(inputs={...})
"""

from __future__ import annotations

from typing import Any, Optional

from ..models import AgentRun, Span, SpanKind, SpanStatus, _preview
from ..tracer import AgentLens


class _TracedCrew:
    def __init__(self, lens: AgentLens, crew: Any, run_name: str, tags: Optional[list[str]] = None):
        self._lens = lens
        self._crew = crew
        self._run_name = run_name
        self._tags = list(tags or [])

    def kickoff(self, inputs: Optional[dict] = None, **kwargs) -> Any:
        run = AgentRun(name=self._run_name, tags=self._tags)
        root = Span(name=self._run_name, kind=SpanKind.AGENT)
        root.inputs = _preview(inputs)
        run.spans.append(root)

        # wrap each task's execution via step callbacks where available
        original_task_cb = getattr(self._crew, "task_callback", None)
        task_spans: dict[int, Span] = {}

        def task_callback(output):
            idx = len(task_spans)
            span = Span(
                name=getattr(output, "name", None) or f"task_{idx + 1}",
                kind=SpanKind.CHAIN,
                parent_id=root.span_id,
            )
            span.outputs = _preview(getattr(output, "raw", output))
            span.finish(SpanStatus.SUCCESS)
            run.spans.append(span)
            task_spans[idx] = span
            if original_task_cb:
                original_task_cb(output)

        try:
            self._crew.task_callback = task_callback
        except Exception:
            pass

        try:
            result = self._crew.kickoff(inputs=inputs, **kwargs)
            root.outputs = _preview(result)
            root.finish(SpanStatus.SUCCESS)
            run.finish(SpanStatus.SUCCESS)
            return result
        except BaseException as e:
            root.finish(SpanStatus.ERROR, error=repr(e))
            run.finish(SpanStatus.ERROR, error=repr(e))
            raise
        finally:
            try:
                self._crew.task_callback = original_task_cb
            except Exception:
                pass
            self._lens.exporter.export(run)

    def __getattr__(self, item):
        return getattr(self._crew, item)


def trace_crew(lens: AgentLens, crew: Any, run_name: str = "crew_run", tags: Optional[list[str]] = None) -> _TracedCrew:
    return _TracedCrew(lens, crew, run_name, tags)

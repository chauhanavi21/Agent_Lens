"""
LangChain integration: a BaseCallbackHandler that mirrors chain/tool/LLM
events into AgentLens spans.

    from agentlens import AgentLens
    from agentlens.integrations.langchain import AgentLensCallbackHandler

    lens = AgentLens(endpoint="http://localhost:7430")
    handler = AgentLensCallbackHandler(lens, run_name="my_chain")
    chain.invoke(inputs, config={"callbacks": [handler]})
    handler.end()   # finalize + export
"""

from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

from ..cost import estimate_cost
from ..models import AgentRun, LLMMetadata, Span, SpanKind, SpanStatus, _preview
from ..tracer import AgentLens

try:
    from langchain_core.callbacks import BaseCallbackHandler  # type: ignore
except ImportError:  # pragma: no cover

    class BaseCallbackHandler:  # minimal stand-in so import never fails
        pass


class AgentLensCallbackHandler(BaseCallbackHandler):
    def __init__(self, lens: AgentLens, run_name: str = "langchain_run", tags: Optional[list[str]] = None):
        self.lens = lens
        self.run = AgentRun(name=run_name, tags=list(tags or []))
        self._spans: dict[UUID, Span] = {}
        self._root = Span(name=run_name, kind=SpanKind.AGENT)
        self.run.spans.append(self._root)

    # -- helpers ------------------------------------------------------- #

    def _open(
        self, run_id: UUID, parent_run_id: Optional[UUID], name: str, kind: SpanKind, inputs: Any
    ) -> Span:
        parent = self._spans.get(parent_run_id) if parent_run_id else self._root
        span = Span(name=name, kind=kind, parent_id=(parent or self._root).span_id)
        span.inputs = _preview(inputs)
        self.run.spans.append(span)
        self._spans[run_id] = span
        return span

    def _close(
        self, run_id: UUID, outputs: Any = None, error: Optional[BaseException] = None
    ) -> Optional[Span]:
        span = self._spans.pop(run_id, None)
        if span is None:
            return None
        if error is not None:
            span.finish(SpanStatus.ERROR, error=repr(error))
        else:
            span.outputs = _preview(outputs)
            span.finish(SpanStatus.SUCCESS)
        return span

    # -- chains -------------------------------------------------------- #

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or "chain"
        self._open(run_id, parent_run_id, name, SpanKind.CHAIN, inputs)

    def on_chain_end(self, outputs, *, run_id, **kw):
        self._close(run_id, outputs)

    def on_chain_error(self, error, *, run_id, **kw):
        self._close(run_id, error=error)

    # -- tools --------------------------------------------------------- #

    def on_tool_start(self, serialized, input_str, *, run_id, parent_run_id=None, **kw):
        name = (serialized or {}).get("name") or "tool"
        self._open(run_id, parent_run_id, name, SpanKind.TOOL, input_str)

    def on_tool_end(self, output, *, run_id, **kw):
        self._close(run_id, output)

    def on_tool_error(self, error, *, run_id, **kw):
        self._close(run_id, error=error)

    # -- retrievers ---------------------------------------------------- #

    def on_retriever_start(self, serialized, query, *, run_id, parent_run_id=None, **kw):
        self._open(run_id, parent_run_id, "retriever", SpanKind.RETRIEVAL, query)

    def on_retriever_end(self, documents, *, run_id, **kw):
        self._close(run_id, f"{len(documents)} documents")

    def on_retriever_error(self, error, *, run_id, **kw):
        self._close(run_id, error=error)

    # -- LLMs ---------------------------------------------------------- #

    def on_llm_start(self, serialized, prompts, *, run_id, parent_run_id=None, **kw):
        span = self._open(run_id, parent_run_id, "llm", SpanKind.LLM, prompts)
        span.llm = LLMMetadata(prompt_preview=_preview(prompts))

    def on_llm_end(self, response, *, run_id, **kw):
        span = self._spans.get(run_id)
        if span is not None and span.llm is not None:
            usage = {}
            try:
                usage = (response.llm_output or {}).get("token_usage", {}) or {}
            except AttributeError:
                pass
            model = ""
            try:
                model = (response.llm_output or {}).get("model_name", "") or ""
            except AttributeError:
                pass
            span.llm.model = model
            span.llm.input_tokens = int(usage.get("prompt_tokens", 0))
            span.llm.output_tokens = int(usage.get("completion_tokens", 0))
            span.llm.cost_usd, span.llm.cost_source = estimate_cost(
                model, span.llm.input_tokens, span.llm.output_tokens, self.lens.cost_table
            )
            span.llm.response_preview = _preview(response)
        self._close(run_id, response)

    def on_llm_error(self, error, *, run_id, **kw):
        self._close(run_id, error=error)

    # -- finalize ------------------------------------------------------ #

    def end(self, status: SpanStatus = SpanStatus.SUCCESS) -> None:
        for span in list(self._spans.values()):
            span.finish(SpanStatus.CANCELLED)
        self._spans.clear()
        self._root.finish(status)
        self.run.finish(status)
        self.lens.exporter.export(self.run)

"""
Core data models for AgentLens spans and runs.
Designed to be OTel-compatible but agent-aware.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class SpanStatus(str, Enum):
    RUNNING = "running"
    SUCCESS = "success"
    ERROR = "error"
    CANCELLED = "cancelled"
    PAUSED = "paused"  # budget guard tripped


class SpanKind(str, Enum):
    AGENT = "agent"          # top-level agent entry
    TOOL = "tool"            # tool/function call
    LLM = "llm"              # raw LLM call
    CHAIN = "chain"          # sub-chain or sub-agent
    RETRIEVAL = "retrieval"  # RAG retrieval step
    MCP = "mcp"              # tool call over the Model Context Protocol
    CUSTOM = "custom"


@dataclass
class LLMMetadata:
    """Captured metadata for LLM calls within a span."""

    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    prompt_preview: str = ""     # first 500 chars of prompt
    response_preview: str = ""   # first 500 chars of response
    temperature: Optional[float] = None

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "provider": self.provider,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "prompt_preview": self.prompt_preview,
            "response_preview": self.response_preview,
            "temperature": self.temperature,
        }


def _preview(value: Any, limit: int = 500) -> str:
    try:
        s = repr(value)
    except Exception:
        s = "<unrepresentable>"
    return s if len(s) <= limit else s[:limit] + "…"


@dataclass
class Span:
    """A single unit of work within an agent run (one DAG node)."""

    name: str
    kind: SpanKind = SpanKind.CUSTOM
    span_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    parent_id: Optional[str] = None
    status: SpanStatus = SpanStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    inputs: str = ""
    outputs: str = ""
    error: Optional[str] = None
    retry_of: Optional[str] = None   # span_id of the attempt this retries
    # Set on a span whose parent lives in another process (an MCP server
    # executing a tool the agent called). The server stitches on this.
    remote_parent_id: Optional[str] = None
    service: Optional[str] = None    # which process recorded this span
    llm: Optional[LLMMetadata] = None
    attributes: dict[str, Any] = field(default_factory=dict)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at) * 1000, 2)

    def finish(self, status: SpanStatus = SpanStatus.SUCCESS, error: Optional[str] = None) -> None:
        self.ended_at = time.time()
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "parent_id": self.parent_id,
            "name": self.name,
            "kind": self.kind.value,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "inputs": self.inputs,
            "outputs": self.outputs,
            "error": self.error,
            "retry_of": self.retry_of,
            "remote_parent_id": self.remote_parent_id,
            "service": self.service,
            "llm": self.llm.to_dict() if self.llm else None,
            "attributes": self.attributes,
        }


@dataclass
class AgentRun:
    """A complete agent execution: a tree of spans plus run-level rollups."""

    name: str
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    # W3C trace id, shared with every process participating in this run
    trace_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tags: list[str] = field(default_factory=list)
    status: SpanStatus = SpanStatus.RUNNING
    started_at: float = field(default_factory=time.time)
    ended_at: Optional[float] = None
    spans: list[Span] = field(default_factory=list)
    error: Optional[str] = None
    max_total_tokens: Optional[int] = None
    max_cost_usd: Optional[float] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    scores: list = field(default_factory=list)

    @property
    def duration_ms(self) -> Optional[float]:
        if self.ended_at is None:
            return None
        return round((self.ended_at - self.started_at) * 1000, 2)

    @property
    def total_tokens(self) -> int:
        return sum(s.llm.total_tokens for s in self.spans if s.llm)

    @property
    def total_cost_usd(self) -> float:
        return round(sum(s.llm.cost_usd for s in self.spans if s.llm), 6)

    def over_budget(self) -> Optional[str]:
        """Return a human-readable reason if a budget guard has tripped."""
        if self.max_total_tokens is not None and self.total_tokens > self.max_total_tokens:
            return f"token budget exceeded: {self.total_tokens} > {self.max_total_tokens}"
        if self.max_cost_usd is not None and self.total_cost_usd > self.max_cost_usd:
            return f"cost budget exceeded: ${self.total_cost_usd:.4f} > ${self.max_cost_usd:.4f}"
        return None

    def finish(self, status: SpanStatus = SpanStatus.SUCCESS, error: Optional[str] = None) -> None:
        self.ended_at = time.time()
        self.status = status
        if error:
            self.error = error

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "trace_id": self.trace_id,
            "name": self.name,
            "tags": self.tags,
            "status": self.status.value,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "duration_ms": self.duration_ms,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "error": self.error,
            "metadata": self.metadata,
            "scores": [s.to_dict() for s in self.scores],
            "spans": [s.to_dict() for s in self.spans],
        }

"""Pydantic schemas mirroring the SDK's wire format."""

from typing import Any, Optional

from pydantic import BaseModel, Field


class LLMMetadataIn(BaseModel):
    model: str = ""
    provider: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    prompt_preview: str = ""
    response_preview: str = ""
    temperature: Optional[float] = None


class SpanIn(BaseModel):
    span_id: str
    parent_id: Optional[str] = None
    name: str
    kind: str = "custom"
    status: str = "success"
    started_at: float
    ended_at: Optional[float] = None
    duration_ms: Optional[float] = None
    inputs: str = ""
    outputs: str = ""
    error: Optional[str] = None
    retry_of: Optional[str] = None
    remote_parent_id: Optional[str] = None
    service: Optional[str] = None
    llm: Optional[LLMMetadataIn] = None
    attributes: dict[str, Any] = Field(default_factory=dict)


class ScoreIn(BaseModel):
    name: str
    value: float
    source: str = "custom"
    threshold: Optional[float] = None
    passed: Optional[bool] = None
    comment: str = ""
    span_id: Optional[str] = None
    recorded_at: float = 0.0


class ScoresIn(BaseModel):
    """Late-arriving scores from an eval harness, keyed by run."""

    run_id: str
    scores: list[ScoreIn]


class RunIn(BaseModel):
    run_id: str
    trace_id: Optional[str] = None
    name: str
    tags: list[str] = Field(default_factory=list)
    status: str
    started_at: float
    ended_at: Optional[float] = None
    duration_ms: Optional[float] = None
    total_tokens: int = 0
    total_cost_usd: float = 0.0
    error: Optional[str] = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    scores: list[ScoreIn] = Field(default_factory=list)
    spans: list[SpanIn] = Field(default_factory=list)


class RunSummary(BaseModel):
    run_id: str
    name: str
    status: str
    tags: list[str]
    started_at: float
    duration_ms: Optional[float]
    total_tokens: int
    total_cost_usd: float
    span_count: int
    scores: list[ScoreIn] = Field(default_factory=list)
    error: Optional[str] = None


class DiffRequest(BaseModel):
    run_a: str
    run_b: str


class AlertRuleIn(BaseModel):
    name: str
    field: str
    op: str
    value: str
    webhook_url: str
    run_name: Optional[str] = None
    enabled: bool = True


class AlertRuleOut(AlertRuleIn):
    id: str
    created_at: float


class AlertEventOut(BaseModel):
    id: str
    rule_id: str
    rule_name: str
    run_id: str
    run_name: str
    reason: str
    delivered: bool
    delivery_error: Optional[str] = None
    fired_at: float

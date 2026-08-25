"""SQLAlchemy models. Spans live as JSONB on the run row: agent DAGs are
read whole, so one row per run with a GIN index beats a spans table."""

from sqlalchemy import Boolean, Float, Index, Integer, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sqlalchemy.types import JSON


class Base(DeclarativeBase):
    # JSONB on Postgres, plain JSON elsewhere (SQLite dev mode)
    type_annotation_map = {
        dict: JSON().with_variant(JSONB, "postgresql"),
        list: JSON().with_variant(JSONB, "postgresql"),
    }


class RunRow(Base):
    __tablename__ = "runs"

    run_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), index=True, nullable=True)
    name: Mapped[str] = mapped_column(String(255), index=True)
    status: Mapped[str] = mapped_column(String(16), index=True)
    tags: Mapped[list] = mapped_column(default=list)
    started_at: Mapped[float] = mapped_column(Float, index=True)
    ended_at: Mapped[float] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[float] = mapped_column(Float, nullable=True)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0)
    total_cost_usd: Mapped[float] = mapped_column(Float, default=0.0)
    error: Mapped[str] = mapped_column(Text, nullable=True)
    meta: Mapped[dict] = mapped_column(default=dict)
    is_remote: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    scores: Mapped[list] = mapped_column(default=list)
    spans: Mapped[list] = mapped_column(default=list)


Index("ix_runs_spans_gin", RunRow.spans, postgresql_using="gin")


class AlertRuleRow(Base):
    __tablename__ = "alert_rules"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    run_name: Mapped[str] = mapped_column(String(255), nullable=True)  # optional scope
    field: Mapped[str] = mapped_column(String(64))
    op: Mapped[str] = mapped_column(String(16))
    value: Mapped[str] = mapped_column(String(255))
    webhook_url: Mapped[str] = mapped_column(Text)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[float] = mapped_column(Float)


class AlertEventRow(Base):
    __tablename__ = "alert_events"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    rule_id: Mapped[str] = mapped_column(String(64), index=True)
    rule_name: Mapped[str] = mapped_column(String(255))
    run_id: Mapped[str] = mapped_column(String(64), index=True)
    run_name: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text)
    delivered: Mapped[bool] = mapped_column(Boolean, default=False)
    delivery_error: Mapped[str] = mapped_column(Text, nullable=True)
    fired_at: Mapped[float] = mapped_column(Float, index=True)

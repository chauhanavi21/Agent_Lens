from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..analytics import (
    PERCENTILE_SAMPLE_CAP,
    find_outliers,
    summarize,
    window_start,
)
from ..db import get_session
from ..indexing import index_is_stale, rebuild_index
from ..models import SpanIndexRow

router = APIRouter(tags=["analytics"])


def _row_dict(row) -> dict:
    return {
        "span_id": row.span_id,
        "run_id": row.run_id,
        "run_name": row.run_name,
        "name": row.name,
        "kind": row.kind,
        "status": row.status,
        "started_at": row.started_at,
        "duration_ms": row.duration_ms,
        "total_tokens": row.total_tokens,
        "cost_usd": row.cost_usd,
        "model": row.model,
        "service": row.service,
        "is_retry": row.is_retry,
    }


async def _fetch(
    session: AsyncSession,
    *,
    days: Optional[float],
    agent: Optional[str],
    kind: Optional[str],
    cap: int = PERCENTILE_SAMPLE_CAP,
) -> tuple[list[dict], bool]:
    stmt = select(SpanIndexRow).order_by(SpanIndexRow.started_at.desc()).limit(cap + 1)
    since = window_start(days)
    if since is not None:
        stmt = stmt.where(SpanIndexRow.started_at >= since)
    if agent:
        stmt = stmt.where(SpanIndexRow.run_name == agent)
    if kind:
        stmt = stmt.where(SpanIndexRow.kind == kind)

    rows = (await session.execute(stmt)).scalars().all()
    capped = len(rows) > cap
    return [_row_dict(r) for r in rows[:cap]], capped


@router.get("/analytics/spans")
async def span_stats(
    session: AsyncSession = Depends(get_session),
    days: Optional[float] = Query(default=7, description="Window in days; omit for all time"),
    agent: Optional[str] = Query(default=None, description="Only this agent's runs"),
    kind: Optional[str] = Query(default=None, description="tool, llm, retrieval, mcp, …"),
):
    """
    Per-step statistics across runs: call counts, error and retry rates,
    p50/p95/p99 latency, tokens, and cost.

    This is the question a single run's DAG can't answer — "is `web_search`
    always this slow, or was that run unlucky?"
    """
    rows, capped = await _fetch(session, days=days, agent=agent, kind=kind)
    stats = summarize(rows, group_by="name", sample_capped=capped)
    return {
        "window_days": days,
        "spans_examined": len(rows),
        "sample_capped": capped,
        "note": (
            f"showing the most recent {PERCENTILE_SAMPLE_CAP:,} spans; "
            "narrow the window or filter by agent for exact percentiles"
        )
        if capped
        else None,
        "stats": stats,
    }


@router.get("/analytics/models")
async def model_stats(
    session: AsyncSession = Depends(get_session),
    days: Optional[float] = Query(default=30),
    agent: Optional[str] = Query(default=None),
):
    """Cost and token usage grouped by model — where the money goes."""
    rows, capped = await _fetch(session, days=days, agent=agent, kind="llm")
    stats = [s for s in summarize(rows, group_by="model", sample_capped=capped) if s.get("model")]
    return {
        "window_days": days,
        "total_cost_usd": round(sum(s["total_cost_usd"] for s in stats), 6),
        "total_tokens": sum(s["total_tokens"] for s in stats),
        "sample_capped": capped,
        "stats": sorted(stats, key=lambda s: s["total_cost_usd"], reverse=True),
    }


@router.get("/analytics/outliers")
async def outliers(
    session: AsyncSession = Depends(get_session),
    days: Optional[float] = Query(default=7),
    agent: Optional[str] = Query(default=None),
    limit: int = Query(default=20, le=100),
):
    """
    Individual spans that ran far past their own p95, worst first.

    Aggregates say a step is slow; this says which run to open.
    """
    rows, capped = await _fetch(session, days=days, agent=agent, kind=None)
    stats = summarize(rows, group_by="name", sample_capped=capped)
    return {"window_days": days, "outliers": find_outliers(rows, stats, limit=limit)}


@router.get("/analytics/health")
async def index_health(session: AsyncSession = Depends(get_session)):
    """Is the derived index populated and roughly in step with the runs?"""
    indexed = (await session.execute(select(func.count(SpanIndexRow.span_id)))).scalar() or 0
    return {
        "indexed_spans": indexed,
        "stale": await index_is_stale(session),
        "hint": "POST /api/analytics/reindex to rebuild from the runs table",
    }


@router.post("/analytics/reindex")
async def reindex(session: AsyncSession = Depends(get_session)):
    """
    Rebuild the index from the runs table.

    Safe to run at any time: the index is derived, so the worst case of
    rebuilding is wasted work, never lost data.
    """
    result = await rebuild_index(session)
    await session.commit()
    return {"rebuilt": True, **result}

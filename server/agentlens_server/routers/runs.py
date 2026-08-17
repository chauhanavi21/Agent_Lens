from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..diff import diff_runs
from ..models import RunRow
from ..stitching import is_child_run, stitch
from ..schemas import DiffRequest, RunSummary

router = APIRouter(tags=["runs"])


def _to_dict(row: RunRow) -> dict:
    return {
        "run_id": row.run_id, "trace_id": row.trace_id, "name": row.name, "status": row.status,
        "tags": row.tags or [], "started_at": row.started_at, "ended_at": row.ended_at,
        "duration_ms": row.duration_ms, "total_tokens": row.total_tokens,
        "total_cost_usd": row.total_cost_usd, "error": row.error,
        "metadata": row.meta or {}, "scores": row.scores or [], "spans": row.spans or [],
    }


@router.get("/runs", response_model=list[RunSummary])
async def list_runs(
    session: AsyncSession = Depends(get_session),
    status: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None),
    tag: Optional[str] = Query(default=None),
    limit: int = Query(default=50, le=200),
    offset: int = Query(default=0, ge=0),
    include_remote: bool = Query(default=False, description="Include MCP server / sub-agent runs"),
):
    stmt = select(RunRow).order_by(desc(RunRow.started_at)).limit(limit).offset(offset)
    # remote continuations show inside their caller's DAG, not as separate
    # top-level runs — unless asked for explicitly
    if not include_remote:
        stmt = stmt.where(RunRow.is_remote == False)  # noqa: E712
    if status:
        stmt = stmt.where(RunRow.status == status)
    if name:
        stmt = stmt.where(RunRow.name.ilike(f"%{name}%"))
    rows = (await session.execute(stmt)).scalars().all()
    out = []
    for r in rows:
        if tag and tag not in (r.tags or []):
            continue
        out.append(RunSummary(
            run_id=r.run_id, name=r.name, status=r.status, tags=r.tags or [],
            started_at=r.started_at, duration_ms=r.duration_ms,
            total_tokens=r.total_tokens, total_cost_usd=r.total_cost_usd,
            span_count=len(r.spans or []), scores=r.scores or [], error=r.error,
        ))
    return out


@router.get("/runs/stats")
async def stats(session: AsyncSession = Depends(get_session)):
    total = (await session.execute(select(func.count(RunRow.run_id)))).scalar() or 0
    by_status_rows = (await session.execute(
        select(RunRow.status, func.count(RunRow.run_id)).group_by(RunRow.status)
    )).all()
    cost = (await session.execute(select(func.coalesce(func.sum(RunRow.total_cost_usd), 0.0)))).scalar()
    tokens = (await session.execute(select(func.coalesce(func.sum(RunRow.total_tokens), 0)))).scalar()
    return {
        "total_runs": total,
        "by_status": {s: c for s, c in by_status_rows},
        "total_cost_usd": round(float(cost), 4),
        "total_tokens": int(tokens),
    }


@router.get("/runs/scores")
async def score_trends(
    session: AsyncSession = Depends(get_session),
    name: Optional[str] = Query(default=None, description="Filter to one agent"),
    limit: int = Query(default=100, le=500),
):
    """Score history per metric, oldest first — the shape of quality over time."""
    stmt = select(RunRow).order_by(desc(RunRow.started_at)).limit(limit)
    if name:
        stmt = stmt.where(RunRow.name == name)
    rows = (await session.execute(stmt)).scalars().all()
    series: dict[str, list] = {}
    for r in reversed(rows):
        for s in r.scores or []:
            series.setdefault(s["name"], []).append({
                "run_id": r.run_id, "started_at": r.started_at,
                "value": s["value"], "passed": s.get("passed"),
            })
    return {
        "metrics": sorted(series),
        "series": series,
        "latest": {k: v[-1]["value"] for k, v in series.items() if v},
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _to_dict(row)

    # graft in any remote runs sharing this trace (MCP servers, sub-agents)
    if row.trace_id:
        siblings = (await session.execute(
            select(RunRow).where(RunRow.trace_id == row.trace_id, RunRow.run_id != row.run_id)
        )).scalars().all()
        children = [_to_dict(r) for r in siblings]
        children = [c for c in children if is_child_run(c)]
        if children:
            run = stitch(run, children)
    return run


@router.post("/runs/diff")
async def diff(req: DiffRequest, session: AsyncSession = Depends(get_session)):
    a = await session.get(RunRow, req.run_a)
    b = await session.get(RunRow, req.run_b)
    missing = [rid for rid, row in ((req.run_a, a), (req.run_b, b)) if row is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Run(s) not found: {', '.join(missing)}")
    return diff_runs(_to_dict(a), _to_dict(b))

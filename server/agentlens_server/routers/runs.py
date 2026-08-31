from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete as sql_delete
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..diff import diff_runs
from ..models import RunRow
from ..retention import (
    RetentionPolicy,
    RunRef,
    expand_to_traces,
    select_for_pruning,
)
from ..schemas import DiffRequest, PruneRequest, PruneResult, RunPage, RunSummary
from ..stitching import is_child_run, stitch

router = APIRouter(tags=["runs"])


def _summary(row: RunRow) -> RunSummary:
    return RunSummary(
        run_id=row.run_id,
        name=row.name,
        status=row.status,
        tags=row.tags or [],
        started_at=row.started_at,
        duration_ms=row.duration_ms,
        total_tokens=row.total_tokens,
        total_cost_usd=row.total_cost_usd,
        span_count=len(row.spans or []),
        scores=row.scores or [],
        error=row.error,
    )


def _to_dict(row: RunRow) -> dict:
    return {
        "run_id": row.run_id,
        "trace_id": row.trace_id,
        "name": row.name,
        "status": row.status,
        "tags": row.tags or [],
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "duration_ms": row.duration_ms,
        "total_tokens": row.total_tokens,
        "total_cost_usd": row.total_cost_usd,
        "error": row.error,
        "metadata": row.meta or {},
        "scores": row.scores or [],
        "spans": row.spans or [],
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
        out.append(
            RunSummary(
                run_id=r.run_id,
                name=r.name,
                status=r.status,
                tags=r.tags or [],
                started_at=r.started_at,
                duration_ms=r.duration_ms,
                total_tokens=r.total_tokens,
                total_cost_usd=r.total_cost_usd,
                span_count=len(r.spans or []),
                scores=r.scores or [],
                error=r.error,
            )
        )
    return out


@router.get("/runs/page", response_model=RunPage)
async def page_runs(
    session: AsyncSession = Depends(get_session),
    cursor: Optional[float] = Query(default=None, description="started_at of the last run seen"),
    limit: int = Query(default=50, le=200),
    status: Optional[str] = Query(default=None),
    name: Optional[str] = Query(default=None),
    include_remote: bool = Query(default=False),
):
    """
    Cursor pagination, for scrolling a long history.

    Offset pagination drifts under a live workload: new runs arrive at the
    head while you page, so `offset=50` skips different rows each time and
    you see duplicates. A cursor on `started_at` is stable regardless of
    what lands after you started.
    """
    stmt = select(RunRow).order_by(desc(RunRow.started_at)).limit(limit + 1)
    if not include_remote:
        stmt = stmt.where(RunRow.is_remote == False)  # noqa: E712
    if cursor is not None:
        stmt = stmt.where(RunRow.started_at < cursor)
    if status:
        stmt = stmt.where(RunRow.status == status)
    if name:
        stmt = stmt.where(RunRow.name.ilike(f"%{name}%"))

    rows = (await session.execute(stmt)).scalars().all()
    has_more = len(rows) > limit
    rows = rows[:limit]

    return RunPage(
        runs=[_summary(r) for r in rows],
        next_cursor=rows[-1].started_at if rows and has_more else None,
        has_more=has_more,
    )


@router.get("/runs/stats")
async def stats(session: AsyncSession = Depends(get_session)):
    total = (await session.execute(select(func.count(RunRow.run_id)))).scalar() or 0
    by_status_rows = (
        await session.execute(select(RunRow.status, func.count(RunRow.run_id)).group_by(RunRow.status))
    ).all()
    cost = (await session.execute(select(func.coalesce(func.sum(RunRow.total_cost_usd), 0.0)))).scalar()
    tokens = (await session.execute(select(func.coalesce(func.sum(RunRow.total_tokens), 0)))).scalar()
    return {
        "total_runs": total,
        "by_status": dict(by_status_rows),
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
            series.setdefault(s["name"], []).append(
                {
                    "run_id": r.run_id,
                    "started_at": r.started_at,
                    "value": s["value"],
                    "passed": s.get("passed"),
                }
            )
    return {
        "metrics": sorted(series),
        "series": series,
        "latest": {k: v[-1]["value"] for k, v in series.items() if v},
    }


@router.get("/runs/{run_id}")
async def get_run(run_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(RunRow, run_id)
    if row is None:
        # still executing: serve the in-memory shape so the UI can open a
        # run before it has finished
        from ..live import live_store

        partial = live_store.get(run_id)
        if partial is not None:
            return {
                **partial,
                "live": True,
                "scores": partial.get("scores", []),
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "error": None,
                "metadata": {"live": True},
            }
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")
    run = _to_dict(row)

    # graft in any remote runs sharing this trace (MCP servers, sub-agents)
    if row.trace_id:
        siblings = (
            (
                await session.execute(
                    select(RunRow).where(RunRow.trace_id == row.trace_id, RunRow.run_id != row.run_id)
                )
            )
            .scalars()
            .all()
        )
        children = [_to_dict(r) for r in siblings]
        children = [c for c in children if is_child_run(c)]
        if children:
            run = stitch(run, children)
    return run


@router.get("/runs/{run_id}/cassette")
async def cassette(run_id: str, session: AsyncSession = Depends(get_session)):
    """
    A replay-ready recording of this run's side effects: tool, LLM,
    retrieval, and MCP outputs keyed by call order. Save it as a fixture and
    a production failure becomes a deterministic test that touches no live
    API.

    Runs traced without `record_outputs=True` still produce a cassette, but
    from truncated previews — usable for shape assertions, not for feeding
    real objects back into the agent. The `truncated` flag says which.
    """
    row = await session.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    from agentlens.replay import Cassette

    return Cassette.from_run(_to_dict(row)).to_dict()


async def _all_refs(session: AsyncSession, name: Optional[str] = None) -> list[RunRef]:
    """Load just the columns pruning needs — never the span payloads."""
    stmt = select(
        RunRow.run_id,
        RunRow.name,
        RunRow.started_at,
        RunRow.tags,
        RunRow.trace_id,
        RunRow.is_remote,
    )
    if name:
        stmt = stmt.where(RunRow.name == name)
    rows = (await session.execute(stmt)).all()
    return [
        RunRef(
            run_id=r.run_id,
            name=r.name,
            started_at=r.started_at or 0.0,
            tags=list(r.tags or []),
            trace_id=r.trace_id,
            is_remote=bool(r.is_remote),
        )
        for r in rows
    ]


@router.delete("/runs/{run_id}", status_code=200)
async def delete_run(
    run_id: str,
    session: AsyncSession = Depends(get_session),
    cascade: bool = Query(default=True, description="Also delete remote runs in this trace"),
):
    """
    Delete one run.

    By default this follows the trace: an MCP server's run is only meaningful
    stitched into its caller, and leaving it behind produces an orphan nobody
    recognizes. Deleting a *server* run never removes the agent run that
    called it — that direction would let cleaning up a tool server destroy
    the agent traces referencing it.
    """
    row = await session.get(RunRow, run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{run_id}' not found.")

    doomed = {run_id}
    cascaded: dict[str, str] = {}
    if cascade and row.trace_id and not row.is_remote:
        doomed, cascaded = expand_to_traces([run_id], await _all_refs(session))

    await session.execute(sql_delete(RunRow).where(RunRow.run_id.in_(doomed)))
    await session.commit()
    return {"deleted": sorted(doomed), "cascaded": sorted(cascaded)}


@router.post("/runs/prune", response_model=PruneResult)
async def prune_runs(
    req: PruneRequest,
    session: AsyncSession = Depends(get_session),
):
    """
    Apply a retention policy on demand.

    `dry_run` defaults to True — deletion is irreversible, so the safe path
    has to be what you get by forgetting a parameter. Every selected run
    comes back with the reason it was chosen.
    """
    policy = RetentionPolicy(
        max_age_days=req.older_than_days,
        max_runs_per_agent=req.max_runs_per_agent,
        protect_tags=frozenset(req.protect_tags or []),
    )
    if not policy.active:
        raise HTTPException(
            status_code=422,
            detail="Set older_than_days or max_runs_per_agent — a prune with no rule would be a no-op.",
        )

    refs = await _all_refs(session, req.name)
    # remote runs are pruned by following their caller, not on their own
    selection = select_for_pruning([r for r in refs if not r.is_remote], policy)
    doomed, cascaded = expand_to_traces(selection["run_ids"], refs)

    reasons = dict(selection["reasons"])
    reasons.update(cascaded)

    deleted = 0
    if not req.dry_run and doomed:
        await session.execute(sql_delete(RunRow).where(RunRow.run_id.in_(doomed)))
        await session.commit()
        deleted = len(doomed)

    verb = "Would delete" if req.dry_run else "Deleted"
    summary = (
        f"{verb} {len(doomed)} run(s)"
        + (f", including {len(cascaded)} remote continuation(s)" if cascaded else "")
        + f". {selection['protected']} protected by tag, {selection['kept']} kept."
        + (" Re-run with dry_run=false to apply." if req.dry_run and doomed else "")
    )

    return PruneResult(
        dry_run=req.dry_run,
        deleted=deleted,
        would_delete=len(doomed),
        protected=selection["protected"],
        kept=selection["kept"],
        cascaded=len(cascaded),
        reasons=reasons,
        summary=summary,
    )


@router.post("/runs/diff")
async def diff(req: DiffRequest, session: AsyncSession = Depends(get_session)):
    a = await session.get(RunRow, req.run_a)
    b = await session.get(RunRow, req.run_b)
    missing = [rid for rid, row in ((req.run_a, a), (req.run_b, b)) if row is None]
    if missing:
        raise HTTPException(status_code=404, detail=f"Run(s) not found: {', '.join(missing)}")
    return diff_runs(_to_dict(a), _to_dict(b))

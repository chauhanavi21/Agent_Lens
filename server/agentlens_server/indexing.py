"""
Keeping the derived span index in step with the runs it mirrors.

Writes go through here rather than being inlined at each call site, because
there are four places a run's spans change — ingest, OTLP ingest, late
scores, deletion — and an index that silently misses one of them produces
analytics that are wrong in a way nobody notices.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete as sql_delete
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .analytics import span_rows_for_run
from .models import RunRow, SpanIndexRow


async def reindex_run(session: AsyncSession, run: dict[str, Any]) -> int:
    """
    Replace this run's index rows.

    Delete-then-insert rather than upsert: a re-ingested run can have *fewer*
    spans than before (a shorter retry chain, a truncated export), and an
    upsert would leave the extras behind forever.
    """
    run_id = run.get("run_id")
    if not run_id:
        return 0

    await session.execute(sql_delete(SpanIndexRow).where(SpanIndexRow.run_id == run_id))
    rows = span_rows_for_run(run)
    for row in rows:
        if row.get("span_id"):
            session.add(SpanIndexRow(**row))
    return len(rows)


async def deindex_runs(session: AsyncSession, run_ids: list[str]) -> int:
    """Drop index rows for deleted runs, so analytics can't count ghosts."""
    if not run_ids:
        return 0
    result = await session.execute(sql_delete(SpanIndexRow).where(SpanIndexRow.run_id.in_(run_ids)))
    return result.rowcount or 0


async def rebuild_index(session: AsyncSession, batch_size: int = 200) -> dict[str, int]:
    """
    Rebuild the whole index from the runs table.

    Needed after upgrading from a version without the index, and useful as
    an escape hatch if it ever drifts — which is the advantage of derived
    data over a schema migration.
    """
    await session.execute(sql_delete(SpanIndexRow))

    runs = 0
    spans = 0
    offset = 0
    while True:
        batch = (await session.execute(select(RunRow).limit(batch_size).offset(offset))).scalars().all()
        if not batch:
            break
        for row in batch:
            run = {"run_id": row.run_id, "name": row.name, "spans": row.spans or []}
            for indexed in span_rows_for_run(run):
                if indexed.get("span_id"):
                    session.add(SpanIndexRow(**indexed))
                    spans += 1
            runs += 1
        await session.commit()
        offset += batch_size

    return {"runs": runs, "spans": spans}


async def index_is_stale(session: AsyncSession) -> bool:
    """
    A cheap check for the one case that matters: runs exist but the index is
    empty, which is what an upgrade from an older version looks like.
    """
    from sqlalchemy import func

    runs = (await session.execute(select(func.count(RunRow.run_id)))).scalar() or 0
    indexed = (await session.execute(select(func.count(SpanIndexRow.span_id)))).scalar() or 0
    return runs > 0 and indexed == 0

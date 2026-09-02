"""AgentLens server entrypoint."""

import asyncio
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import __version__
from .config import CORS_ORIGINS, RETENTION_POLICY, RETENTION_SWEEP_HOURS
from .db import SessionLocal, init_db
from .routers import alerts, analytics, evals, ingest, runs, stream

log = logging.getLogger("agentlens")


async def _retention_sweep() -> None:
    """
    Apply the configured retention policy on a timer.

    Deliberately dumb: no locking, no coordination. With several server
    replicas each one sweeps, and the loser of a race deletes nothing
    because the rows are already gone. Failures are logged and retried on
    the next tick rather than taking the process down — losing a sweep is
    survivable, losing the server is not.
    """
    from sqlalchemy import delete as sql_delete
    from sqlalchemy import select

    from .models import RunRow
    from .retention import RunRef, expand_to_traces, select_for_pruning

    # A one-second floor keeps a typo from spinning the loop, without
    # making the sweep untestable.
    interval = max(RETENTION_SWEEP_HOURS * 3600, 1.0)

    # Sweep shortly after startup rather than waiting a full interval: a
    # server that restarts every few hours would otherwise never reach its
    # first sweep, and the data it was configured to drop would accumulate
    # forever. The brief delay keeps startup responsive.
    await asyncio.sleep(min(interval, 2.0))

    while True:
        try:
            async with SessionLocal() as session:
                rows = (
                    await session.execute(
                        select(
                            RunRow.run_id,
                            RunRow.name,
                            RunRow.started_at,
                            RunRow.tags,
                            RunRow.trace_id,
                            RunRow.is_remote,
                        )
                    )
                ).all()
                refs = [
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
                selection = select_for_pruning([r for r in refs if not r.is_remote], RETENTION_POLICY)
                doomed, _cascaded = expand_to_traces(selection["run_ids"], refs)
                if doomed:
                    from .indexing import deindex_runs

                    await session.execute(sql_delete(RunRow).where(RunRow.run_id.in_(doomed)))
                    await deindex_runs(session, sorted(doomed))
                    await session.commit()
                    log.info("retention sweep removed %d run(s)", len(doomed))
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("retention sweep failed; will retry next interval")

        await asyncio.sleep(interval)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()

    sweeper = None
    if RETENTION_POLICY.active:
        log.info("retention: %s", RETENTION_POLICY.describe())
        sweeper = asyncio.create_task(_retention_sweep())

    try:
        yield
    finally:
        if sweeper is not None:
            sweeper.cancel()
            try:
                await sweeper
            except asyncio.CancelledError:
                pass


# version comes from the package rather than a literal, so the docs
# page can't drift from what was actually released
app = FastAPI(title="AgentLens", version=__version__, lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(ingest.router, prefix="/api")
app.include_router(runs.router, prefix="/api")
app.include_router(alerts.router, prefix="/api")
app.include_router(stream.router, prefix="/api")
app.include_router(evals.router, prefix="/api")
app.include_router(analytics.router, prefix="/api")


@app.get("/api/health")
async def health():
    return {"status": "ok"}

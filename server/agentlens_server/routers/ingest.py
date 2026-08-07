from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import API_KEY
from ..db import get_session
from ..models import RunRow
from ..schemas import RunIn

router = APIRouter(tags=["ingest"])


def _check_auth(authorization: str | None) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


@router.post("/ingest/run", status_code=201)
async def ingest_run(
    run: RunIn,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_auth(authorization)
    row = await session.get(RunRow, run.run_id)
    payload = dict(
        name=run.name,
        status=run.status,
        tags=run.tags,
        started_at=run.started_at,
        ended_at=run.ended_at,
        duration_ms=run.duration_ms,
        total_tokens=run.total_tokens,
        total_cost_usd=run.total_cost_usd,
        error=run.error,
        meta=run.metadata,
        spans=[s.model_dump() for s in run.spans],
    )
    if row is None:
        session.add(RunRow(run_id=run.run_id, **payload))
    else:  # idempotent re-ingest / streaming update
        for k, v in payload.items():
            setattr(row, k, v)
    await session.commit()
    return {"run_id": run.run_id, "spans": len(run.spans)}

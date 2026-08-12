import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts import _describe, build_payload, dispatch, rule_matches
from ..config import API_KEY
from ..db import SessionLocal, get_session
from ..models import AlertEventRow, AlertRuleRow, RunRow
from ..schemas import RunIn

router = APIRouter(tags=["ingest"])


def _check_auth(authorization: str | None) -> None:
    if not API_KEY:
        return
    if authorization != f"Bearer {API_KEY}":
        raise HTTPException(status_code=401, detail="Invalid or missing API key.")


async def evaluate_alerts(run: dict) -> list[dict]:
    """
    Run every enabled rule against a finished run and dispatch webhooks.
    Runs as a background task so ingest stays fast and a slow or broken
    webhook can never delay or fail the agent's export.
    """
    fired = []
    async with SessionLocal() as session:
        rules = (await session.execute(
            select(AlertRuleRow).where(AlertRuleRow.enabled == True)  # noqa: E712
        )).scalars().all()
        for r in rules:
            rule = {"name": r.name, "field": r.field, "op": r.op, "value": r.value, "run_name": r.run_name}
            try:
                if not rule_matches(rule, run):
                    continue
            except Exception:
                continue  # a malformed rule must not break ingest
            ok, err = dispatch(r.webhook_url, build_payload(rule, run))
            event = AlertEventRow(
                id=uuid.uuid4().hex[:16], rule_id=r.id, rule_name=r.name,
                run_id=run["run_id"], run_name=run["name"], reason=_describe(rule, run),
                delivered=ok, delivery_error=err, fired_at=time.time(),
            )
            session.add(event)
            fired.append({"rule": r.name, "delivered": ok})
        await session.commit()
    return fired


@router.post("/ingest/run", status_code=201)
async def ingest_run(
    run: RunIn,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    _check_auth(authorization)
    row = await session.get(RunRow, run.run_id)
    spans = [s.model_dump() for s in run.spans]
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
        spans=spans,
    )
    if row is None:
        session.add(RunRow(run_id=run.run_id, **payload))
    else:  # idempotent re-ingest / streaming update
        for k, v in payload.items():
            setattr(row, k, v)
    await session.commit()

    # only alert on terminal runs — a still-running export isn't news yet
    if run.status != "running":
        background.add_task(evaluate_alerts, {"run_id": run.run_id, **payload, "metadata": run.metadata})
    return {"run_id": run.run_id, "spans": len(spans)}

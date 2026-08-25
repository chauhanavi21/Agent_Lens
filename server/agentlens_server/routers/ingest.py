import time
import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts import _describe, build_payload, dispatch, rule_matches
from ..config import API_KEY, REDACT_ON_INGEST
from ..db import SessionLocal, get_session
from ..models import AlertEventRow, AlertRuleRow, RunRow
from ..schemas import RunIn, ScoresIn

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
        rules = (
            (
                await session.execute(
                    select(AlertRuleRow).where(AlertRuleRow.enabled == True)  # noqa: E712
                )
            )
            .scalars()
            .all()
        )
        for r in rules:
            rule = {"name": r.name, "field": r.field, "op": r.op, "value": r.value, "run_name": r.run_name}
            try:
                if not rule_matches(rule, run):
                    continue
            except Exception:
                continue  # a malformed rule must not break ingest
            ok, err = dispatch(r.webhook_url, build_payload(rule, run))
            event = AlertEventRow(
                id=uuid.uuid4().hex[:16],
                rule_id=r.id,
                rule_name=r.name,
                run_id=run["run_id"],
                run_name=run["name"],
                reason=_describe(rule, run),
                delivered=ok,
                delivery_error=err,
                fired_at=time.time(),
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
    payload = dict(  # noqa: C408 - keyword form mirrors the column names
        trace_id=run.trace_id or run.run_id,
        is_remote=any(s.get("remote_parent_id") for s in spans),
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
        scores=[sc.model_dump() for sc in run.scores],
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


@router.post("/ingest/scores", status_code=200)
async def ingest_scores(
    body: ScoresIn,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """
    Attach eval scores to a run that already finished. Eval harnesses run
    after the agent, so scores arrive on their own schedule; alerts are
    re-evaluated here so a quality regression still pages you.
    """
    _check_auth(authorization)
    row = await session.get(RunRow, body.run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{body.run_id}' not found.")

    incoming = [s.model_dump() for s in body.scores]
    for s in incoming:
        if s.get("passed") is None and s.get("threshold") is not None:
            s["passed"] = float(s["value"]) >= float(s["threshold"])
    # replace by name so re-scoring a run updates rather than duplicates
    kept = [s for s in (row.scores or []) if s["name"] not in {i["name"] for i in incoming}]
    row.scores = kept + incoming
    await session.commit()

    run = {
        "run_id": row.run_id,
        "name": row.name,
        "status": row.status,
        "total_cost_usd": row.total_cost_usd,
        "total_tokens": row.total_tokens,
        "duration_ms": row.duration_ms,
        "spans": row.spans or [],
        "scores": row.scores,
    }
    background.add_task(evaluate_alerts, run)
    return {"run_id": row.run_id, "scores": len(row.scores)}


@router.post("/ingest/otlp", status_code=202)
async def ingest_otlp(
    payload: dict,
    background: BackgroundTasks,
    session: AsyncSession = Depends(get_session),
    authorization: str | None = Header(default=None),
):
    """
    OTLP/HTTP trace receiver. Point any OpenTelemetry exporter here — or an
    OTel Collector's otlphttp exporter — and agent traces show up as runs
    with the full DAG, no SDK swap required.

    Accepts the same JSON body as a collector's /v1/traces endpoint.
    """
    _check_auth(authorization)
    from ..otlp import convert_otlp

    try:
        runs = convert_otlp(payload)
        if REDACT_ON_INGEST:
            from agentlens.redaction import default_redactor

            redactor = default_redactor()
            for run in runs:
                run["spans"] = [redactor.redact_value(s) for s in run["spans"]]
                if run.get("error"):
                    run["error"] = redactor.redact_text(run["error"])
    except Exception as e:
        raise HTTPException(status_code=422, detail=f"Could not read OTLP payload: {e}") from e
    if not runs:
        return {"accepted": 0, "runs": []}

    accepted = []
    for run in runs:
        existing = await session.get(RunRow, run["run_id"])
        fields = dict(  # noqa: C408 - keyword form mirrors the column names
            name=run["name"],
            status=run["status"],
            tags=run["tags"],
            started_at=run["started_at"],
            ended_at=run["ended_at"],
            duration_ms=run["duration_ms"],
            total_tokens=run["total_tokens"],
            total_cost_usd=run["total_cost_usd"],
            error=run["error"],
            meta=run["metadata"],
            scores=run["scores"],
            spans=run["spans"],
        )
        if existing is None:
            session.add(RunRow(run_id=run["run_id"], **fields))
        else:
            for k, v in fields.items():
                setattr(existing, k, v)
        accepted.append(run["run_id"])
    await session.commit()

    for run in runs:
        if run["status"] != "running":
            background.add_task(evaluate_alerts, run)
    return {"accepted": len(accepted), "runs": accepted}

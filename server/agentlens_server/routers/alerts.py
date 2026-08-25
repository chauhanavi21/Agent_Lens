import time
import uuid

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import delete, desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..alerts import FIELDS, OPS, RuleError, build_payload, dispatch, validate_rule
from ..db import get_session
from ..models import AlertEventRow, AlertRuleRow
from ..schemas import AlertEventOut, AlertRuleIn, AlertRuleOut

router = APIRouter(tags=["alerts"])


def _rule_out(r: AlertRuleRow) -> AlertRuleOut:
    return AlertRuleOut(
        id=r.id,
        name=r.name,
        field=r.field,
        op=r.op,
        value=r.value,
        webhook_url=r.webhook_url,
        run_name=r.run_name,
        enabled=r.enabled,
        created_at=r.created_at,
    )


@router.get("/alerts/fields")
async def alert_fields():
    """What a rule can be built from — powers the rule builder UI."""
    return {"fields": sorted(FIELDS), "operators": sorted(OPS)}


@router.get("/alerts/rules", response_model=list[AlertRuleOut])
async def list_rules(session: AsyncSession = Depends(get_session)):
    rows = (
        (await session.execute(select(AlertRuleRow).order_by(desc(AlertRuleRow.created_at)))).scalars().all()
    )
    return [_rule_out(r) for r in rows]


@router.post("/alerts/rules", response_model=AlertRuleOut, status_code=201)
async def create_rule(rule: AlertRuleIn, session: AsyncSession = Depends(get_session)):
    try:
        validate_rule(rule.field, rule.op, rule.value)
    except RuleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    row = AlertRuleRow(
        id=uuid.uuid4().hex[:16],
        name=rule.name,
        run_name=rule.run_name,
        field=rule.field,
        op=rule.op,
        value=str(rule.value),
        webhook_url=rule.webhook_url,
        enabled=rule.enabled,
        created_at=time.time(),
    )
    session.add(row)
    await session.commit()
    return _rule_out(row)


@router.patch("/alerts/rules/{rule_id}", response_model=AlertRuleOut)
async def update_rule(rule_id: str, rule: AlertRuleIn, session: AsyncSession = Depends(get_session)):
    row = await session.get(AlertRuleRow, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    try:
        validate_rule(rule.field, rule.op, rule.value)
    except RuleError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    row.name, row.field, row.op = rule.name, rule.field, rule.op
    row.value, row.webhook_url = str(rule.value), rule.webhook_url
    row.run_name, row.enabled = rule.run_name, rule.enabled
    await session.commit()
    return _rule_out(row)


@router.delete("/alerts/rules/{rule_id}", status_code=204)
async def delete_rule(rule_id: str, session: AsyncSession = Depends(get_session)):
    row = await session.get(AlertRuleRow, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    await session.execute(delete(AlertRuleRow).where(AlertRuleRow.id == rule_id))
    await session.commit()


@router.post("/alerts/rules/{rule_id}/test")
async def test_rule(rule_id: str, session: AsyncSession = Depends(get_session)):
    """Send a sample alert so the user can confirm the webhook works."""
    row = await session.get(AlertRuleRow, rule_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Rule '{rule_id}' not found.")
    sample = {
        "run_id": "test-run",
        "name": row.run_name or "sample_agent",
        "status": "error",
        "total_cost_usd": 0.42,
        "total_tokens": 4200,
        "duration_ms": 3100,
        "spans": [],
    }
    rule = {"name": row.name, "field": row.field, "op": row.op, "value": row.value}
    ok, err = dispatch(row.webhook_url, build_payload(rule, sample))
    return {"delivered": ok, "error": err}


@router.get("/alerts/events", response_model=list[AlertEventOut])
async def list_events(
    session: AsyncSession = Depends(get_session),
    limit: int = Query(default=50, le=200),
    run_id: str | None = Query(default=None),
):
    stmt = select(AlertEventRow).order_by(desc(AlertEventRow.fired_at)).limit(limit)
    if run_id:
        stmt = stmt.where(AlertEventRow.run_id == run_id)
    rows = (await session.execute(stmt)).scalars().all()
    return [
        AlertEventOut(
            id=r.id,
            rule_id=r.rule_id,
            rule_name=r.rule_name,
            run_id=r.run_id,
            run_name=r.run_name,
            reason=r.reason,
            delivered=r.delivered,
            delivery_error=r.delivery_error,
            fired_at=r.fired_at,
        )
        for r in rows
    ]

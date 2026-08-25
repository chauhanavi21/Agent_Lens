import os
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db import get_session
from ..gate import evaluate, to_markdown
from ..judge import BUILTIN_RUBRICS, judge_run
from ..models import RunRow

router = APIRouter(tags=["evals"])


class JudgeRequest(BaseModel):
    run_id: str
    rubrics: Optional[list[str]] = None
    model: str = "claude-sonnet-4"


class GateRequest(BaseModel):
    candidate_tag: str = Field(description="Tag identifying this branch's runs, e.g. 'pr-123'")
    baseline_tag: Optional[str] = Field(default=None, description="Tag to compare against, e.g. 'main'")
    thresholds: dict[str, float] = Field(default_factory=dict)
    max_regression: float = 0.05
    min_runs: int = 1
    fail_on_error_runs: bool = True
    limit: int = 100


def _runs_with_tag(rows, tag: str) -> list[dict]:
    return [
        {
            "run_id": r.run_id,
            "name": r.name,
            "status": r.status,
            "scores": r.scores or [],
            "tags": r.tags or [],
        }
        for r in rows
        if tag in (r.tags or [])
    ]


@router.get("/evals/rubrics")
async def list_rubrics():
    """The judged criteria available, for building a request or a UI."""
    return {
        "rubrics": [
            {"name": r.name, "question": r.question, "threshold": r.threshold, "guidance": r.guidance}
            for r in BUILTIN_RUBRICS.values()
        ]
    }


@router.post("/evals/judge")
async def judge(req: JudgeRequest, session: AsyncSession = Depends(get_session)):
    """
    Score a run with an LLM judge reading its execution trace. Scores are
    stored in the same shape as inline and Ragas scores, so trends, diffs,
    and alerts treat them identically.
    """
    row = await session.get(RunRow, req.run_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Run '{req.run_id}' not found.")
    if not os.getenv("ANTHROPIC_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY is not set on the server, so the judge can't run.",
        )

    run = {
        "run_id": row.run_id,
        "name": row.name,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "total_tokens": row.total_tokens,
        "total_cost_usd": row.total_cost_usd,
        "error": row.error,
        "spans": row.spans or [],
    }
    try:
        from ..judge import anthropic_judge

        scores = judge_run(run, req.rubrics, provider=anthropic_judge(req.model))
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Judge call failed: {e}") from e

    now = time.time()
    for s in scores:
        s["recorded_at"] = now
    incoming = {s["name"] for s in scores}
    row.scores = [s for s in (row.scores or []) if s["name"] not in incoming] + scores
    await session.commit()
    return {"run_id": row.run_id, "scores": scores}


@router.post("/evals/gate")
async def gate(req: GateRequest, session: AsyncSession = Depends(get_session)):
    """
    The CI check. Compares this branch's scored runs against a baseline and
    returns pass/fail with a per-metric breakdown plus a markdown summary
    for a PR comment.
    """
    rows = (
        (await session.execute(select(RunRow).order_by(desc(RunRow.started_at)).limit(max(req.limit, 1) * 4)))
        .scalars()
        .all()
    )

    candidate = _runs_with_tag(rows, req.candidate_tag)[: req.limit]
    baseline = _runs_with_tag(rows, req.baseline_tag)[: req.limit] if req.baseline_tag else []

    if not candidate:
        raise HTTPException(
            status_code=404,
            detail=f"No runs tagged '{req.candidate_tag}'. Tag your eval runs with it so the gate can find them.",
        )

    result = evaluate(
        candidate,
        baseline,
        thresholds=req.thresholds,
        max_regression=req.max_regression,
        min_runs=req.min_runs,
        fail_on_error_runs=req.fail_on_error_runs,
    )
    result["markdown"] = to_markdown(result)
    result["candidate_tag"] = req.candidate_tag
    result["baseline_tag"] = req.baseline_tag
    return result

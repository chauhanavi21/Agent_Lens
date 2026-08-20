"""
LLM-as-judge evaluation.

Ragas and hand-written metrics cover what you can compute. A judge covers
what you can only describe: did the agent actually answer the question, did
it invent a tool result, did it give up too early. The agent's own trace is
the evidence — the judge reads the DAG, not just the final string.

The provider is injected rather than imported, so the scoring logic is
testable without a network call and swappable between vendors.
"""

from __future__ import annotations

import json
import os
import re
import urllib.request
from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass
class Rubric:
    """One judged criterion. Scores are 0.0–1.0 so they sit alongside Ragas."""

    name: str
    question: str
    threshold: Optional[float] = None
    guidance: str = ""


BUILTIN_RUBRICS: dict[str, Rubric] = {
    "task_completion": Rubric(
        name="task_completion",
        question="Did the agent actually complete the task it was given, rather than partially attempting it or stopping early?",
        threshold=0.8,
        guidance="1.0 = fully completed. 0.5 = partially. 0.0 = did not attempt or abandoned it.",
    ),
    "tool_correctness": Rubric(
        name="tool_correctness",
        question="Did the agent choose appropriate tools and pass sensible arguments to them?",
        threshold=0.75,
        guidance="Penalize redundant calls, wrong tool for the job, and malformed arguments.",
    ),
    "grounding": Rubric(
        name="grounding",
        question="Is the final output supported by what the tools and retrievals actually returned, with nothing invented?",
        threshold=0.85,
        guidance="Any claim not traceable to a span's output is a hallucination. Score harshly.",
    ),
    "efficiency": Rubric(
        name="efficiency",
        question="Did the agent reach its result without unnecessary steps, retries, or repeated work?",
        threshold=0.6,
        guidance="Consider span count and duplicated calls relative to the task's difficulty.",
    ),
    "error_handling": Rubric(
        name="error_handling",
        question="When a step failed, did the agent recover sensibly rather than ignoring the failure or looping?",
        threshold=0.7,
        guidance="A run with no failures scores 1.0. Swallowing an error silently scores low.",
    ),
}


def trace_summary(run: dict[str, Any], max_spans: int = 40) -> str:
    """
    Render the run as compact text for the judge. Full payloads would blow
    the context and bury the signal, so this keeps structure, status, and
    truncated inputs/outputs.
    """
    lines = [
        f"Agent: {run.get('name')}",
        f"Status: {run.get('status')}  Duration: {run.get('duration_ms')}ms  "
        f"Tokens: {run.get('total_tokens')}  Cost: ${run.get('total_cost_usd', 0):.4f}",
    ]
    if run.get("error"):
        lines.append(f"Run error: {str(run['error'])[:300]}")

    spans = sorted(run.get("spans") or [], key=lambda s: s.get("started_at") or 0)
    by_id = {s["span_id"]: s for s in spans}

    def depth(s):
        d, cur = 0, s
        while cur.get("parent_id") and cur["parent_id"] in by_id and d < 12:
            cur = by_id[cur["parent_id"]]
            d += 1
        return d

    lines.append("\nExecution trace:")
    for s in spans[:max_spans]:
        pad = "  " * depth(s)
        bits = [f"{pad}- [{s.get('kind')}] {s.get('name')} ({s.get('status')}, {s.get('duration_ms')}ms)"]
        if s.get("retry_of"):
            bits.append(" [retry]")
        line = "".join(bits)
        if s.get("inputs"):
            line += f"\n{pad}    in:  {str(s['inputs'])[:200]}"
        if s.get("outputs"):
            line += f"\n{pad}    out: {str(s['outputs'])[:200]}"
        if s.get("error"):
            line += f"\n{pad}    error: {str(s['error'])[:200]}"
        lines.append(line)
    if len(spans) > max_spans:
        lines.append(f"  … {len(spans) - max_spans} more spans omitted")
    return "\n".join(lines)


def build_prompt(run: dict[str, Any], rubrics: list[Rubric]) -> str:
    criteria = "\n".join(
        f"- {r.name}: {r.question}" + (f" ({r.guidance})" if r.guidance else "")
        for r in rubrics
    )
    return f"""You are evaluating an AI agent's execution trace.

{trace_summary(run)}

Score each criterion from 0.0 to 1.0:
{criteria}

Respond with JSON only, no prose or code fences:
{{"scores": {{"<criterion>": {{"value": 0.0, "reason": "<one sentence>"}}}}}}"""


def parse_judge_response(text: str, rubrics: list[Rubric]) -> dict[str, dict[str, Any]]:
    """
    Read the judge's JSON. Models wrap JSON in fences or prose often enough
    that a strict parse would fail runs for cosmetic reasons.
    """
    cleaned = text.strip()
    cleaned = re.sub(r"^```(?:json)?|```$", "", cleaned, flags=re.MULTILINE).strip()
    match = re.search(r"\{.*\}", cleaned, re.DOTALL)
    if not match:
        raise ValueError("Judge response contained no JSON object.")
    data = json.loads(match.group(0))
    raw = data.get("scores", data)

    wanted = {r.name for r in rubrics}
    out: dict[str, dict[str, Any]] = {}
    for name, value in raw.items():
        if name not in wanted:
            continue
        if isinstance(value, dict):
            score, reason = value.get("value", value.get("score")), value.get("reason", "")
        else:
            score, reason = value, ""
        try:
            score = float(score)
        except (TypeError, ValueError):
            continue
        out[name] = {"value": max(0.0, min(1.0, score)), "reason": str(reason)[:500]}
    if not out:
        raise ValueError("Judge returned no scores matching the requested rubrics.")
    return out


def anthropic_judge(model: str = "claude-sonnet-4", api_key: Optional[str] = None) -> Callable[[str], str]:
    """Default provider. Returns a callable so the caller can swap it out."""
    key = api_key or os.getenv("ANTHROPIC_API_KEY", "")

    def call(prompt: str) -> str:
        if not key:
            raise RuntimeError("ANTHROPIC_API_KEY is not set; cannot run the judge.")
        body = json.dumps({
            "model": model,
            "max_tokens": 1024,
            "messages": [{"role": "user", "content": prompt}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=body,
            headers={
                "Content-Type": "application/json",
                "x-api-key": key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=60) as res:
            data = json.loads(res.read())
        return "".join(block.get("text", "") for block in data.get("content", []))

    return call


def judge_run(
    run: dict[str, Any],
    rubric_names: Optional[list[str]] = None,
    provider: Optional[Callable[[str], str]] = None,
) -> list[dict[str, Any]]:
    """
    Score one run. Returns score dicts in the same shape as inline and Ragas
    scores, so everything downstream — trends, diffs, alerts — treats a
    judged score identically.
    """
    names = rubric_names or list(BUILTIN_RUBRICS)
    unknown = [n for n in names if n not in BUILTIN_RUBRICS]
    if unknown:
        raise ValueError(f"Unknown rubric(s): {', '.join(unknown)}. Available: {', '.join(BUILTIN_RUBRICS)}.")
    rubrics = [BUILTIN_RUBRICS[n] for n in names]

    call = provider or anthropic_judge()
    raw = call(build_prompt(run, rubrics))
    parsed = parse_judge_response(raw, rubrics)

    scores = []
    for r in rubrics:
        got = parsed.get(r.name)
        if got is None:
            continue
        scores.append({
            "name": r.name,
            "value": got["value"],
            "source": "llm_judge",
            "threshold": r.threshold,
            "passed": None if r.threshold is None else got["value"] >= r.threshold,
            "comment": got["reason"],
            "span_id": None,
            "recorded_at": 0.0,
        })
    return scores

"""
Alert rules: evaluate a finished run against user-defined conditions and
dispatch webhooks (Slack-compatible or generic JSON).

A rule is a small declarative object, not code, so rules can be created
from the UI or API without a deploy:

    {"name": "expensive runs", "field": "total_cost_usd",
     "op": "gt", "value": 0.50, "webhook_url": "https://hooks.slack.com/..."}
"""

from __future__ import annotations

import json
import urllib.request
from typing import Any, Optional

# fields a rule can test, mapped to how they're pulled off a run dict
FIELDS = {
    "status": lambda r: r.get("status"),
    "total_cost_usd": lambda r: float(r.get("total_cost_usd") or 0.0),
    "total_tokens": lambda r: int(r.get("total_tokens") or 0),
    "duration_ms": lambda r: float(r.get("duration_ms") or 0.0),
    "span_count": lambda r: len(r.get("spans") or []),
    "error_span_count": lambda r: sum(1 for s in (r.get("spans") or []) if s.get("status") == "error"),
    "retry_count": lambda r: sum(1 for s in (r.get("spans") or []) if s.get("retry_of")),
    "name": lambda r: r.get("name"),
    # quality: lets a score regression page you like any other failure
    "failed_score_count": lambda r: sum(1 for s in (r.get("scores") or []) if s.get("passed") is False),
    "min_score": lambda r: min([float(s["value"]) for s in (r.get("scores") or [])], default=1.0),
}


def score_field(run: dict, name: str) -> Optional[float]:
    """Value of a named score on a run, if present."""
    for s in run.get("scores") or []:
        if s.get("name") == name:
            return float(s["value"])
    return None

OPS = {
    "gt": lambda a, b: a > b,
    "gte": lambda a, b: a >= b,
    "lt": lambda a, b: a < b,
    "lte": lambda a, b: a <= b,
    "eq": lambda a, b: str(a) == str(b),
    "neq": lambda a, b: str(a) != str(b),
    "contains": lambda a, b: str(b).lower() in str(a).lower(),
}


class RuleError(ValueError):
    pass


def validate_rule(field: str, op: str, value: Any) -> None:
    if field.startswith("score:"):
        if not field[6:].strip():
            raise RuleError("Name the score to test, e.g. 'score:faithfulness'.")
    elif field not in FIELDS:
        raise RuleError(f"Unknown field '{field}'. Choose one of: {', '.join(sorted(FIELDS))}.")
    if op not in OPS:
        raise RuleError(f"Unknown operator '{op}'. Choose one of: {', '.join(sorted(OPS))}.")
    if op in ("gt", "gte", "lt", "lte"):
        try:
            float(value)
        except (TypeError, ValueError):
            raise RuleError(f"Operator '{op}' needs a numeric value, got '{value}'.")


def rule_matches(rule: dict, run: dict) -> bool:
    """True when the run trips this rule. Also honors an optional run-name scope."""
    scope = rule.get("run_name")
    if scope and scope.lower() not in (run.get("name") or "").lower():
        return False
    field, op, value = rule["field"], rule["op"], rule["value"]
    if field.startswith("score:"):
        actual = score_field(run, field[6:])
        if actual is None:
            return False  # run wasn't scored on this metric
    else:
        actual = FIELDS[field](run)
    if op in ("gt", "gte", "lt", "lte"):
        try:
            return OPS[op](float(actual), float(value))
        except (TypeError, ValueError):
            return False
    return OPS[op](actual, value)


def _describe(rule: dict, run: dict) -> str:
    f = rule["field"]
    actual = score_field(run, f[6:]) if f.startswith("score:") else FIELDS[f](run)
    return f"{rule['field']} is {actual} ({rule['op']} {rule['value']})"


def build_payload(rule: dict, run: dict, ui_base: str = "http://localhost:5173") -> dict:
    """Slack-compatible payload; generic consumers can read the `alert` key."""
    reason = _describe(rule, run)
    link = f"{ui_base}/?run={run['run_id']}"
    text = (
        f"AgentLens alert · {rule['name']}\n"
        f"Run {run['name']} ({run['status']}) tripped: {reason}\n"
        f"{run.get('total_tokens', 0)} tokens · ${float(run.get('total_cost_usd') or 0):.4f} · "
        f"{round(float(run.get('duration_ms') or 0))}ms\n{link}"
    )
    return {
        "text": text,
        "alert": {
            "rule": rule["name"],
            "reason": reason,
            "run_id": run["run_id"],
            "run_name": run["name"],
            "status": run["status"],
            "total_tokens": run.get("total_tokens", 0),
            "total_cost_usd": run.get("total_cost_usd", 0.0),
            "duration_ms": run.get("duration_ms"),
            "url": link,
        },
    }


def dispatch(url: str, payload: dict, timeout: float = 5.0) -> tuple[bool, Optional[str]]:
    """POST the payload. Never raises — a broken webhook must not fail ingest."""
    try:
        req = urllib.request.Request(
            url,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return 200 <= res.status < 300, None
    except Exception as e:
        return False, str(e)

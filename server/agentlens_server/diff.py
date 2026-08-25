"""
Run diffing: align two runs' spans by DAG position (path of names from
root), then report added/removed/changed nodes. Answers "why did this run
fail at step 4 when yesterday's succeeded?"
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Optional


def _paths(spans: list[dict]) -> dict[str, dict]:
    """Map each span to a stable path key: root.child.grandchild#occurrence."""
    by_id = {s["span_id"]: s for s in spans}

    def path_of(span: dict) -> str:
        parts, cur = [], span
        while cur is not None:
            parts.append(cur["name"])
            cur = by_id.get(cur["parent_id"]) if cur.get("parent_id") else None
        return ".".join(reversed(parts))

    seen: dict[str, int] = defaultdict(int)
    keyed: dict[str, dict] = {}
    for s in sorted(spans, key=lambda x: x["started_at"]):
        base = path_of(s)
        idx = seen[base]
        seen[base] += 1
        keyed[f"{base}#{idx}"] = s
    return keyed


def _span_delta(a: dict, b: dict) -> Optional[dict[str, Any]]:
    changes: dict[str, Any] = {}
    if a["status"] != b["status"]:
        changes["status"] = {"a": a["status"], "b": b["status"]}
    da, db = a.get("duration_ms") or 0, b.get("duration_ms") or 0
    if da and db:
        pct = (db - da) / da * 100 if da else 0.0
        if abs(pct) >= 20:  # only surface meaningful latency shifts
            changes["duration_ms"] = {"a": da, "b": db, "pct": round(pct, 1)}
    la, lb = a.get("llm") or {}, b.get("llm") or {}
    ta, tb = la.get("total_tokens", 0), lb.get("total_tokens", 0)
    if ta != tb:
        changes["tokens"] = {"a": ta, "b": tb}
    ca, cb = la.get("cost_usd", 0.0), lb.get("cost_usd", 0.0)
    if round(ca, 6) != round(cb, 6):
        changes["cost_usd"] = {"a": ca, "b": cb}
    if (a.get("error") or "") != (b.get("error") or ""):
        changes["error"] = {"a": a.get("error"), "b": b.get("error")}
    return changes or None


def _score_delta(run_a: dict, run_b: dict) -> list[dict[str, Any]]:
    """Compare eval scores by name so quality regressions show up in a diff."""
    sa = {s["name"]: s for s in (run_a.get("scores") or [])}
    sb = {s["name"]: s for s in (run_b.get("scores") or [])}
    out = []
    for name in sorted(set(sa) | set(sb)):
        a, b = sa.get(name), sb.get(name)
        if a and b:
            if round(float(a["value"]), 6) != round(float(b["value"]), 6):
                out.append(
                    {
                        "name": name,
                        "a": a["value"],
                        "b": b["value"],
                        "delta": round(float(b["value"]) - float(a["value"]), 4),
                        "passed_a": a.get("passed"),
                        "passed_b": b.get("passed"),
                    }
                )
        else:
            # present in only one run: a metric that appeared or disappeared
            out.append(
                {
                    "name": name,
                    "a": a["value"] if a else None,
                    "b": b["value"] if b else None,
                    "delta": None,
                    "passed_a": a.get("passed") if a else None,
                    "passed_b": b.get("passed") if b else None,
                }
            )
    return out


def diff_runs(run_a: dict, run_b: dict) -> dict[str, Any]:
    pa, pb = _paths(run_a["spans"]), _paths(run_b["spans"])
    keys_a, keys_b = set(pa), set(pb)

    removed = sorted(keys_a - keys_b)
    added = sorted(keys_b - keys_a)
    changed = []
    for key in sorted(keys_a & keys_b):
        delta = _span_delta(pa[key], pb[key])
        if delta:
            changed.append(
                {"path": key, "span_a": pa[key]["span_id"], "span_b": pb[key]["span_id"], "changes": delta}
            )

    return {
        "run_a": {
            "run_id": run_a["run_id"],
            "name": run_a["name"],
            "status": run_a["status"],
            "duration_ms": run_a.get("duration_ms"),
            "total_tokens": run_a.get("total_tokens", 0),
            "total_cost_usd": run_a.get("total_cost_usd", 0.0),
        },
        "run_b": {
            "run_id": run_b["run_id"],
            "name": run_b["name"],
            "status": run_b["status"],
            "duration_ms": run_b.get("duration_ms"),
            "total_tokens": run_b.get("total_tokens", 0),
            "total_cost_usd": run_b.get("total_cost_usd", 0.0),
        },
        "added": [{"path": k, "span": pb[k]["span_id"], "status": pb[k]["status"]} for k in added],
        "removed": [{"path": k, "span": pa[k]["span_id"], "status": pa[k]["status"]} for k in removed],
        "changed": changed,
        "scores": _score_delta(run_a, run_b),
        "summary": {
            "added": len(added),
            "removed": len(removed),
            "changed": len(changed),
            "verdict": _verdict(run_a, run_b, changed),
        },
    }


def _verdict(a: dict, b: dict, changed: list) -> str:
    parts = []

    # quality leads: a score regression is the headline even when the DAG matches
    regressions = [s for s in _score_delta(a, b) if s.get("delta") is not None and s["delta"] < 0]
    if regressions:
        worst = min(regressions, key=lambda s: s["delta"])
        parts.append(f"Quality dropped: {worst['name']} {worst['a']} → {worst['b']}.")

    if a["status"] != b["status"]:
        flips = [c for c in changed if "status" in c["changes"]]
        if flips:
            # deepest flipped span is the true divergence point (root flips are downstream)
            deepest = max(flips, key=lambda c: c["path"].count("."))
            parts.append(f"Status diverged first at '{deepest['path']}'.")
        else:
            parts.append("Run status differs but no single span status flip was found.")
    elif changed:
        parts.append(f"{len(changed)} span(s) changed behavior between runs.")

    if not parts:
        return "Runs are structurally and behaviorally equivalent."
    return " ".join(parts)

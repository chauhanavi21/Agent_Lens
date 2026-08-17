"""
Cross-process span stitching.

An MCP server exports its own run: it doesn't know, and shouldn't need to
know, which agent called it. Both sides share a W3C trace id, and the
server's root span carries the caller's span id as `remote_parent_id`.
That's enough to graft the server's spans onto the agent's DAG so one
waterfall shows agent reasoning → tool selection → server execution.

Stitching happens at read time rather than by rewriting the caller's row.
Either side can arrive first, a late-arriving server run still merges, and
each service keeps owning its own data.
"""

from __future__ import annotations

from typing import Any


def is_child_run(run: dict) -> bool:
    """True when this run is a remote continuation of some other run."""
    return any(s.get("remote_parent_id") for s in run.get("spans") or [])


def stitch(parent: dict, children: list[dict]) -> dict:
    """
    Graft child runs' spans into the parent run. Returns a new dict; the
    stored rows are never mutated.
    """
    if not children:
        return parent

    merged = dict(parent)
    spans = [dict(s) for s in parent.get("spans") or []]
    known = {s["span_id"] for s in spans}
    grafted, orphaned = 0, 0

    for child in sorted(children, key=lambda c: c.get("started_at") or 0):
        child_spans = [dict(s) for s in child.get("spans") or []]
        for s in child_spans:
            remote = s.get("remote_parent_id")
            if remote:
                if remote in known:
                    s["parent_id"] = remote
                    grafted += 1
                else:
                    # caller's span isn't in this run — leave it a root so
                    # the data is still visible rather than silently dropped
                    orphaned += 1
            s.setdefault("service", child.get("name"))
        spans.extend(child_spans)
        known.update(s["span_id"] for s in child_spans)

    merged["spans"] = spans
    merged["total_tokens"] = (parent.get("total_tokens") or 0) + sum(c.get("total_tokens") or 0 for c in children)
    merged["total_cost_usd"] = round(
        (parent.get("total_cost_usd") or 0.0) + sum(c.get("total_cost_usd") or 0.0 for c in children), 6
    )
    ends = [e for e in [parent.get("ended_at")] + [c.get("ended_at") for c in children] if e]
    if ends and parent.get("started_at"):
        merged["ended_at"] = max(ends)
        merged["duration_ms"] = round((max(ends) - parent["started_at"]) * 1000, 2)
    if parent.get("status") == "success" and any(c.get("status") == "error" for c in children):
        # the agent may have swallowed a tool failure; the DAG shouldn't
        merged["status"] = "success"
    merged["metadata"] = {
        **(parent.get("metadata") or {}),
        "stitched_runs": [c["run_id"] for c in children],
        "stitched_services": sorted({c.get("name", "") for c in children}),
        "grafted_spans": grafted,
        "orphaned_spans": orphaned,
    }
    return merged

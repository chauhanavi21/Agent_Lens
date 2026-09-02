"""
Cross-run span analytics.

Spans live as JSONB on the run row because every view reads a run whole
(§3.1 of ARCHITECTURE.md). That's right for the product's dominant read and
wrong for one question it can't answer: "what's the p95 latency of
`web_search` across the last ten thousand runs?" — which needs a scan.

The fix is a derived table, not a schema change. `span_index` holds one
narrow row per span, written on ingest and rebuilt on demand. JSONB stays
the source of truth; this is a query-shaped copy that can be dropped and
regenerated without losing anything.

Percentiles are computed in Python rather than SQL. `percentile_cont` exists
on PostgreSQL and not on SQLite, and the dev/CI path runs SQLite — a query
that only works on one of them is a query nobody tests. The durations are
fetched with a cap and sorted in memory, which is fine at the scale this
answers for and honest about where it stops being fine.
"""

from __future__ import annotations

import math
import time
from typing import Any, Iterable, Optional

# How many durations to pull per span name when computing percentiles.
# Beyond this the estimate stops improving and the memory cost starts
# mattering, so the response says the sample was capped rather than
# pretending it saw everything.
PERCENTILE_SAMPLE_CAP = 20_000


def percentile(sorted_values: list[float], fraction: float) -> Optional[float]:
    """
    Linear-interpolated percentile over an already-sorted list.

    Nearest-rank is simpler but jumps in coarse steps on small samples,
    which makes a p95 look like it moved when only one run was added.
    """
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return sorted_values[0]

    position = fraction * (len(sorted_values) - 1)
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return sorted_values[int(position)]
    weight = position - lower
    return sorted_values[lower] * (1 - weight) + sorted_values[upper] * weight


def span_rows_for_run(run: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Flatten a run's spans into index rows.

    Only the columns a cross-run question needs — no inputs, outputs, or
    attributes. The point of the derived table is that it stays narrow
    enough to scan.
    """
    rows = []
    run_id = run.get("run_id")
    run_name = run.get("name") or ""
    for span in run.get("spans") or []:
        llm = span.get("llm") or {}
        rows.append(
            {
                "span_id": span.get("span_id"),
                "run_id": run_id,
                "run_name": run_name,
                "name": span.get("name") or "",
                "kind": span.get("kind") or "custom",
                "status": span.get("status") or "success",
                "started_at": span.get("started_at") or 0.0,
                "duration_ms": span.get("duration_ms"),
                "total_tokens": int(llm.get("total_tokens") or 0),
                "cost_usd": float(llm.get("cost_usd") or 0.0),
                "model": str(llm.get("model") or ""),
                "service": span.get("service"),
                "is_retry": bool(span.get("retry_of")),
            }
        )
    return rows


def summarize(
    rows: Iterable[dict[str, Any]],
    group_by: str = "name",
    sample_capped: bool = False,
) -> list[dict[str, Any]]:
    """
    Aggregate index rows into per-group statistics.

    Grouped in Python from pre-filtered rows so the same code path serves
    both databases. The caller is responsible for narrowing the row set
    with SQL first.
    """
    groups: dict[str, dict[str, Any]] = {}

    for row in rows:
        key = str(row.get(group_by) or "")
        bucket = groups.setdefault(
            key,
            {
                group_by: key,
                "kind": row.get("kind"),
                "calls": 0,
                "errors": 0,
                "retries": 0,
                "durations": [],
                "total_tokens": 0,
                "total_cost_usd": 0.0,
                "runs": set(),
            },
        )
        bucket["calls"] += 1
        if row.get("status") == "error":
            bucket["errors"] += 1
        if row.get("is_retry"):
            bucket["retries"] += 1
        if row.get("duration_ms") is not None:
            bucket["durations"].append(float(row["duration_ms"]))
        bucket["total_tokens"] += int(row.get("total_tokens") or 0)
        bucket["total_cost_usd"] += float(row.get("cost_usd") or 0.0)
        bucket["runs"].add(row.get("run_id"))

    out = []
    for bucket in groups.values():
        durations = sorted(bucket.pop("durations"))
        runs = bucket.pop("runs")
        calls = bucket["calls"]
        out.append(
            {
                **bucket,
                "runs": len(runs),
                "error_rate": round(bucket["errors"] / calls, 4) if calls else 0.0,
                "retry_rate": round(bucket["retries"] / calls, 4) if calls else 0.0,
                "p50_ms": _round(percentile(durations, 0.50)),
                "p95_ms": _round(percentile(durations, 0.95)),
                "p99_ms": _round(percentile(durations, 0.99)),
                "max_ms": _round(durations[-1] if durations else None),
                "total_ms": _round(sum(durations) if durations else 0.0),
                "total_cost_usd": round(bucket["total_cost_usd"], 6),
                "sampled": sample_capped,
            }
        )

    # slowest aggregate first: where the wall-clock time actually goes is
    # usually the reason someone opened this view
    out.sort(key=lambda g: g["total_ms"] or 0, reverse=True)
    return out


def _round(value: Optional[float]) -> Optional[float]:
    return None if value is None else round(value, 2)


def find_outliers(
    rows: Iterable[dict[str, Any]],
    stats: list[dict[str, Any]],
    limit: int = 20,
) -> list[dict[str, Any]]:
    """
    Individual spans that ran far slower than their own p95.

    Aggregates tell you a step is slow on average; this tells you which
    specific run to go and open, which is the actual next question.
    """
    p95_by_name = {s["name"]: s.get("p95_ms") for s in stats if s.get("p95_ms")}
    outliers = []
    for row in rows:
        threshold = p95_by_name.get(row.get("name"))
        duration = row.get("duration_ms")
        if not threshold or duration is None or duration <= threshold:
            continue
        outliers.append(
            {
                "span_id": row.get("span_id"),
                "run_id": row.get("run_id"),
                "name": row.get("name"),
                "kind": row.get("kind"),
                "duration_ms": round(float(duration), 2),
                "p95_ms": round(float(threshold), 2),
                "times_p95": round(float(duration) / float(threshold), 2),
                "started_at": row.get("started_at"),
            }
        )
    outliers.sort(key=lambda o: o["times_p95"], reverse=True)
    return outliers[:limit]


def window_start(days: Optional[float], now: Optional[float] = None) -> Optional[float]:
    if days is None:
        return None
    return (now if now is not None else time.time()) - days * 86400

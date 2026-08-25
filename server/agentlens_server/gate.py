"""
The CI gate: does this branch's eval quality still clear the bar?

Two questions, deliberately separated:

  1. Absolute — is any metric below its floor? Catches a branch that was
     always bad.
  2. Relative — did any metric drop more than `max_regression` against a
     baseline? Catches a branch that made things worse, even while still
     technically passing.

The second matters more in practice. A metric drifting 0.92 → 0.86 passes
every fixed threshold and is exactly the regression nobody notices until
it's three releases old.
"""

from __future__ import annotations

from statistics import mean
from typing import Any, Optional


def aggregate(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Mean score per metric across a set of runs."""
    buckets: dict[str, list[float]] = {}
    for run in runs:
        for s in run.get("scores") or []:
            try:
                buckets.setdefault(s["name"], []).append(float(s["value"]))
            except (KeyError, TypeError, ValueError):
                continue
    return {
        name: {
            "mean": round(mean(vals), 4),
            "n": len(vals),
            "min": round(min(vals), 4),
            "max": round(max(vals), 4),
        }
        for name, vals in buckets.items()
    }


def evaluate(
    candidate_runs: list[dict[str, Any]],
    baseline_runs: Optional[list[dict[str, Any]]] = None,
    thresholds: Optional[dict[str, float]] = None,
    max_regression: float = 0.05,
    min_runs: int = 1,
    fail_on_error_runs: bool = True,
) -> dict[str, Any]:
    """Decide pass/fail and explain why, metric by metric."""
    thresholds = thresholds or {}
    candidate = aggregate(candidate_runs)
    baseline = aggregate(baseline_runs or [])

    checks: list[dict[str, Any]] = []
    failures: list[str] = []

    if len(candidate_runs) < min_runs:
        failures.append(f"only {len(candidate_runs)} candidate run(s), need {min_runs}")

    errored = [r for r in candidate_runs if r.get("status") == "error"]
    if fail_on_error_runs and errored:
        failures.append(f"{len(errored)} candidate run(s) errored")

    # baseline metrics are included: one the branch stopped producing is
    # lost coverage, which would otherwise pass silently
    for name in sorted(set(candidate) | set(baseline) | set(thresholds)):
        cand = candidate.get(name)
        if cand is None:
            # a metric the baseline measured but this branch didn't produce
            # is a gap in coverage, not a silent pass
            checks.append(
                {
                    "metric": name,
                    "status": "missing",
                    "candidate": None,
                    "baseline": baseline.get(name, {}).get("mean"),
                    "detail": "no runs scored this metric",
                }
            )
            failures.append(f"{name}: not scored on this branch")
            continue

        row: dict[str, Any] = {
            "metric": name,
            "candidate": cand["mean"],
            "n": cand["n"],
            "baseline": baseline.get(name, {}).get("mean"),
            "threshold": thresholds.get(name),
        }
        problems = []

        floor = thresholds.get(name)
        if floor is not None and cand["mean"] < floor:
            problems.append(f"below floor {floor}")

        base = baseline.get(name)
        if base is not None:
            delta = round(cand["mean"] - base["mean"], 4)
            row["delta"] = delta
            if delta < -abs(max_regression):
                problems.append(f"regressed {delta:+.4f} (limit {-abs(max_regression):+.4f})")

        row["status"] = "fail" if problems else "pass"
        row["detail"] = "; ".join(problems)
        checks.append(row)
        if problems:
            failures.append(f"{name}: {row['detail']}")

    passed = not failures
    return {
        "passed": passed,
        "checks": checks,
        "failures": failures,
        "candidate_runs": len(candidate_runs),
        "baseline_runs": len(baseline_runs or []),
        "summary": (
            "All eval checks passed."
            if passed
            else f"{len(failures)} eval check(s) failed: "
            + "; ".join(failures[:3])
            + ("…" if len(failures) > 3 else "")
        ),
    }


def to_markdown(result: dict[str, Any]) -> str:
    """A GitHub-friendly summary table for the PR comment / job summary."""

    def num(value: Any, signed: bool = False) -> str:
        if value is None:
            return "—"
        return f"{value:+.3f}" if signed else f"{value:.3f}"

    icon = "✅" if result["passed"] else "❌"
    lines = [
        f"## {icon} AgentLens eval gate",
        "",
        result["summary"],
        "",
        f"_{result['candidate_runs']} candidate run(s), {result['baseline_runs']} baseline run(s)_",
        "",
        "| Metric | Branch | Baseline | Δ | Floor | Result |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for c in result["checks"]:
        floor = c.get("threshold")
        verdict = "✅" if c["status"] == "pass" else "❌"
        detail = c.get("detail") or ""
        lines.append(
            f"| `{c['metric']}` | {num(c.get('candidate'))} | {num(c.get('baseline'))} "
            f"| {num(c.get('delta'), signed=True)} | {'—' if floor is None else floor} "
            f"| {verdict} {detail} |".rstrip()
        )
    return "\n".join(lines)

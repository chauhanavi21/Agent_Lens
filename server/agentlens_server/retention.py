"""
Data retention.

An observability store that only grows is one that eventually gets dropped
by whoever is paying for the disk. But deleting traces is also how you lose
the run someone was about to investigate, so the policy here is deliberately
conservative: nothing is removed unless a rule says so, protected tags win
over every rule, and the default entry point is a dry run.

Two rules, applied together:

  by age    — drop runs older than N days
  by count  — keep only the newest N runs *per agent*, so a chatty agent
              can't push a quiet one out of the window

Per-agent rather than global, because "keep the last 1000 runs" on a system
where one agent runs 100x more often than another silently deletes the
entire history of the quiet one — which is usually the one you're debugging.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Iterable, Optional


@dataclass
class RetentionPolicy:
    """What to keep. `None` on a rule means that rule is off."""

    max_age_days: Optional[float] = None
    max_runs_per_agent: Optional[int] = None
    protect_tags: frozenset[str] = field(default_factory=frozenset)

    @property
    def active(self) -> bool:
        return self.max_age_days is not None or self.max_runs_per_agent is not None

    def describe(self) -> str:
        if not self.active:
            return "retention is off; nothing is pruned automatically"
        parts = []
        if self.max_age_days is not None:
            parts.append(f"drop runs older than {self.max_age_days:g} days")
        if self.max_runs_per_agent is not None:
            parts.append(f"keep the newest {self.max_runs_per_agent} runs per agent")
        if self.protect_tags:
            parts.append(f"never touch runs tagged {', '.join(sorted(self.protect_tags))}")
        return "; ".join(parts)


@dataclass
class RunRef:
    """The minimum needed to decide a run's fate, so callers can avoid
    loading span payloads just to prune."""

    run_id: str
    name: str
    started_at: float
    tags: list[str]
    trace_id: Optional[str] = None
    is_remote: bool = False


def is_protected(run: RunRef, policy: RetentionPolicy) -> bool:
    return bool(policy.protect_tags.intersection(run.tags or []))


def select_for_pruning(
    runs: Iterable[RunRef],
    policy: RetentionPolicy,
    now: Optional[float] = None,
) -> dict[str, Any]:
    """
    Decide which runs to remove, and say why for each one.

    Returning reasons rather than a bare id list matters: pruning is
    destructive and irreversible, and "why is this run gone?" is a question
    someone will ask.
    """
    if not policy.active:
        return {"run_ids": [], "reasons": {}, "protected": 0, "kept": 0}

    now = now if now is not None else time.time()
    candidates = list(runs)

    protected = [r for r in candidates if is_protected(r, policy)]
    eligible = [r for r in candidates if not is_protected(r, policy)]

    doomed: dict[str, str] = {}

    if policy.max_age_days is not None:
        cutoff = now - policy.max_age_days * 86400
        for run in eligible:
            if run.started_at < cutoff:
                age_days = (now - run.started_at) / 86400
                doomed[run.run_id] = f"older than {policy.max_age_days:g} days ({age_days:.1f} days old)"

    if policy.max_runs_per_agent is not None:
        by_agent: dict[str, list[RunRef]] = {}
        for run in eligible:
            # remote continuations are grouped with their caller's agent so
            # a chatty MCP server doesn't consume another agent's budget
            by_agent.setdefault(run.name, []).append(run)

        for name, group in by_agent.items():
            group.sort(key=lambda r: r.started_at, reverse=True)
            for position, run in enumerate(group):
                if position >= policy.max_runs_per_agent and run.run_id not in doomed:
                    doomed[run.run_id] = (
                        f"run #{position + 1} for '{name}', beyond the newest {policy.max_runs_per_agent}"
                    )

    return {
        "run_ids": sorted(doomed),
        "reasons": doomed,
        "protected": len(protected),
        "kept": len(candidates) - len(doomed),
    }


def expand_to_traces(
    run_ids: Iterable[str],
    all_runs: Iterable[RunRef],
) -> tuple[set[str], dict[str, str]]:
    """
    Pull in remote continuations belonging to a deleted run's trace.

    An MCP server's run is only meaningful stitched into its caller. Leaving
    it behind after the caller is gone produces an orphan that shows up as a
    top-level run nobody recognizes, so deletion follows the trace.

    The reverse is not true: deleting a server's run never removes the agent
    run that called it. That direction would let pruning a tool server's
    history silently destroy the agent traces that reference it.
    """
    doomed = set(run_ids)
    by_id = {r.run_id: r for r in all_runs}
    traces = {by_id[rid].trace_id for rid in doomed if rid in by_id and by_id[rid].trace_id}

    cascaded: dict[str, str] = {}
    for run in by_id.values():
        if run.run_id in doomed or not run.is_remote:
            continue
        if run.trace_id and run.trace_id in traces:
            cascaded[run.run_id] = "remote continuation of a deleted run"
            doomed.add(run.run_id)

    return doomed, cascaded


def policy_from_env(env: dict[str, str]) -> RetentionPolicy:
    """Build a policy from environment variables, tolerating bad values."""

    def number(key: str) -> Optional[float]:
        raw = (env.get(key) or "").strip()
        if not raw:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        # a zero or negative retention would mean "delete everything", which
        # is never what someone meant to type
        return value if value > 0 else None

    age = number("AGENTLENS_RETENTION_DAYS")
    count = number("AGENTLENS_RETENTION_MAX_RUNS_PER_AGENT")
    tags = frozenset(t.strip() for t in (env.get("AGENTLENS_PROTECT_TAGS") or "keep").split(",") if t.strip())
    return RetentionPolicy(
        max_age_days=age,
        max_runs_per_agent=int(count) if count else None,
        protect_tags=tags,
    )

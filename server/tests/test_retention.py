"""
Retention, deletion, and pagination.

Deletion is the one irreversible thing this server does, so these lean hard
on the negative cases: what must *not* be deleted, and what a dry run must
not touch.
"""

import time

import pytest
from agentlens_server.retention import (
    RetentionPolicy,
    RunRef,
    expand_to_traces,
    policy_from_env,
    select_for_pruning,
)

pytestmark = pytest.mark.anyio

NOW = time.time()
DAY = 86400


def ref(run_id, name, days_old, tags=(), trace=None, remote=False):
    return RunRef(
        run_id=run_id,
        name=name,
        started_at=NOW - days_old * DAY,
        tags=list(tags),
        trace_id=trace,
        is_remote=remote,
    )


# --- policy --------------------------------------------------------------- #


def test_no_rules_means_nothing_is_pruned():
    """A misconfigured policy must be a no-op, never a wipe."""
    runs = [ref("a", "agent", 900), ref("b", "agent", 1000)]
    result = select_for_pruning(runs, RetentionPolicy(), NOW)
    assert result["run_ids"] == []


def test_age_rule():
    runs = [ref("old", "agent", 30), ref("new", "agent", 1)]
    result = select_for_pruning(runs, RetentionPolicy(max_age_days=7), NOW)
    assert result["run_ids"] == ["old"]
    assert "older than 7 days" in result["reasons"]["old"]


def test_count_rule_is_per_agent():
    """
    A chatty agent must not push a quiet one out of the window — that quiet
    agent is usually the one being debugged.
    """
    runs = [ref(f"chatty{i}", "chatty_agent", i) for i in range(10)]
    runs.append(ref("quiet1", "quiet_agent", 5))

    result = select_for_pruning(runs, RetentionPolicy(max_runs_per_agent=3), NOW)
    assert "quiet1" not in result["run_ids"], "the quiet agent's only run was deleted"
    assert len([r for r in result["run_ids"] if r.startswith("chatty")]) == 7


def test_protected_tags_beat_every_rule():
    runs = [ref("keeper", "agent", 999, tags=["keep"]), ref("doomed", "agent", 999)]
    policy = RetentionPolicy(max_age_days=1, max_runs_per_agent=0, protect_tags=frozenset({"keep"}))
    result = select_for_pruning(runs, policy, NOW)
    assert result["run_ids"] == ["doomed"]
    assert result["protected"] == 1


def test_every_deletion_carries_a_reason():
    runs = [ref("a", "agent", 30), ref("b", "agent", 40)]
    result = select_for_pruning(runs, RetentionPolicy(max_age_days=7), NOW)
    for run_id in result["run_ids"]:
        assert result["reasons"][run_id], f"{run_id} has no explanation"


def test_env_parsing_rejects_dangerous_values():
    # a zero or negative retention would mean "delete everything"
    assert policy_from_env({"AGENTLENS_RETENTION_DAYS": "0"}).active is False
    assert policy_from_env({"AGENTLENS_RETENTION_DAYS": "-5"}).active is False
    assert policy_from_env({"AGENTLENS_RETENTION_DAYS": "nonsense"}).active is False
    assert policy_from_env({}).active is False

    policy = policy_from_env({"AGENTLENS_RETENTION_DAYS": "30"})
    assert policy.active and policy.max_age_days == 30
    assert "keep" in policy.protect_tags, "the default protect tag disappeared"


# --- trace cascade -------------------------------------------------------- #


def test_deleting_a_caller_takes_its_remote_continuations():
    runs = [
        ref("agent", "issue_agent", 30, trace="t1"),
        ref("server", "github.create_issue", 30, trace="t1", remote=True),
        ref("other", "other_agent", 30, trace="t2"),
    ]
    doomed, cascaded = expand_to_traces(["agent"], runs)
    assert doomed == {"agent", "server"}
    assert "server" in cascaded
    assert "other" not in doomed


def test_deleting_a_server_run_never_removes_its_caller():
    """
    The reverse direction would let cleaning up a tool server's history
    silently destroy the agent traces that reference it.
    """
    runs = [
        ref("agent", "issue_agent", 30, trace="t1"),
        ref("server", "github.create_issue", 30, trace="t1", remote=True),
    ]
    doomed, _ = expand_to_traces(["server"], runs)
    assert doomed == {"server"}


# --- API ------------------------------------------------------------------ #


async def test_delete_a_run(client, make_run):
    run = make_run()
    await client.post("/api/ingest/run", json=run)

    response = await client.delete(f"/api/runs/{run['run_id']}")
    assert response.status_code == 200
    assert run["run_id"] in response.json()["deleted"]

    assert (await client.get(f"/api/runs/{run['run_id']}")).status_code == 404
    assert (await client.delete(f"/api/runs/{run['run_id']}")).status_code == 404


async def test_prune_defaults_to_a_dry_run(client, make_run):
    """Forgetting a parameter must not delete anything."""
    for _ in range(3):
        await client.post("/api/ingest/run", json=make_run())

    result = (await client.post("/api/runs/prune", json={"max_runs_per_agent": 1})).json()
    assert result["dry_run"] is True
    assert result["deleted"] == 0
    assert result["would_delete"] == 2
    assert "Re-run with dry_run=false" in result["summary"]

    # nothing actually went away
    assert len((await client.get("/api/runs")).json()) == 3


async def test_prune_applies_when_asked(client, make_run):
    for _ in range(3):
        await client.post("/api/ingest/run", json=make_run())

    result = (await client.post("/api/runs/prune", json={"max_runs_per_agent": 1, "dry_run": False})).json()
    assert result["deleted"] == 2
    assert len((await client.get("/api/runs")).json()) == 1


async def test_prune_without_a_rule_is_refused(client):
    response = await client.post("/api/runs/prune", json={"dry_run": False})
    assert response.status_code == 422
    assert "no rule" in response.json()["detail"]


async def test_prune_respects_protected_tags(client, make_run):
    await client.post("/api/ingest/run", json=make_run(tags=["keep"]))
    for _ in range(2):
        await client.post("/api/ingest/run", json=make_run())

    result = (
        await client.post(
            "/api/runs/prune",
            json={
                "max_runs_per_agent": 0,
                "protect_tags": ["keep"],
                "dry_run": False,
            },
        )
    ).json()
    assert result["protected"] == 1

    remaining = (await client.get("/api/runs")).json()
    assert len(remaining) == 1
    assert "keep" in remaining[0]["tags"]


async def test_prune_can_target_one_agent(client, make_run):
    await client.post("/api/ingest/run", json=make_run(name="agent_a"))
    await client.post("/api/ingest/run", json=make_run(name="agent_b"))

    result = (
        await client.post(
            "/api/runs/prune",
            json={
                "name": "agent_a",
                "max_runs_per_agent": 0,
                "dry_run": False,
            },
        )
    ).json()
    assert result["deleted"] == 1
    assert [r["name"] for r in (await client.get("/api/runs")).json()] == ["agent_b"]


# --- pagination ----------------------------------------------------------- #


async def test_cursor_pagination_walks_the_whole_history(client, make_run):
    for i in range(7):
        run = make_run()
        run["started_at"] = NOW - i * 60
        await client.post("/api/ingest/run", json=run)

    seen, cursor = [], None
    for _ in range(10):
        query = "/api/runs/page?limit=3" + (f"&cursor={cursor}" if cursor else "")
        page = (await client.get(query)).json()
        seen.extend(r["run_id"] for r in page["runs"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]

    assert len(seen) == 7
    assert len(set(seen)) == 7, "pagination returned a duplicate"


async def test_pagination_is_stable_when_new_runs_arrive(client, make_run):
    """
    The reason for a cursor rather than an offset: with offsets, a run
    arriving at the head shifts every subsequent page and you see a
    duplicate.
    """
    for i in range(5):
        run = make_run()
        run["started_at"] = NOW - 1000 - i * 60
        await client.post("/api/ingest/run", json=run)

    first = (await client.get("/api/runs/page?limit=2")).json()
    seen = [r["run_id"] for r in first["runs"]]

    # a new run lands at the head between pages
    newer = make_run()
    newer["started_at"] = NOW
    await client.post("/api/ingest/run", json=newer)

    second = (await client.get(f"/api/runs/page?limit=2&cursor={first['next_cursor']}")).json()
    seen.extend(r["run_id"] for r in second["runs"])

    assert len(set(seen)) == len(seen), "a run appeared on two pages"
    assert newer["run_id"] not in seen[2:], "the new run leaked into a later page"


async def test_page_filters_match_the_list_endpoint(client, make_run):
    await client.post("/api/ingest/run", json=make_run(name="alpha"))
    await client.post("/api/ingest/run", json=make_run(name="beta", fail=True))

    page = (await client.get("/api/runs/page?status=error")).json()
    assert len(page["runs"]) == 1
    assert page["runs"][0]["name"] == "beta"
    assert page["has_more"] is False

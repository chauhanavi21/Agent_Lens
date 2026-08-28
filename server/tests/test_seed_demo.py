"""
The seeder is what a new user sees first, so its claims are tested.

Every bullet the script prints ("Quality declines", "rules the failures
tripped", "an issue_agent run nests its github spans") is an assertion here —
a demo that silently stops demonstrating something is worse than no demo.
"""

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import seed_demo  # noqa: E402

pytestmark = pytest.mark.anyio


@pytest.fixture
def rng():
    import random

    return random.Random(7)


def test_generated_runs_cover_every_agent(rng):
    runs = seed_demo.build_runs(40, days=7, rng=rng)
    names = {r["name"] for r in runs}
    assert {"research_agent", "support_agent", "triage_agent", "issue_agent"} <= names


def test_generated_runs_are_internally_consistent(rng):
    """Roll-ups must match the spans, or the UI shows numbers that don't add up."""
    for run in seed_demo.build_runs(30, days=7, rng=rng):
        span_tokens = sum((s["llm"] or {}).get("total_tokens", 0) for s in run["spans"])
        assert run["total_tokens"] == span_tokens

        assert run["ended_at"] >= run["started_at"]
        ids = {s["span_id"] for s in run["spans"]}

        # A remote continuation (an MCP server's run) has no local root: its
        # root points at a span in the caller's process. Everything else
        # must have exactly one.
        local_roots = [s for s in run["spans"] if not s["parent_id"] and not s["remote_parent_id"]]
        remote_roots = [s for s in run["spans"] if s["remote_parent_id"]]
        if remote_roots:
            assert local_roots == [], f"{run['name']} mixes local and remote roots"
        else:
            assert len(local_roots) == 1, f"{run['name']} has {len(local_roots)} roots"

        for s in run["spans"]:
            if s["parent_id"]:
                assert s["parent_id"] in ids, "a span points at a parent that isn't in the run"


def test_quality_regression_is_actually_present(rng):
    """The Quality tab is only worth opening if the trend goes somewhere."""
    runs = sorted(seed_demo.build_runs(60, days=7, rng=rng), key=lambda r: r["started_at"])
    values = [s["value"] for r in runs for s in r["scores"] if s["name"] == "faithfulness"]
    assert len(values) >= 10

    early = sum(values[:5]) / 5
    late = sum(values[-5:]) / 5
    assert late < early - 0.05, f"no visible regression: {early:.3f} → {late:.3f}"
    assert any(s["passed"] is False for r in runs for s in r["scores"]), (
        "nothing fails its threshold, so nothing renders red"
    )


def test_failure_modes_are_represented(rng):
    runs = seed_demo.build_runs(60, days=7, rng=rng)
    spans = [s for r in runs for s in r["spans"]]

    assert any(s["retry_of"] for s in spans), "no retry lineage"
    assert any(s["status"] == "error" for s in spans), "no failed spans"
    assert any(r["status"] == "error" for r in runs), "no failed runs"


def test_branch_tags_support_the_gate_demo(rng):
    """The gate command printed at the end has to have something to compare."""
    runs = seed_demo.build_runs(40, days=7, rng=rng)
    tags = {t for r in runs for t in r["tags"]}
    assert "main" in tags and "pr-118" in tags


def test_mcp_pair_shares_a_trace_and_links_back(rng):
    agent, server = seed_demo.mcp_pair(seed_demo.time.time() - 3600, rng)

    assert agent["trace_id"] == server["trace_id"]
    client_span = next(s for s in agent["spans"] if s["name"] == "create_issue")
    assert server["spans"][0]["remote_parent_id"] == client_span["span_id"]
    assert server["spans"][0]["service"] == "github"


async def test_seeded_runs_are_accepted_by_the_server(client, rng):
    """Generated payloads must satisfy the real ingest schema, not a guess."""
    for run in seed_demo.build_runs(6, days=2, rng=rng):
        response = await client.post("/api/ingest/run", json=run)
        assert response.status_code == 201, response.text

    listed = (await client.get("/api/runs?limit=50")).json()
    assert len(listed) >= 4
    assert all(r["span_count"] > 0 for r in listed)


async def test_seeded_mcp_run_stitches_in_the_server(client, rng):
    agent, server = seed_demo.mcp_pair(seed_demo.time.time() - 600, rng)
    await client.post("/api/ingest/run", json=server)
    await client.post("/api/ingest/run", json=agent)

    merged = (await client.get(f"/api/runs/{agent['run_id']}")).json()
    assert "github_api_post" in [s["name"] for s in merged["spans"]]
    assert merged["metadata"]["grafted_spans"] >= 1

    # the server's run stays out of the top-level list
    assert "github.create_issue" not in {r["name"] for r in (await client.get("/api/runs")).json()}

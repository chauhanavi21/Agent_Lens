"""
Cross-run analytics and the derived span index.

The index is duplicated data, which is only safe if it stays in step with
the runs it mirrors — so most of these check consistency after the four
operations that change a run's spans, not just that the maths is right.
"""

import pytest
from agentlens_server.analytics import (
    find_outliers,
    percentile,
    span_rows_for_run,
    summarize,
)

pytestmark = pytest.mark.anyio


# --- pure aggregation ----------------------------------------------------- #


def test_percentile_interpolates():
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert percentile(values, 0.5) == 30.0
    assert percentile(values, 0.0) == 10.0
    assert percentile(values, 1.0) == 50.0
    # between two samples rather than snapping to one, so a p95 doesn't
    # jump in coarse steps when a single run is added
    assert 40.0 < percentile(values, 0.9) < 50.0

    assert percentile([], 0.5) is None
    assert percentile([7.0], 0.99) == 7.0


def test_span_rows_stay_narrow():
    """The index is only useful if it's small enough to scan."""
    run = {
        "run_id": "r1",
        "name": "agent",
        "spans": [
            {
                "span_id": "s1",
                "name": "search",
                "kind": "tool",
                "status": "success",
                "started_at": 1.0,
                "duration_ms": 120.0,
                "inputs": "x" * 5000,
                "outputs": "y" * 5000,
                "attributes": {"big": "z" * 5000},
                "llm": None,
            }
        ],
    }
    row = span_rows_for_run(run)[0]
    assert "inputs" not in row and "outputs" not in row and "attributes" not in row
    assert row["name"] == "search"


def test_summarize_computes_rates_and_percentiles():
    rows = [
        {
            "span_id": f"s{i}",
            "run_id": f"r{i}",
            "name": "search",
            "kind": "tool",
            "status": "error" if i < 2 else "success",
            "duration_ms": float(i * 100),
            "total_tokens": 10,
            "cost_usd": 0.001,
            "is_retry": i == 1,
        }
        for i in range(10)
    ]
    stats = summarize(rows)[0]

    assert stats["calls"] == 10
    assert stats["errors"] == 2
    assert stats["error_rate"] == 0.2
    assert stats["retry_rate"] == 0.1
    assert stats["runs"] == 10
    assert stats["total_tokens"] == 100
    assert stats["p50_ms"] < stats["p95_ms"] <= stats["max_ms"]


def test_summarize_orders_by_total_time():
    """Where the wall clock actually goes is the reason to open this view."""
    rows = [
        {
            "span_id": "a",
            "run_id": "r",
            "name": "fast",
            "kind": "tool",
            "status": "success",
            "duration_ms": 10.0,
            "total_tokens": 0,
            "cost_usd": 0,
            "is_retry": False,
        },
        {
            "span_id": "b",
            "run_id": "r",
            "name": "slow",
            "kind": "llm",
            "status": "success",
            "duration_ms": 5000.0,
            "total_tokens": 0,
            "cost_usd": 0,
            "is_retry": False,
        },
    ]
    assert [s["name"] for s in summarize(rows)] == ["slow", "fast"]


def test_summarize_handles_unfinished_spans():
    rows = [
        {
            "span_id": "a",
            "run_id": "r",
            "name": "running",
            "kind": "tool",
            "status": "running",
            "duration_ms": None,
            "total_tokens": 0,
            "cost_usd": 0,
            "is_retry": False,
        }
    ]
    stats = summarize(rows)[0]
    assert stats["calls"] == 1
    assert stats["p95_ms"] is None


def test_outliers_point_at_specific_runs():
    rows = [
        {
            "span_id": f"s{i}",
            "run_id": f"r{i}",
            "name": "search",
            "kind": "tool",
            "status": "success",
            "duration_ms": 100.0,
            "started_at": i,
            "total_tokens": 0,
            "cost_usd": 0,
            "is_retry": False,
        }
        for i in range(20)
    ]
    rows.append(
        {
            "span_id": "slow",
            "run_id": "r_bad",
            "name": "search",
            "kind": "tool",
            "status": "success",
            "duration_ms": 9000.0,
            "started_at": 99,
            "total_tokens": 0,
            "cost_usd": 0,
            "is_retry": False,
        }
    )

    found = find_outliers(rows, summarize(rows))
    assert found[0]["run_id"] == "r_bad"
    assert found[0]["times_p95"] > 10


# --- index consistency ---------------------------------------------------- #


async def test_ingest_populates_the_index(client, make_run):
    run = make_run()
    await client.post("/api/ingest/run", json=run)

    health = (await client.get("/api/analytics/health")).json()
    assert health["indexed_spans"] == len(run["spans"])
    assert health["stale"] is False


async def test_reingest_replaces_rather_than_accumulates(client, make_run):
    """
    A re-ingested run can have fewer spans than before, so the index has to
    delete-then-insert. An upsert would leave the extras behind forever.
    """
    run = make_run()
    await client.post("/api/ingest/run", json=run)
    first = (await client.get("/api/analytics/health")).json()["indexed_spans"]

    run["spans"] = run["spans"][:1]
    await client.post("/api/ingest/run", json=run)
    second = (await client.get("/api/analytics/health")).json()["indexed_spans"]

    assert second == 1, f"index kept stale rows: {first} → {second}"


async def test_deleting_a_run_removes_its_index_rows(client, make_run):
    run = make_run()
    await client.post("/api/ingest/run", json=run)
    await client.delete(f"/api/runs/{run['run_id']}")

    assert (await client.get("/api/analytics/health")).json()["indexed_spans"] == 0
    # and the analytics no longer count a run that isn't there
    assert (await client.get("/api/analytics/spans")).json()["stats"] == []


async def test_pruning_removes_index_rows(client, make_run):
    for _ in range(3):
        await client.post("/api/ingest/run", json=make_run())
    before = (await client.get("/api/analytics/health")).json()["indexed_spans"]

    await client.post("/api/runs/prune", json={"max_runs_per_agent": 1, "dry_run": False})
    after = (await client.get("/api/analytics/health")).json()["indexed_spans"]

    assert after < before
    assert after == before // 3


async def test_reindex_rebuilds_from_the_runs_table(client, make_run):
    """
    The index is derived, so rebuilding is always safe — that's the payoff
    for duplicating the data instead of migrating the schema.
    """
    for _ in range(3):
        await client.post("/api/ingest/run", json=make_run())
    expected = (await client.get("/api/analytics/health")).json()["indexed_spans"]

    result = (await client.post("/api/analytics/reindex")).json()
    assert result["runs"] == 3
    assert result["spans"] == expected
    assert (await client.get("/api/analytics/health")).json()["indexed_spans"] == expected


# --- endpoints ------------------------------------------------------------ #


async def test_span_stats_answers_the_cross_run_question(client, make_run):
    for _ in range(4):
        await client.post("/api/ingest/run", json=make_run())
    await client.post("/api/ingest/run", json=make_run(fail=True))

    result = (await client.get("/api/analytics/spans")).json()
    by_name = {s["name"]: s for s in result["stats"]}

    assert by_name["web_search"]["calls"] == 5
    assert by_name["web_search"]["error_rate"] == 0.0
    assert by_name["summarize"]["errors"] == 1
    assert by_name["summarize"]["error_rate"] == 0.2
    assert result["sample_capped"] is False


async def test_span_stats_filters(client, make_run):
    await client.post("/api/ingest/run", json=make_run(name="agent_a"))
    await client.post("/api/ingest/run", json=make_run(name="agent_b"))

    scoped = (await client.get("/api/analytics/spans?agent=agent_a")).json()
    assert all(s["calls"] == 1 for s in scoped["stats"])

    tools = (await client.get("/api/analytics/spans?kind=tool")).json()
    assert {s["kind"] for s in tools["stats"]} == {"tool"}


async def test_window_excludes_older_spans(client, make_run):
    import time

    old = make_run()
    for span in old["spans"]:
        span["started_at"] = time.time() - 60 * 86400
    await client.post("/api/ingest/run", json=old)
    await client.post("/api/ingest/run", json=make_run())

    recent = (await client.get("/api/analytics/spans?days=7")).json()
    assert recent["spans_examined"] == len(old["spans"])  # only the fresh run's spans

    everything = (await client.get("/api/analytics/spans?days=3650")).json()
    assert everything["spans_examined"] > recent["spans_examined"]


async def test_model_stats_report_cost(client, make_run):
    run = make_run()
    run["spans"][2]["llm"] = {
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "cost_usd": 0.0075,
        "prompt_preview": "",
        "response_preview": "",
        "temperature": None,
    }
    await client.post("/api/ingest/run", json=run)

    result = (await client.get("/api/analytics/models")).json()
    assert result["total_cost_usd"] == 0.0075
    assert result["stats"][0]["model"] == "gpt-4o"
    assert result["stats"][0]["total_tokens"] == 1500


async def test_outlier_endpoint(client, make_run):
    for _ in range(5):
        await client.post("/api/ingest/run", json=make_run())

    slow = make_run()
    slow["spans"][1]["duration_ms"] = 60_000
    await client.post("/api/ingest/run", json=slow)

    outliers = (await client.get("/api/analytics/outliers")).json()["outliers"]
    assert outliers
    assert outliers[0]["run_id"] == slow["run_id"]


async def test_analytics_on_an_empty_store(client):
    """No runs must be an empty answer, not a crash."""
    result = (await client.get("/api/analytics/spans")).json()
    assert result["stats"] == []
    assert (await client.get("/api/analytics/models")).json()["total_cost_usd"] == 0
    assert (await client.get("/api/analytics/outliers")).json()["outliers"] == []


async def test_cost_total_admits_what_it_does_not_cover(client, make_run):
    """
    The failure this guards against: an unpriced model contributes 0.00 to
    the total, so the dashboard shows a confident number that silently
    excludes real spend.
    """
    priced = make_run()
    priced["spans"][2]["llm"] = {
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "cost_usd": 0.0075,
        "cost_source": "table",
        "prompt_preview": "",
        "response_preview": "",
        "temperature": None,
    }
    await client.post("/api/ingest/run", json=priced)

    unpriced = make_run()
    unpriced["spans"][2]["llm"] = {
        "model": "mystery-model-v9",
        "provider": "",
        "input_tokens": 8000,
        "output_tokens": 2000,
        "total_tokens": 10000,
        "cost_usd": 0.0,
        "cost_source": "unpriced",
        "prompt_preview": "",
        "response_preview": "",
        "temperature": None,
    }
    await client.post("/api/ingest/run", json=unpriced)

    result = (await client.get("/api/analytics/models")).json()

    assert result["total_cost_usd"] == 0.0075
    assert result["unpriced_models"] == ["mystery-model-v9"]
    assert result["unpriced_tokens"] == 10000
    # the total only covers 1500 of 11500 tokens, and says so
    assert result["cost_coverage"] < 0.15
    assert "not included in this total" in result["warning"]


async def test_fully_priced_totals_carry_no_warning(client, make_run):
    run = make_run()
    run["spans"][2]["llm"] = {
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 1000,
        "output_tokens": 500,
        "total_tokens": 1500,
        "cost_usd": 0.0075,
        "cost_source": "table",
        "prompt_preview": "",
        "response_preview": "",
        "temperature": None,
    }
    await client.post("/api/ingest/run", json=run)

    result = (await client.get("/api/analytics/models")).json()
    assert result["warning"] is None
    assert result["unpriced_models"] == []
    assert result["cost_coverage"] == 1.0


async def test_old_runs_without_cost_source_still_work(client, make_run):
    """Runs traced before this field existed must not break analytics."""
    run = make_run()
    run["spans"][2]["llm"] = {
        "model": "gpt-4o",
        "provider": "openai",
        "input_tokens": 100,
        "output_tokens": 50,
        "total_tokens": 150,
        "cost_usd": 0.001,
        "prompt_preview": "",
        "response_preview": "",
        "temperature": None,
    }
    assert (await client.post("/api/ingest/run", json=run)).status_code == 201

    result = (await client.get("/api/analytics/models")).json()
    # a legacy run with a real cost is treated as priced, not as a gap
    assert result["total_cost_usd"] == 0.001
    assert result["unpriced_models"] == []

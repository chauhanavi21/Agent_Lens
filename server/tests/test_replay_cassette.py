import pytest

pytestmark = pytest.mark.anyio


async def test_cassette_endpoint_returns_recorded_calls(client, make_run):
    run = make_run()
    await client.post("/api/ingest/run", json=run)

    cassette = (await client.get(f"/api/runs/{run['run_id']}/cassette")).json()
    assert cassette["run_id"] == run["run_id"]
    # tool and llm spans are recorded; the agent span runs for real on replay
    assert set(cassette["calls"]) == {"web_search", "summarize"}
    assert cassette["metadata"]["recorded_spans"] == 2


async def test_cassette_without_full_outputs_is_flagged(client, make_run):
    """Runs traced without record_outputs fall back to truncated previews."""
    run = make_run()
    await client.post("/api/ingest/run", json=run)
    cassette = (await client.get(f"/api/runs/{run['run_id']}/cassette")).json()
    assert all(c["truncated"] for calls in cassette["calls"].values() for c in calls)


async def test_cassette_for_missing_run_is_404(client):
    assert (await client.get("/api/runs/nope/cassette")).status_code == 404

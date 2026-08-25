import pytest

pytestmark = pytest.mark.anyio


async def test_health(client):
    assert (await client.get("/api/health")).json() == {"status": "ok"}


async def test_ingest_then_read_back(client, make_run):
    run = make_run()
    assert (await client.post("/api/ingest/run", json=run)).status_code == 201

    stored = (await client.get(f"/api/runs/{run['run_id']}")).json()
    assert stored["name"] == "qa_agent"
    assert [s["name"] for s in stored["spans"]] == ["qa_agent", "web_search", "summarize"]
    assert stored["spans"][1]["parent_id"] == stored["spans"][0]["span_id"]
    assert stored["scores"][0]["name"] == "faithfulness"


async def test_ingest_is_idempotent(client, make_run):
    run = make_run()
    for _ in range(3):
        assert (await client.post("/api/ingest/run", json=run)).status_code == 201
    # re-posting a run updates it rather than creating duplicates
    assert len((await client.get("/api/runs")).json()) == 1


async def test_run_filters(client, make_run):
    await client.post("/api/ingest/run", json=make_run(name="alpha", tags=["prod"]))
    await client.post("/api/ingest/run", json=make_run(name="beta", tags=["dev"], fail=True))

    assert len((await client.get("/api/runs?status=error")).json()) == 1
    assert len((await client.get("/api/runs?name=alph")).json()) == 1
    assert len((await client.get("/api/runs?tag=prod")).json()) == 1
    assert len((await client.get("/api/runs?limit=1")).json()) == 1


async def test_missing_run_is_404(client):
    r = await client.get("/api/runs/does-not-exist")
    assert r.status_code == 404
    assert "not found" in r.json()["detail"].lower()


async def test_stats_roll_up(client, make_run):
    await client.post("/api/ingest/run", json=make_run(name="a"))
    await client.post("/api/ingest/run", json=make_run(name="b", fail=True))

    stats = (await client.get("/api/runs/stats")).json()
    assert stats["total_runs"] == 2
    assert stats["by_status"] == {"success": 1, "error": 1}

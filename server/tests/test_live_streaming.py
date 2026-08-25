import time

import pytest
from agentlens_server.live import LiveStore, sse

pytestmark = pytest.mark.anyio


def _span_event(run_id, span_id, name, kind="tool", type_="span_start"):
    return {
        "type": type_,
        "run_id": run_id,
        "ts": time.time(),
        "span": {
            "span_id": span_id,
            "parent_id": None,
            "name": name,
            "kind": kind,
            "status": "running",
            "started_at": time.time(),
        },
    }


async def test_live_store_folds_events_into_a_run():
    store = LiveStore()
    await store.apply(
        {
            "type": "run_start",
            "run_id": "r1",
            "run": {
                "run_id": "r1",
                "name": "agent",
                "tags": [],
                "status": "running",
                "started_at": time.time(),
            },
        }
    )
    await store.apply(_span_event("r1", "s1", "search"))
    await store.apply(_span_event("r1", "s1", "search", type_="span_end"))

    live = store.snapshot()
    assert len(live) == 1
    # span_end replaces span_start rather than appending a duplicate
    assert live[0]["span_count"] == 1


async def test_events_before_run_start_are_kept():
    """A browser can connect mid-run; dropping these would show a broken DAG."""
    store = LiveStore()
    await store.apply(_span_event("ghost", "s1", "orphan"))
    assert any(r["run_id"] == "ghost" for r in store.snapshot())


async def test_run_end_clears_live_state():
    store = LiveStore()
    await store.apply(
        {
            "type": "run_start",
            "run_id": "r1",
            "run": {"run_id": "r1", "name": "a", "tags": [], "status": "running", "started_at": time.time()},
        }
    )
    await store.apply({"type": "run_end", "run_id": "r1", "run": {"run_id": "r1"}})
    assert store.snapshot() == []


def test_sse_frame_format():
    frame = sse({"type": "span_start", "run_id": "r1"})
    assert frame.startswith("event: span_start\n")
    assert frame.endswith("\n\n")
    assert '"run_id": "r1"' in frame


async def test_event_endpoint_accepts_and_rejects(client):
    ok = await client.post("/api/ingest/event", json=_span_event("r1", "s1", "search"))
    assert ok.status_code == 202

    bad = await client.post("/api/ingest/event", json={"type": "nonsense", "run_id": "r1"})
    assert bad.status_code == 422


async def test_live_run_is_readable_before_it_finishes(client):
    await client.post(
        "/api/ingest/event",
        json={
            "type": "run_start",
            "run_id": "live1",
            "run": {
                "run_id": "live1",
                "name": "slow_agent",
                "tags": [],
                "status": "running",
                "started_at": time.time(),
            },
        },
    )
    await client.post("/api/ingest/event", json=_span_event("live1", "s1", "search"))

    listed = (await client.get("/api/live/runs")).json()["runs"]
    assert any(r["run_id"] == "live1" for r in listed)

    partial = (await client.get("/api/runs/live1")).json()
    assert partial["live"] is True
    assert partial["spans"][0]["name"] == "search"

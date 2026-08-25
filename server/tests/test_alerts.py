import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def webhook():
    """A real HTTP receiver — the dispatcher uses urllib, so mocking it
    would test the mock rather than the wire behaviour."""
    received = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            received.append(json.loads(self.rfile.read(length)))
            self.send_response(200)
            self.end_headers()

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}/hook", received
    server.shutdown()


async def test_rule_crud(client, webhook):
    url, _ = webhook
    created = await client.post(
        "/api/alerts/rules",
        json={"name": "any failure", "field": "status", "op": "eq", "value": "error", "webhook_url": url},
    )
    assert created.status_code == 201
    rule_id = created.json()["id"]

    assert len((await client.get("/api/alerts/rules")).json()) == 1

    patched = await client.patch(
        f"/api/alerts/rules/{rule_id}",
        json={
            "name": "any failure",
            "field": "status",
            "op": "eq",
            "value": "error",
            "webhook_url": url,
            "enabled": False,
        },
    )
    assert patched.json()["enabled"] is False

    assert (await client.delete(f"/api/alerts/rules/{rule_id}")).status_code == 204
    assert (await client.delete(f"/api/alerts/rules/{rule_id}")).status_code == 404


async def test_invalid_rule_is_rejected_with_a_usable_message(client, webhook):
    url, _ = webhook
    r = await client.post(
        "/api/alerts/rules",
        json={"name": "bad", "field": "nonsense", "op": "gt", "value": "1", "webhook_url": url},
    )
    assert r.status_code == 422
    assert "Unknown field" in r.json()["detail"]

    r = await client.post(
        "/api/alerts/rules",
        json={
            "name": "bad",
            "field": "total_cost_usd",
            "op": "gt",
            "value": "not-a-number",
            "webhook_url": url,
        },
    )
    assert r.status_code == 422


async def test_alert_fires_on_matching_run(client, make_run, webhook):
    url, received = webhook
    await client.post(
        "/api/alerts/rules",
        json={"name": "any failure", "field": "status", "op": "eq", "value": "error", "webhook_url": url},
    )

    await client.post("/api/ingest/run", json=make_run(fail=True))
    await asyncio.sleep(0.6)

    assert len(received) == 1
    assert received[-1]["alert"]["status"] == "error"

    events = (await client.get("/api/alerts/events")).json()
    assert events[0]["delivered"] is True


async def test_successful_run_does_not_fire(client, make_run, webhook):
    url, received = webhook
    await client.post(
        "/api/alerts/rules",
        json={"name": "any failure", "field": "status", "op": "eq", "value": "error", "webhook_url": url},
    )

    await client.post("/api/ingest/run", json=make_run())
    await asyncio.sleep(0.5)
    assert received == []


async def test_broken_webhook_never_fails_ingest(client, make_run):
    """A dead endpoint is recorded as a failed delivery, not an ingest error."""
    await client.post(
        "/api/alerts/rules",
        json={
            "name": "broken",
            "field": "status",
            "op": "eq",
            "value": "error",
            "webhook_url": "http://127.0.0.1:9/nope",
        },
    )

    r = await client.post("/api/ingest/run", json=make_run(fail=True))
    assert r.status_code == 201
    await asyncio.sleep(0.8)

    events = (await client.get("/api/alerts/events")).json()
    assert events[0]["delivered"] is False
    assert events[0]["delivery_error"]


async def test_score_based_alert(client, make_run, webhook):
    url, received = webhook
    await client.post(
        "/api/alerts/rules",
        json={
            "name": "low faithfulness",
            "field": "score:faithfulness",
            "op": "lt",
            "value": "0.85",
            "webhook_url": url,
        },
    )

    await client.post("/api/ingest/run", json=make_run(faithfulness=0.55))
    await asyncio.sleep(0.6)
    assert len(received) == 1
    assert "faithfulness" in received[-1]["alert"]["reason"]

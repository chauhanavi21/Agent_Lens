"""
Transport-level integration tests.

The UI suite runs in jsdom, which fakes `EventSource` and ignores CORS
entirely — so the two things most likely to break a real browser are exactly
the two things it can't see. These run a **real server in a real process**
and talk to it over real HTTP.

This is not a browser test: nothing here renders a DOM. It covers the wire
between the browser and the server, which is where the failures jsdom hides
actually live.
"""

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.anyio


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class LiveServer:
    """A real uvicorn process, not an ASGI shim."""

    def __init__(self, env_overrides=None, tmp_path=None):
        self.port = free_port()
        self.base = f"http://127.0.0.1:{self.port}"
        db = (tmp_path or Path("/tmp")) / f"transport_{self.port}.db"

        env = {
            **os.environ,
            "DATABASE_URL": f"sqlite+aiosqlite:///{db}",
            "PYTHONPATH": f"{ROOT / 'server'}{os.pathsep}{ROOT / 'sdk'}",
            **(env_overrides or {}),
        }
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "uvicorn",
                "agentlens_server.main:app",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--log-level",
                "error",
            ],
            env=env,
            cwd=str(ROOT / "server"),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def wait(self, timeout=30):
        deadline = time.time() + timeout
        while time.time() < deadline:
            if self.process.poll() is not None:
                err = self.process.stderr.read().decode()[:2000]
                raise RuntimeError(f"server exited early:\n{err}")
            try:
                if requests.get(f"{self.base}/api/health", timeout=1).ok:
                    return self
            except requests.RequestException:
                time.sleep(0.2)
        raise RuntimeError("server did not become healthy")

    def stop(self):
        self.process.terminate()
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    s = LiveServer(
        {"CORS_ORIGINS": "http://localhost:5173,https://agentlens.example.com"},
        tmp_path_factory.mktemp("transport"),
    ).wait()
    yield s
    s.stop()


@pytest.fixture(scope="module")
def secured_server(tmp_path_factory):
    s = LiveServer(
        {"AGENTLENS_API_KEY": "secret-key-123", "CORS_ORIGINS": "http://localhost:5173"},
        tmp_path_factory.mktemp("secured"),
    ).wait()
    yield s
    s.stop()


def next_event(lines, match=None, timeout=10):
    """
    Read SSE frames until one matches.

    An SSE frame is several lines (`event:`, `data:`, blank), so consuming a
    single line leaves you mid-frame — which silently hands you the wrong
    event. This parses whole frames and skips the ones you didn't ask for.
    """
    deadline = time.time() + timeout
    for line in lines:
        if time.time() > deadline:
            break
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        if match is None or match(event):
            return event
    return None


def sample_run(run_id="t1", name="transport_agent"):
    now = time.time()
    return {
        "run_id": run_id,
        "trace_id": run_id + "trace",
        "name": name,
        "tags": ["transport"],
        "status": "success",
        "started_at": now,
        "ended_at": now + 1,
        "duration_ms": 1000,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "error": None,
        "metadata": {},
        "scores": [],
        "spans": [
            {
                "span_id": "s1",
                "parent_id": None,
                "name": name,
                "kind": "agent",
                "status": "success",
                "started_at": now,
                "ended_at": now + 1,
                "duration_ms": 1000,
                "inputs": "",
                "outputs": "",
                "error": None,
                "retry_of": None,
                "remote_parent_id": None,
                "service": None,
                "llm": None,
                "attributes": {},
            }
        ],
    }


# --- CORS ----------------------------------------------------------------- #


def test_preflight_allows_the_configured_origin(server):
    """
    jsdom never issues a preflight, so a CORS misconfiguration passes every
    UI test and then breaks every real browser.
    """
    response = requests.options(
        f"{server.base}/api/runs",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "GET",
        },
        timeout=10,
    )
    assert response.status_code in (200, 204), response.text
    assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"


def test_preflight_covers_the_methods_the_ui_uses(server):
    for method in ("POST", "DELETE", "PATCH"):
        response = requests.options(
            f"{server.base}/api/runs",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": method,
                "Access-Control-Request-Headers": "content-type",
            },
            timeout=10,
        )
        assert response.status_code in (200, 204), f"{method} preflight failed"
        allowed = response.headers.get("access-control-allow-methods", "")
        assert method in allowed or "*" in allowed


def test_an_unlisted_origin_is_not_granted_access(server):
    response = requests.get(
        f"{server.base}/api/runs",
        headers={"Origin": "https://evil.example.com"},
        timeout=10,
    )
    # the request itself succeeds; the browser blocks it because the header
    # doesn't name the origin — which is exactly what must not be present
    assert response.headers.get("access-control-allow-origin") != "https://evil.example.com"


def test_a_second_configured_origin_also_works(server):
    response = requests.get(
        f"{server.base}/api/runs",
        headers={"Origin": "https://agentlens.example.com"},
        timeout=10,
    )
    assert response.headers.get("access-control-allow-origin") == "https://agentlens.example.com"


# --- SSE ------------------------------------------------------------------ #


def test_sse_streams_incrementally_rather_than_buffering(server):
    """
    The failure this catches: a proxy or middleware that buffers the
    response. Every event still arrives — at the end, all at once — so a
    buffered stream passes a naive test and shows a frozen UI.
    """
    received = []
    started = time.time()

    with requests.get(f"{server.base}/api/stream", stream=True, timeout=20) as response:
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        # the header that tells nginx not to buffer this response
        assert response.headers.get("x-accel-buffering") == "no"
        assert response.headers.get("cache-control") == "no-cache"

        lines = response.iter_lines(decode_unicode=True)

        # the connected frame must arrive before anything is published
        for line in lines:
            if line.startswith("data: "):
                received.append(json.loads(line[6:]))
                break
        assert received[0]["type"] == "connected"
        assert time.time() - started < 10, "the opening frame was buffered"

        # publish an event and require it to arrive promptly
        publish_time = time.time()
        requests.post(
            f"{server.base}/api/ingest/event",
            json={
                "type": "span_start",
                "run_id": "live-1",
                "ts": time.time(),
                "span": {
                    "span_id": "sp1",
                    "parent_id": None,
                    "name": "search",
                    "kind": "tool",
                    "status": "running",
                    "started_at": time.time(),
                },
            },
            timeout=10,
        )

        for line in lines:
            if line.startswith("data: "):
                event = json.loads(line[6:])
                if event["type"] == "span_start":
                    latency = time.time() - publish_time
                    assert latency < 5, f"event took {latency:.1f}s — the stream is buffered"
                    assert event["span"]["name"] == "search"
                    return
    pytest.fail("the published event never arrived over the stream")


def test_sse_named_events_carry_a_type(server):
    """The UI subscribes with addEventListener, so the event name matters."""
    with requests.get(f"{server.base}/api/stream", stream=True, timeout=20) as response:
        lines = response.iter_lines(decode_unicode=True)
        assert next_event(lines, lambda e: e["type"] == "connected")

        requests.post(
            f"{server.base}/api/ingest/event",
            json={
                "type": "run_end",
                "run_id": "live-2",
                "run": {"run_id": "live-2"},
            },
            timeout=10,
        )

        saw_named_line = False
        deadline = time.time() + 10
        for line in lines:
            if time.time() > deadline:
                break
            if line.startswith("event: run_end"):
                saw_named_line = True
            elif saw_named_line and line.startswith("data: "):
                assert json.loads(line[6:])["type"] == "run_end"
                return
    pytest.fail("no named run_end frame was sent")


def test_sse_filters_by_run_over_the_wire(server):
    with requests.get(f"{server.base}/api/stream?run_id=wanted", stream=True, timeout=20) as response:
        lines = response.iter_lines(decode_unicode=True)
        assert next_event(lines, lambda e: e["type"] == "connected")

        for run_id in ("ignored", "wanted"):
            requests.post(
                f"{server.base}/api/ingest/event",
                json={
                    "type": "span_start",
                    "run_id": run_id,
                    "ts": time.time(),
                    "span": {
                        "span_id": f"s-{run_id}",
                        "parent_id": None,
                        "name": run_id,
                        "kind": "tool",
                        "status": "running",
                        "started_at": time.time(),
                    },
                },
                timeout=10,
            )

        event = next_event(lines, lambda e: e.get("type") == "span_start")
        assert event is not None, "no event arrived on the filtered stream"
        assert event["run_id"] == "wanted", "the filter leaked another run's events"


def test_several_subscribers_all_receive_an_event(server):
    """Fan-out is a set of queues; a bug there silently starves one browser."""
    streams = [requests.get(f"{server.base}/api/stream", stream=True, timeout=20) for _ in range(3)]
    try:
        iterators = []
        for stream in streams:
            it = stream.iter_lines(decode_unicode=True)
            assert next_event(it, lambda e: e["type"] == "connected"), "no opening frame"
            iterators.append(it)

        requests.post(
            f"{server.base}/api/ingest/event",
            json={
                "type": "span_start",
                "run_id": "fanout",
                "ts": time.time(),
                "span": {
                    "span_id": "fo",
                    "parent_id": None,
                    "name": "fanout",
                    "kind": "tool",
                    "status": "running",
                    "started_at": time.time(),
                },
            },
            timeout=10,
        )

        for index, it in enumerate(iterators):
            event = next_event(it, lambda e: e.get("run_id") == "fanout")
            assert event is not None, f"subscriber {index} received nothing"
    finally:
        for stream in streams:
            stream.close()


def test_a_disconnecting_subscriber_does_not_break_the_others(server):
    """A closed browser tab must not take the stream down for everyone else."""
    doomed = requests.get(f"{server.base}/api/stream", stream=True, timeout=20)
    next_event(doomed.iter_lines(decode_unicode=True), lambda e: e["type"] == "connected")
    doomed.close()

    time.sleep(0.5)
    assert requests.get(f"{server.base}/api/health", timeout=5).ok

    with requests.get(f"{server.base}/api/stream", stream=True, timeout=20) as survivor:
        it = survivor.iter_lines(decode_unicode=True)
        assert next_event(it, lambda e: e["type"] == "connected")

        requests.post(
            f"{server.base}/api/ingest/event",
            json={
                "type": "run_end",
                "run_id": "after-disconnect",
                "run": {"run_id": "after-disconnect"},
            },
            timeout=10,
        )

        event = next_event(it, lambda e: e.get("run_id") == "after-disconnect")
        assert event is not None, "the surviving subscriber got nothing"


# --- auth over the wire ---------------------------------------------------- #


def test_api_key_is_enforced_on_a_real_request(secured_server):
    unauthorized = requests.post(
        f"{secured_server.base}/api/ingest/run", json=sample_run("auth-1"), timeout=10
    )
    assert unauthorized.status_code == 401

    authorized = requests.post(
        f"{secured_server.base}/api/ingest/run",
        json=sample_run("auth-2"),
        headers={"Authorization": "Bearer secret-key-123"},
        timeout=10,
    )
    assert authorized.status_code == 201


def test_the_wrong_key_is_rejected(secured_server):
    response = requests.post(
        f"{secured_server.base}/api/ingest/run",
        json=sample_run("auth-3"),
        headers={"Authorization": "Bearer wrong"},
        timeout=10,
    )
    assert response.status_code == 401


def test_streaming_events_require_the_key_too(secured_server):
    """An unauthenticated event feed would let anyone forge live runs."""
    response = requests.post(
        f"{secured_server.base}/api/ingest/event",
        json={"type": "run_end", "run_id": "x", "run": {}},
        timeout=10,
    )
    assert response.status_code == 401


# --- the real SDK over the real wire --------------------------------------- #


def test_the_sdk_reaches_a_real_server(server):
    """
    Everything else in the suite posts hand-built JSON. This proves the
    actual exporter — background thread, urllib, real socket — lands a run.
    """
    sys.path.insert(0, str(ROOT / "sdk"))
    from agentlens import AgentLens, HttpExporter, SpanKind

    exporter = HttpExporter(server.base)
    lens = AgentLens(exporter=exporter)

    @lens.tool("web_search")
    def web_search(q):
        return ["doc"]

    @lens.span("summarize", kind=SpanKind.LLM)
    def summarize(docs):
        return "summary"

    @lens.trace("wire_agent", tags=["transport"])
    def agent(q):
        return summarize(web_search(q))

    agent("query")
    exporter.flush()

    deadline = time.time() + 10
    while time.time() < deadline:
        runs = requests.get(f"{server.base}/api/runs?name=wire_agent", timeout=5).json()
        if runs:
            assert runs[0]["span_count"] == 3
            return
        time.sleep(0.3)
    pytest.fail("the SDK's run never arrived over a real socket")

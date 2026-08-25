"""
Live streaming: push span lifecycle events as they happen instead of one
payload at the end.

A long agent run is exactly when you most want to watch it — and a run that
hangs or gets killed never reaches its final export at all, so batch-only
tracing loses precisely the runs worth debugging. StreamExporter emits an
event when each span opens and closes, then the complete run at the end.

    lens = AgentLens(exporter=StreamExporter("http://localhost:7430"))

Events are best-effort: they're dropped rather than queued indefinitely if
the server is unreachable, and the final run export is the source of truth.
That way a streaming outage costs you the live view, not the data.
"""

from __future__ import annotations

import json
import queue
import threading
import time
import urllib.request
from typing import Any, Optional

from .models import AgentRun, Span


def run_start_event(run: AgentRun) -> dict[str, Any]:
    return {
        "type": "run_start",
        "ts": time.time(),
        "run_id": run.run_id,
        "trace_id": run.trace_id,
        "run": {
            "run_id": run.run_id,
            "trace_id": run.trace_id,
            "name": run.name,
            "tags": run.tags,
            "status": "running",
            "started_at": run.started_at,
        },
    }


def span_event(run: AgentRun, span: Span, kind: str) -> dict[str, Any]:
    return {
        "type": kind,  # span_start | span_end
        "ts": time.time(),
        "run_id": run.run_id,
        "trace_id": run.trace_id,
        "span": span.to_dict(),
    }


def run_end_event(run: AgentRun) -> dict[str, Any]:
    return {"type": "run_end", "ts": time.time(), "run_id": run.run_id, "run": run.to_dict()}


class StreamExporter:
    """
    Posts lifecycle events to /api/ingest/event as they occur and the full
    run to /api/ingest/run at the end.

    `max_queue` bounds memory: if the server can't keep up, events are
    dropped oldest-first rather than growing without limit inside the
    agent's process.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:7430",
        api_key: Optional[str] = None,
        max_queue: int = 1000,
        timeout: float = 3.0,
    ):
        base = endpoint.rstrip("/")
        self.event_url = base + "/api/ingest/event"
        self.run_url = base + "/api/ingest/run"
        self.api_key = api_key
        self.timeout = timeout
        self.dropped = 0
        self._q: queue.Queue[Optional[tuple[str, dict]]] = queue.Queue(maxsize=max_queue)
        threading.Thread(target=self._drain, daemon=True, name="agentlens-stream").start()

    # -- exporter protocol --------------------------------------------- #

    def export(self, run: AgentRun) -> None:
        self._put((self.run_url, run.to_dict()))

    def export_event(self, event: dict[str, Any]) -> None:
        self._put((self.event_url, event))

    def flush(self, timeout: float = 10.0) -> None:
        deadline = time.time() + timeout
        while not self._q.empty() and time.time() < deadline:
            time.sleep(0.03)

    # ------------------------------------------------------------------ #

    def _put(self, item) -> None:
        try:
            self._q.put_nowait(item)
        except queue.Full:
            # drop the oldest so the newest state still gets through
            try:
                self._q.get_nowait()
                self._q.put_nowait(item)
            except queue.Empty:
                pass
            self.dropped += 1

    def _drain(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            url, payload = item
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            try:
                req = urllib.request.Request(
                    url, data=json.dumps(payload, default=str).encode(), headers=headers, method="POST"
                )
                with urllib.request.urlopen(req, timeout=self.timeout):
                    pass
            except Exception:
                continue  # live view is best-effort; the final run still lands

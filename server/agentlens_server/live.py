"""
Live run state and SSE fan-out.

Two jobs. First, hold partially-complete runs in memory while they execute,
so the UI can show a DAG building itself before the run is over — and so a
run that hangs or dies is still visible, which batch-only tracing never
shows you. Second, fan events out to connected browsers over SSE.

In-memory by design: live state is disposable, and the final run export is
what gets persisted. A multi-process deployment would swap the broker for
Redis pub/sub — the interface is small enough that it's a drop-in.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, AsyncIterator, Optional

# how long a run can sit untouched before it's considered abandoned
STALE_AFTER_SECONDS = 15 * 60
MAX_LIVE_RUNS = 200
SUBSCRIBER_QUEUE_SIZE = 100


class LiveStore:
    """Partially-complete runs, keyed by run_id."""

    def __init__(self) -> None:
        self._runs: dict[str, dict[str, Any]] = {}
        self._lock = asyncio.Lock()

    async def apply(self, event: dict[str, Any]) -> Optional[dict[str, Any]]:
        """Fold one event into live state. Returns the run's current shape."""
        kind = event.get("type")
        run_id = event.get("run_id")
        if not run_id:
            return None

        async with self._lock:
            if kind == "run_start":
                run = dict(event.get("run") or {})
                run.update({"spans": [], "status": "running", "updated_at": time.time()})
                self._runs[run_id] = run
                self._evict_locked()
                return run

            run = self._runs.get(run_id)
            if run is None and kind in ("span_start", "span_end"):
                # events can outrun run_start, or the agent may have started
                # before the server came up — synthesize a shell rather than
                # dropping spans on the floor
                run = {"run_id": run_id, "trace_id": event.get("trace_id"),
                       "name": "(unknown run)", "tags": [], "status": "running",
                       "started_at": event.get("ts", time.time()), "spans": []}
                self._runs[run_id] = run

            if kind in ("span_start", "span_end") and run is not None:
                span = event.get("span") or {}
                spans = run["spans"]
                for i, existing in enumerate(spans):
                    if existing.get("span_id") == span.get("span_id"):
                        spans[i] = span
                        break
                else:
                    spans.append(span)
                run["updated_at"] = time.time()
                return run

            if kind == "run_end":
                finished = self._runs.pop(run_id, None)
                return event.get("run") or finished
        return run

    def _evict_locked(self) -> None:
        now = time.time()
        stale = [k for k, v in self._runs.items() if now - v.get("updated_at", now) > STALE_AFTER_SECONDS]
        for k in stale:
            self._runs.pop(k, None)
        while len(self._runs) > MAX_LIVE_RUNS:
            oldest = min(self._runs, key=lambda k: self._runs[k].get("updated_at", 0))
            self._runs.pop(oldest, None)

    def snapshot(self) -> list[dict[str, Any]]:
        now = time.time()
        return [
            {**r, "span_count": len(r.get("spans") or []),
             "duration_ms": round((now - r["started_at"]) * 1000, 2) if r.get("started_at") else None}
            for r in self._runs.values()
        ]

    def get(self, run_id: str) -> Optional[dict[str, Any]]:
        return self._runs.get(run_id)


class Broker:
    """Fan events out to connected SSE clients."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue] = set()

    async def publish(self, event: dict[str, Any]) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                # a browser that can't keep up loses events rather than
                # stalling the agent that produced them
                pass

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=SUBSCRIBER_QUEUE_SIZE)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


live_store = LiveStore()
broker = Broker()


def sse(event: dict[str, Any]) -> str:
    """Format one SSE frame."""
    return f"event: {event.get('type', 'message')}\ndata: {json.dumps(event, default=str)}\n\n"


async def event_stream(run_id: Optional[str] = None, heartbeat: float = 15.0) -> AsyncIterator[str]:
    """
    Yield SSE frames for a subscriber. Heartbeats keep proxies from closing
    an idle connection; the client treats them as a liveness signal.
    """
    q = broker.subscribe()
    try:
        yield sse({"type": "connected", "ts": time.time(),
                   "live_runs": [r["run_id"] for r in live_store.snapshot()]})
        while True:
            try:
                event = await asyncio.wait_for(q.get(), timeout=heartbeat)
            except asyncio.TimeoutError:
                yield ": heartbeat\n\n"
                continue
            if run_id and event.get("run_id") != run_id:
                continue
            yield sse(event)
    except asyncio.CancelledError:
        raise
    finally:
        broker.unsubscribe(q)

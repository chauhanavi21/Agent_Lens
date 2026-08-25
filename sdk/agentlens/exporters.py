"""
Exporters ship finished AgentRuns off the hot path.
HttpExporter posts to an AgentLens server on a background thread;
FileExporter appends JSONL; ConsoleExporter prints a one-line summary.
"""

from __future__ import annotations

import json
import queue
import threading
import urllib.request
from typing import Optional, Protocol

from .models import AgentRun


class Exporter(Protocol):
    def export(self, run: AgentRun) -> None: ...


class ConsoleExporter:
    def export(self, run: AgentRun) -> None:
        d = run.to_dict()
        print(
            f"[agentlens] run={d['name']} status={d['status']} "
            f"spans={len(d['spans'])} tokens={d['total_tokens']} "
            f"cost=${d['total_cost_usd']:.4f} duration={d['duration_ms']}ms"
        )


class FileExporter:
    """Append each run as one JSON line. Safe for concurrent writers per-process."""

    def __init__(self, path: str):
        self.path = path
        self._lock = threading.Lock()

    def export(self, run: AgentRun) -> None:
        line = json.dumps(run.to_dict(), default=str)
        with self._lock, open(self.path, "a", encoding="utf-8") as f:
            f.write(line + "\n")


class HttpExporter:
    """
    POSTs runs to {endpoint}/api/ingest/run from a daemon thread so the
    agent never blocks on the network. Failed exports are dropped after
    `retries` attempts — observability must not break the observed.
    """

    def __init__(self, endpoint: str, api_key: Optional[str] = None, retries: int = 2, timeout: float = 5.0):
        self.url = endpoint.rstrip("/") + "/api/ingest/run"
        self.api_key = api_key
        self.retries = retries
        self.timeout = timeout
        self._q: queue.Queue[Optional[AgentRun]] = queue.Queue()
        self._worker = threading.Thread(target=self._drain, daemon=True, name="agentlens-exporter")
        self._worker.start()

    def export(self, run: AgentRun) -> None:
        self._q.put(run)

    def flush(self, timeout: float = 10.0) -> None:
        """Block until the queue is empty (useful in tests / short scripts)."""
        import time

        deadline = time.time() + timeout
        while not self._q.empty() and time.time() < deadline:
            time.sleep(0.05)

    def _drain(self) -> None:
        while True:
            run = self._q.get()
            if run is None:
                return
            body = json.dumps(run.to_dict(), default=str).encode()
            headers = {"Content-Type": "application/json"}
            if self.api_key:
                headers["Authorization"] = f"Bearer {self.api_key}"
            for _ in range(self.retries + 1):
                try:
                    req = urllib.request.Request(self.url, data=body, headers=headers, method="POST")
                    with urllib.request.urlopen(req, timeout=self.timeout):
                        break
                except Exception:
                    continue

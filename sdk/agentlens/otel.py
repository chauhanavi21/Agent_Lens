"""
OTLP exporter: ship AgentLens runs to any OpenTelemetry backend.

    from agentlens import AgentLens
    from agentlens.otel import OTLPExporter

    lens = AgentLens(exporter=OTLPExporter("http://localhost:4318"))

Speaks OTLP/HTTP with JSON encoding, hand-rolled over urllib, so the SDK
keeps its zero-dependency promise — no opentelemetry-sdk required. If you
already run the OTel SDK, `to_otel_spans()` hands you plain dicts you can
feed to your own pipeline instead.

Grafana Tempo, Honeycomb, Datadog, Jaeger, and the OTel Collector all accept
this format on /v1/traces.
"""

from __future__ import annotations

import json
import os
import queue
import threading
import urllib.request
import uuid
from typing import Any, Optional

from .models import AgentRun
from .semconv import (
    OTEL_SPAN_KIND,
    SEMCONV_VERSION,
    run_attributes,
    span_attributes,
    span_name,
    status_for,
)

# The OTel opt-in env var: instrumentations default to older attribute
# spellings unless this asks for the current experimental set.
STABILITY_OPT_IN = os.getenv("OTEL_SEMCONV_STABILITY_OPT_IN", "")
CAPTURE_CONTENT = os.getenv("OTEL_GENAI_CAPTURE_MESSAGE_CONTENT", "").lower() in ("1", "true", "yes")


def _to_nanos(seconds: Optional[float]) -> int:
    return int((seconds or 0) * 1_000_000_000)


def _trace_id(run: AgentRun) -> str:
    """OTLP wants a 32-hex trace id; run_id is already a uuid4 hex."""
    rid = run.run_id.replace("-", "")
    return (rid + uuid.uuid4().hex)[:32] if len(rid) < 32 else rid[:32]


def _span_id(raw: str) -> str:
    """OTLP wants 16 hex chars."""
    clean = "".join(c for c in raw if c in "0123456789abcdef")
    return (clean + "0" * 16)[:16]


def _attr(key: str, value: Any) -> dict[str, Any]:
    """OTLP attributes are typed key/value pairs, not a flat map."""
    if isinstance(value, bool):
        v = {"boolValue": value}
    elif isinstance(value, int):
        v = {"intValue": str(value)}
    elif isinstance(value, float):
        v = {"doubleValue": value}
    else:
        v = {"stringValue": str(value)}
    return {"key": key, "value": v}


def to_otel_spans(
    run: AgentRun,
    dual_emit: bool = True,
    capture_content: bool = False,
) -> list[dict[str, Any]]:
    """Convert a run's spans into OTLP span dicts."""
    trace_id = _trace_id(run)
    out = []
    for s in run.spans:
        code, message = status_for(s)
        attrs = span_attributes(s, run.name, run.run_id, dual_emit, capture_content)
        span: dict[str, Any] = {
            "traceId": trace_id,
            "spanId": _span_id(s.span_id),
            "name": span_name(s),
            "kind": OTEL_SPAN_KIND.get(s.kind, 1),
            "startTimeUnixNano": str(_to_nanos(s.started_at)),
            "endTimeUnixNano": str(_to_nanos(s.ended_at or s.started_at)),
            "attributes": [_attr(k, v) for k, v in attrs.items()],
            "status": {"code": code, **({"message": message} if message else {})},
        }
        if s.parent_id:
            span["parentSpanId"] = _span_id(s.parent_id)
        if s.error:
            span["events"] = [
                {
                    "name": "exception",
                    "timeUnixNano": str(_to_nanos(s.ended_at or s.started_at)),
                    "attributes": [_attr("exception.message", s.error[:1000])],
                }
            ]
        out.append(span)
    return out


def to_otlp_payload(
    run: AgentRun,
    service_name: str = "agentlens",
    dual_emit: bool = True,
    capture_content: bool = False,
) -> dict[str, Any]:
    """Full ExportTraceServiceRequest body for POST /v1/traces."""
    resource_attrs = {
        "service.name": service_name,
        "telemetry.sdk.name": "agentlens",
        "telemetry.sdk.language": "python",
        **run_attributes(run, dual_emit),
    }
    return {
        "resourceSpans": [
            {
                "resource": {"attributes": [_attr(k, v) for k, v in resource_attrs.items()]},
                "scopeSpans": [
                    {
                        "scope": {"name": "agentlens", "version": SEMCONV_VERSION},
                        "spans": to_otel_spans(run, dual_emit, capture_content),
                    }
                ],
            }
        ]
    }


class OTLPExporter:
    """
    Exports runs to an OTLP/HTTP endpoint on a background thread.

    endpoint: collector base URL (":4318") or a full /v1/traces URL.
    dual_emit: also emit agentlens.* attributes alongside gen_ai.*, so
        retry lineage and cost survive a spec rename.
    capture_content: include prompt and response text. Off by default;
        override with OTEL_GENAI_CAPTURE_MESSAGE_CONTENT=true.
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:4318",
        headers: Optional[dict[str, str]] = None,
        service_name: str = "agentlens",
        dual_emit: bool = True,
        capture_content: Optional[bool] = None,
        retries: int = 2,
        timeout: float = 10.0,
    ):
        base = endpoint.rstrip("/")
        self.url = base if base.endswith("/v1/traces") else base + "/v1/traces"
        self.headers = {"Content-Type": "application/json", **(headers or {})}
        self.service_name = service_name
        self.dual_emit = dual_emit
        self.capture_content = CAPTURE_CONTENT if capture_content is None else capture_content
        self.retries = retries
        self.timeout = timeout
        self._q: queue.Queue[Optional[AgentRun]] = queue.Queue()
        threading.Thread(target=self._drain, daemon=True, name="agentlens-otlp").start()

    def export(self, run: AgentRun) -> None:
        self._q.put(run)

    def flush(self, timeout: float = 10.0) -> None:
        import time

        deadline = time.time() + timeout
        while not self._q.empty() and time.time() < deadline:
            time.sleep(0.05)

    def _drain(self) -> None:
        while True:
            run = self._q.get()
            if run is None:
                return
            payload = to_otlp_payload(run, self.service_name, self.dual_emit, self.capture_content)
            body = json.dumps(payload).encode()
            for _ in range(self.retries + 1):
                try:
                    req = urllib.request.Request(self.url, data=body, headers=self.headers, method="POST")
                    with urllib.request.urlopen(req, timeout=self.timeout):
                        break
                except Exception:
                    continue


class MultiExporter:
    """
    Fan a run out to several exporters — e.g. AgentLens for the DAG and run
    diffing, plus your existing OTel collector so agent traces sit beside
    the rest of the platform's telemetry. One failing exporter never
    prevents the others from receiving the run.
    """

    def __init__(self, *exporters):
        self.exporters = list(exporters)

    def export(self, run: AgentRun) -> None:
        for e in self.exporters:
            try:
                e.export(run)
            except Exception:
                continue

    def flush(self, timeout: float = 10.0) -> None:
        for e in self.exporters:
            if hasattr(e, "flush"):
                try:
                    e.flush(timeout)
                except Exception:
                    continue

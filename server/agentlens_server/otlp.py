"""
OTLP receiver: convert an incoming OTLP/HTTP trace payload into AgentLens
runs, so an agent already instrumented with OpenTelemetry gets the DAG,
diffing, and budget views without swapping out its SDK.

The mapping runs the reverse of the SDK's exporter. GenAI attributes are
read first; agentlens.* attributes fill in what the spec has no place for
(retry lineage, per-call cost). Spans are grouped by traceId — one trace
becomes one run.
"""

from __future__ import annotations

from typing import Any, Optional

# gen_ai.operation.name -> AgentLens span kind
KIND_FROM_OPERATION = {
    "invoke_agent": "agent",
    "create_agent": "agent",
    "chat": "llm",
    "text_completion": "llm",
    "generate_content": "llm",
    "execute_tool": "tool",
    "retrieval": "retrieval",
    "embeddings": "retrieval",
    "invoke_workflow": "chain",
}

STATUS_FROM_CODE = {0: "success", 1: "success", 2: "error"}


def _unwrap(value: dict[str, Any]) -> Any:
    """OTLP typed values -> plain Python."""
    if not isinstance(value, dict):
        return value
    for key, cast in (
        ("stringValue", str),
        ("boolValue", bool),
        ("intValue", int),
        ("doubleValue", float),
    ):
        if key in value:
            try:
                return cast(value[key])
            except (TypeError, ValueError):
                return value[key]
    if "arrayValue" in value:
        return [_unwrap(v) for v in value["arrayValue"].get("values", [])]
    return None


def _attrs(items: Optional[list]) -> dict[str, Any]:
    return {a["key"]: _unwrap(a.get("value", {})) for a in (items or []) if "key" in a}


def _seconds(nanos: Any) -> float:
    try:
        return int(nanos) / 1_000_000_000
    except (TypeError, ValueError):
        return 0.0


def _span_kind(attrs: dict[str, Any], otel_kind: Any) -> str:
    native = attrs.get("agentlens.span.kind")
    if native:
        return str(native)
    op = attrs.get("gen_ai.operation.name")
    if op:
        return KIND_FROM_OPERATION.get(str(op), "custom")
    # not a GenAI span at all — a plain HTTP or DB span in the same trace
    return "custom"


def _clean_name(name: str, attrs: dict[str, Any]) -> str:
    """Convention names are '{operation} {target}'; show the target."""
    native = attrs.get("agentlens.span.name")
    if native:
        return str(native)
    tool = attrs.get("gen_ai.tool.name")
    if tool:
        return str(tool)
    for op in KIND_FROM_OPERATION:
        if name.startswith(op + " "):
            return name[len(op) + 1 :]
    return name


def convert_trace(spans: list[dict], resource: dict[str, Any]) -> dict[str, Any]:
    """Turn one trace's spans into an AgentLens run dict."""
    parsed = []
    for s in spans:
        attrs = _attrs(s.get("attributes"))
        started = _seconds(s.get("startTimeUnixNano"))
        ended = _seconds(s.get("endTimeUnixNano")) or None
        status_code = (s.get("status") or {}).get("code", 0)
        error = (s.get("status") or {}).get("message")
        if not error:
            for ev in s.get("events") or []:
                if ev.get("name") == "exception":
                    error = _attrs(ev.get("attributes")).get("exception.message")
                    break

        llm = None
        in_tok = attrs.get("gen_ai.usage.input_tokens")
        out_tok = attrs.get("gen_ai.usage.output_tokens")
        model = attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model")
        if model or in_tok or out_tok:
            llm = {
                "model": str(model or ""),
                "provider": str(attrs.get("gen_ai.system") or ""),
                "input_tokens": int(in_tok or 0),
                "output_tokens": int(out_tok or 0),
                "total_tokens": int(in_tok or 0) + int(out_tok or 0),
                "cost_usd": float(attrs.get("agentlens.cost.usd") or 0.0),
                "prompt_preview": str(attrs.get("gen_ai.input.messages") or ""),
                "response_preview": str(attrs.get("gen_ai.output.messages") or ""),
                "temperature": attrs.get("gen_ai.request.temperature"),
            }

        parsed.append(
            {
                "span_id": s.get("spanId", ""),
                "parent_id": s.get("parentSpanId") or None,
                "name": _clean_name(s.get("name", "span"), attrs),
                "kind": _span_kind(attrs, s.get("kind")),
                "status": STATUS_FROM_CODE.get(status_code, "success"),
                "started_at": started,
                "ended_at": ended,
                "duration_ms": round((ended - started) * 1000, 2) if ended else None,
                "inputs": "",
                "outputs": "",
                "error": error,
                "retry_of": attrs.get("agentlens.retry_of"),
                "llm": llm,
                "attributes": {k: v for k, v in attrs.items() if not k.startswith(("gen_ai.", "agentlens."))},
            }
        )

    parsed.sort(key=lambda x: x["started_at"])
    ids = {p["span_id"] for p in parsed}
    roots = [p for p in parsed if not p["parent_id"] or p["parent_id"] not in ids]
    # a trace can arrive with several roots (parallel sub-agents); the
    # earliest one names the run
    root = roots[0] if roots else parsed[0]

    res_attrs = _attrs(resource.get("attributes"))
    name = res_attrs.get("gen_ai.agent.name") or root["name"] or res_attrs.get("service.name") or "otel_run"

    ends = [p["ended_at"] for p in parsed if p["ended_at"]]
    started_at = parsed[0]["started_at"]
    ended_at = max(ends) if ends else None

    scores = [
        {
            "name": k.split("agentlens.score.", 1)[1],
            "value": float(v),
            "source": "otel",
            "threshold": None,
            "passed": None,
            "comment": "",
            "span_id": None,
            "recorded_at": started_at,
        }
        for k, v in res_attrs.items()
        if k.startswith("agentlens.score.") and isinstance(v, (int, float))
    ]

    status = "error" if any(p["status"] == "error" for p in parsed) else "success"
    tags = [t for t in str(res_attrs.get("agentlens.run.tags", "")).split(",") if t]

    return {
        "run_id": str(res_attrs.get("agentlens.run.id") or spans[0].get("traceId", ""))[:64],
        "name": str(name),
        "tags": tags + ["otlp"],
        "status": str(res_attrs.get("agentlens.run.status") or status),
        "started_at": started_at,
        "ended_at": ended_at,
        "duration_ms": round((ended_at - started_at) * 1000, 2) if ended_at else None,
        "total_tokens": sum(p["llm"]["total_tokens"] for p in parsed if p["llm"]),
        "total_cost_usd": round(sum(p["llm"]["cost_usd"] for p in parsed if p["llm"]), 6),
        "error": next((p["error"] for p in parsed if p["status"] == "error" and p["error"]), None),
        "metadata": {
            "source": "otlp",
            "service_name": res_attrs.get("service.name"),
            "sdk": res_attrs.get("telemetry.sdk.name"),
        },
        "scores": scores,
        "spans": parsed,
    }


def convert_otlp(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """
    Convert a full ExportTraceServiceRequest into AgentLens runs.
    Spans are grouped by traceId, so one payload can carry several runs.
    """
    runs = []
    for rs in payload.get("resourceSpans", []) or []:
        resource = rs.get("resource", {}) or {}
        by_trace: dict[str, list[dict]] = {}
        for ss in rs.get("scopeSpans", []) or rs.get("instrumentationLibrarySpans", []) or []:
            for span in ss.get("spans", []) or []:
                by_trace.setdefault(span.get("traceId", "unknown"), []).append(span)
        for spans in by_trace.values():
            if spans:
                runs.append(convert_trace(spans, resource))
    return runs

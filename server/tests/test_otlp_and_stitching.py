import pytest
from agentlens_server.otlp import convert_otlp
from agentlens_server.stitching import is_child_run, stitch

pytestmark = pytest.mark.anyio


def _attr(key, value):
    if isinstance(value, int):
        return {"key": key, "value": {"intValue": str(value)}}
    return {"key": key, "value": {"stringValue": value}}


FOREIGN_TRACE = {
    "resourceSpans": [
        {
            "resource": {"attributes": [_attr("service.name", "langchain-app")]},
            "scopeSpans": [
                {
                    "scope": {"name": "opentelemetry.instrumentation.openai"},
                    "spans": [
                        {
                            "traceId": "a" * 32,
                            "spanId": "b" * 16,
                            "name": "invoke_agent support_bot",
                            "startTimeUnixNano": "1000000000",
                            "endTimeUnixNano": "3000000000",
                            "attributes": [
                                _attr("gen_ai.operation.name", "invoke_agent"),
                                _attr("gen_ai.agent.name", "support_bot"),
                            ],
                            "status": {"code": 1},
                        },
                        {
                            "traceId": "a" * 32,
                            "spanId": "c" * 16,
                            "parentSpanId": "b" * 16,
                            "name": "chat gpt-4o",
                            "startTimeUnixNano": "1200000000",
                            "endTimeUnixNano": "2500000000",
                            "attributes": [
                                _attr("gen_ai.operation.name", "chat"),
                                _attr("gen_ai.system", "openai"),
                                _attr("gen_ai.request.model", "gpt-4o"),
                                _attr("gen_ai.usage.input_tokens", 800),
                                _attr("gen_ai.usage.output_tokens", 200),
                            ],
                            "status": {"code": 1},
                        },
                    ],
                }
            ],
        }
    ]
}


def test_foreign_genai_trace_becomes_a_run():
    """A trace from another SDK, with no agentlens attributes at all."""
    runs = convert_otlp(FOREIGN_TRACE)
    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "support_bot"
    assert run["total_tokens"] == 1000
    assert {s["kind"] for s in run["spans"]} == {"agent", "llm"}
    assert run["spans"][1]["llm"]["provider"] == "openai"


def test_convert_groups_by_trace_id():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "a" * 32,
                                "spanId": "1" * 16,
                                "name": "one",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "attributes": [],
                                "status": {"code": 1},
                            },
                            {
                                "traceId": "d" * 32,
                                "spanId": "2" * 16,
                                "name": "two",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "attributes": [],
                                "status": {"code": 1},
                            },
                        ]
                    }
                ],
            }
        ]
    }
    assert len(convert_otlp(payload)) == 2


def test_error_status_maps_through():
    payload = {
        "resourceSpans": [
            {
                "resource": {"attributes": []},
                "scopeSpans": [
                    {
                        "spans": [
                            {
                                "traceId": "e" * 32,
                                "spanId": "1" * 16,
                                "name": "execute_tool search",
                                "startTimeUnixNano": "1000000000",
                                "endTimeUnixNano": "2000000000",
                                "attributes": [_attr("gen_ai.operation.name", "execute_tool")],
                                "status": {"code": 2, "message": "upstream 503"},
                            }
                        ]
                    }
                ],
            }
        ]
    }
    run = convert_otlp(payload)[0]
    assert run["status"] == "error"
    assert "503" in run["spans"][0]["error"]


async def test_otlp_endpoint(client):
    r = await client.post("/api/ingest/otlp", json=FOREIGN_TRACE)
    assert r.status_code == 202
    run = (await client.get(f"/api/runs/{r.json()['runs'][0]}")).json()
    assert run["name"] == "support_bot"
    assert "otlp" in run["tags"]


async def test_malformed_otlp_is_422_not_500(client):
    assert (await client.post("/api/ingest/otlp", json={"resourceSpans": "nope"})).status_code == 422


# --- stitching ------------------------------------------------------------ #


def _parent_run():
    return {
        "run_id": "parent",
        "name": "agent",
        "status": "success",
        "started_at": 100.0,
        "ended_at": 101.0,
        "duration_ms": 1000,
        "total_tokens": 10,
        "total_cost_usd": 0.001,
        "metadata": {},
        "spans": [
            {
                "span_id": "p1",
                "parent_id": None,
                "name": "agent",
                "kind": "agent",
                "status": "success",
                "started_at": 100.0,
                "ended_at": 101.0,
            },
            {
                "span_id": "p2",
                "parent_id": "p1",
                "name": "create_issue",
                "kind": "mcp",
                "status": "success",
                "started_at": 100.2,
                "ended_at": 100.9,
            },
        ],
    }


def _child_run(remote_parent="p2"):
    return {
        "run_id": "child",
        "name": "github.create_issue",
        "status": "success",
        "started_at": 100.3,
        "ended_at": 100.8,
        "duration_ms": 500,
        "total_tokens": 5,
        "total_cost_usd": 0.002,
        "metadata": {},
        "spans": [
            {
                "span_id": "c1",
                "parent_id": None,
                "name": "create_issue",
                "kind": "mcp",
                "status": "success",
                "started_at": 100.3,
                "ended_at": 100.8,
                "remote_parent_id": remote_parent,
                "service": "github",
            },
            {
                "span_id": "c2",
                "parent_id": "c1",
                "name": "github_api_post",
                "kind": "tool",
                "status": "success",
                "started_at": 100.4,
                "ended_at": 100.7,
                "service": "github",
            },
        ],
    }


def test_is_child_run_detects_remote_continuations():
    assert is_child_run(_child_run()) is True
    assert is_child_run(_parent_run()) is False


def test_stitch_grafts_onto_the_calling_span():
    merged = stitch(_parent_run(), [_child_run()])
    grafted = next(s for s in merged["spans"] if s["span_id"] == "c1")
    assert grafted["parent_id"] == "p2"
    assert len([s for s in merged["spans"] if not s.get("parent_id")]) == 1
    assert merged["metadata"]["grafted_spans"] == 1
    assert merged["total_tokens"] == 15


def test_stitch_keeps_orphans_visible():
    """A remote span whose caller isn't in this run must not vanish."""
    merged = stitch(_parent_run(), [_child_run(remote_parent="missing")])
    assert merged["metadata"]["orphaned_spans"] == 1
    assert any(s["span_id"] == "c1" for s in merged["spans"])


def test_stitch_does_not_mutate_the_stored_rows():
    parent, child = _parent_run(), _child_run()
    stitch(parent, [child])
    assert len(parent["spans"]) == 2, "stitching must return a new shape, not edit storage"
    assert child["spans"][0]["parent_id"] is None

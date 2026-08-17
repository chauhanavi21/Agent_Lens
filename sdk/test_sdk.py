"""Smoke tests for the AgentLens SDK. Run: python test_sdk.py"""

import asyncio
import json
import os
import tempfile

from agentlens import AgentLens, BudgetExceeded, FileExporter, SpanKind, current_run


def test_basic_dag():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    @lens.tool("web_search")
    def web_search(q):
        return [f"result for {q}"]

    @lens.span("summarize", kind=SpanKind.LLM)
    def summarize(docs):
        return "summary"

    @lens.trace("research_agent", tags=["test"])
    def agent(q):
        return summarize(web_search(q))

    assert agent("quantum") == "summary"
    run = json.loads(open(path).read().strip())
    assert run["status"] == "success"
    names = [s["name"] for s in run["spans"]]
    assert names == ["research_agent", "web_search", "summarize"]
    root_id = run["spans"][0]["span_id"]
    assert run["spans"][1]["parent_id"] == root_id
    assert run["spans"][2]["parent_id"] == root_id
    print("test_basic_dag ok")


def test_error_and_retry():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))
    calls = {"n": 0}

    @lens.tool("flaky", retries=2)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise ValueError("boom")
        return "ok"

    @lens.trace("retry_agent")
    def agent():
        return flaky()

    assert agent() == "ok"
    run = json.loads(open(path).read().strip())
    flaky_spans = [s for s in run["spans"] if s["name"] == "flaky"]
    assert len(flaky_spans) == 3
    assert flaky_spans[0]["status"] == "error"
    assert flaky_spans[1]["retry_of"] == flaky_spans[0]["span_id"]
    assert flaky_spans[2]["status"] == "success"
    print("test_error_and_retry ok")


def test_budget_guard():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    class FakeResponse:
        model = "gpt-4o"
        usage = type("U", (), {"prompt_tokens": 4000, "completion_tokens": 2000})()

    @lens.llm_call("chat", model="gpt-4o")
    def chat(prompt):
        return FakeResponse()

    @lens.trace("budget_agent", max_total_tokens=5000)
    def agent():
        chat("hello")  # 6000 tokens > 5000 budget: guard trips here
        chat("world")  # never reached
        return "done"

    raised = False
    try:
        agent()
    except BudgetExceeded:
        raised = True
    assert raised
    run = json.loads(open(path).read().strip())
    assert run["status"] == "paused"
    assert run["total_tokens"] == 6000
    print("test_budget_guard ok")


def test_async():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    @lens.tool("fetch")
    async def fetch(x):
        await asyncio.sleep(0.01)
        return x * 2

    @lens.trace("async_agent")
    async def agent(x):
        assert current_run() is not None
        return await fetch(x)

    assert asyncio.run(agent(21)) == 42
    run = json.loads(open(path).read().strip())
    assert run["status"] == "success" and len(run["spans"]) == 2
    print("test_async ok")


def test_scores():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    from agentlens import score

    @lens.span("answer", kind=SpanKind.LLM)
    def answer(q):
        score("relevancy", 0.91, source="ragas", threshold=0.8, on_span=True)
        return "a"

    @lens.trace("qa_agent")
    def agent(q):
        out = answer(q)
        score("faithfulness", 0.72, source="ragas", threshold=0.85, comment="hallucinated a date")
        return out

    assert agent("q") == "a"
    run = json.loads(open(path).read().strip())
    scores = {s["name"]: s for s in run["scores"]}
    assert scores["relevancy"]["passed"] is True
    assert scores["relevancy"]["span_id"] is not None      # scoped to the span
    assert scores["faithfulness"]["passed"] is False       # below threshold
    assert scores["faithfulness"]["span_id"] is None       # whole-run score
    print("test_scores ok")


def test_score_outside_run_is_safe():
    from agentlens import score
    assert score("orphan", 1.0) is None  # no active run: no-op, no crash
    print("test_score_outside_run_is_safe ok")


def test_from_ragas():
    from agentlens import from_ragas

    assert from_ragas({"faithfulness": 0.86, "answer_relevancy": 0.91, "name": "x"}) == {
        "faithfulness": 0.86, "answer_relevancy": 0.91
    }

    class R:  # per-sample list, as newer Ragas returns
        scores = [{"faithfulness": 0.9}, {"faithfulness": 0.7}]

    assert from_ragas(R()) == {"faithfulness": 0.8}
    print("test_from_ragas ok")


def test_otlp_payload_shape():
    from agentlens.otel import to_otlp_payload

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    captured = []

    class Capture:
        def export(self, run):
            captured.append(run)

    lens = AgentLens(exporter=Capture())

    @lens.tool("web_search")
    def ws(q):
        return ["d"]

    @lens.llm_call("chat", model="gpt-4o", provider="openai")
    def chat(p):
        class R:
            model = "gpt-4o"
            usage = type("U", (), {"prompt_tokens": 100, "completion_tokens": 50})()
        return R()

    @lens.trace("research_agent")
    def agent(q):
        ws(q)
        chat("hi")
        return "x"

    agent("q")
    payload = to_otlp_payload(captured[0], service_name="svc")
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]

    # convention span naming: "{operation} {target}"
    assert sorted(s["name"] for s in spans) == [
        "chat gpt-4o", "execute_tool web_search", "invoke_agent research_agent"
    ]
    # one trace id across the whole run, and a single root
    assert len({s["traceId"] for s in spans}) == 1
    assert sum(1 for s in spans if "parentSpanId" not in s) == 1
    # OTLP id widths are fixed
    assert all(len(s["traceId"]) == 32 and len(s["spanId"]) == 16 for s in spans)

    llm = next(s for s in spans if s["name"].startswith("chat"))
    attrs = {a["key"]: list(a["value"].values())[0] for a in llm["attributes"]}
    assert attrs["gen_ai.system"] == "openai"
    assert attrs["gen_ai.request.model"] == "gpt-4o"
    assert attrs["gen_ai.usage.input_tokens"] == "100"   # OTLP ints are strings
    assert attrs["gen_ai.operation.name"] == "chat"
    assert float(attrs["agentlens.cost.usd"]) > 0        # cost has no gen_ai home
    print("test_otlp_payload_shape ok")


def test_otlp_content_is_opt_in():
    from agentlens.otel import to_otlp_payload

    captured = []

    class Capture:
        def export(self, run):
            captured.append(run)

    lens = AgentLens(exporter=Capture())

    @lens.llm_call("chat", model="gpt-4o")
    def chat(prompt):
        class R:
            model = "gpt-4o"
            usage = type("U", (), {"prompt_tokens": 10, "completion_tokens": 5})()
        return R()

    @lens.trace("a")
    def agent():
        return chat("my social security number is 123-45-6789")

    agent()
    run = captured[0]

    off = to_otlp_payload(run, capture_content=False)
    keys = {a["key"] for s in off["resourceSpans"][0]["scopeSpans"][0]["spans"] for a in s["attributes"]}
    assert "gen_ai.input.messages" not in keys  # prompts stay out by default

    on = to_otlp_payload(run, capture_content=True)
    keys = {a["key"] for s in on["resourceSpans"][0]["scopeSpans"][0]["spans"] for a in s["attributes"]}
    assert "gen_ai.input.messages" in keys
    print("test_otlp_content_is_opt_in ok")


def test_multi_exporter_isolates_failures():
    from agentlens.otel import MultiExporter

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")

    class Broken:
        def export(self, run):
            raise RuntimeError("collector down")

    lens = AgentLens(exporter=MultiExporter(Broken(), FileExporter(path)))

    @lens.trace("resilient")
    def agent():
        return "ok"

    assert agent() == "ok"
    # the healthy exporter still received the run
    assert json.loads(open(path).read().strip())["name"] == "resilient"
    print("test_multi_exporter_isolates_failures ok")


def test_traceparent_roundtrip():
    from agentlens import format_traceparent, parse_traceparent

    tp = format_traceparent("a" * 32, "b" * 16)
    assert tp == f"00-{'a' * 32}-{'b' * 16}-01"
    parsed = parse_traceparent(tp)
    assert parsed["trace_id"] == "a" * 32 and parsed["parent_span_id"] == "b" * 16

    # short ids are padded to the widths the spec requires
    assert len(parse_traceparent(format_traceparent("abc", "def"))["trace_id"]) == 32

    # malformed headers are rejected rather than half-parsed
    assert parse_traceparent("") is None
    assert parse_traceparent("garbage") is None
    assert parse_traceparent(f"00-{'0' * 32}-{'b' * 16}-01") is None  # all-zero trace id
    assert parse_traceparent(f"00-{'z' * 32}-{'b' * 16}-01") is None  # not hex
    assert parse_traceparent("00-abc-def-01") is None                 # wrong widths
    print("test_traceparent_roundtrip ok")


def test_mcp_context_propagation():
    from agentlens import mcp_server_span, trace_mcp_session

    runs = []

    class Capture:
        def export(self, run):
            runs.append(json.loads(json.dumps(run.to_dict())))

    lens, server_lens = AgentLens(exporter=Capture()), AgentLens(exporter=Capture())

    @mcp_server_span(server_lens, server_name="github")
    def create_issue(arguments=None, _meta=None):
        return {"content": [], "isError": False}

    class FakeSession:
        transport = "stdio"

        def call_tool(self, name, arguments=None):
            # the server only ever sees what crossed the wire
            return create_issue(arguments=arguments, _meta=(arguments or {}).get("_meta"))

    @lens.trace("issue_agent")
    def agent():
        session = trace_mcp_session(lens, FakeSession(), server_name="github")
        return session.call_tool("create_issue", {"title": "t"})

    agent()
    agent_run = next(r for r in runs if r["name"] == "issue_agent")
    server_run = next(r for r in runs if r["name"].startswith("github."))

    # both processes agree on the trace
    assert agent_run["trace_id"] == server_run["trace_id"]
    # the server's root points at the client span, not the agent root
    client = next(s for s in agent_run["spans"] if s["kind"] == "mcp")
    assert server_run["spans"][0]["remote_parent_id"] == client["span_id"]
    # `service` names the recording process; the target is an attribute
    assert client["service"] is None
    assert client["attributes"]["mcp.server.name"] == "github"
    assert server_run["spans"][0]["service"] == "github"
    print("test_mcp_context_propagation ok")


def test_mcp_is_error_payload():
    from agentlens import mcp_server_span, trace_mcp_session

    runs = []

    class Capture:
        def export(self, run):
            runs.append(json.loads(json.dumps(run.to_dict())))

    lens = AgentLens(exporter=Capture())

    class FakeSession:
        transport = "stdio"

        def call_tool(self, name, arguments=None):
            # MCP reports tool failure in the payload, not by raising
            return {"content": [{"type": "text", "text": "rate limited"}], "isError": True}

    @lens.trace("agent")
    def agent():
        return trace_mcp_session(lens, FakeSession()).call_tool("x", {})

    agent()
    span = next(s for s in runs[0]["spans"] if s["kind"] == "mcp")
    assert span["status"] == "error", "isError payload should mark the span failed"
    assert span["attributes"]["mcp.tool.is_error"] is True
    print("test_mcp_is_error_payload ok")


def test_mcp_server_works_without_incoming_context():
    from agentlens import mcp_server_span

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    server_lens = AgentLens(exporter=Capture())

    @mcp_server_span(server_lens, server_name="github")
    def create_issue(arguments=None, _meta=None):
        return {"isError": False}

    # a client that doesn't propagate context still gets a usable trace
    create_issue(arguments={"title": "t"})
    run = runs[0]
    assert run["status"] == "success"
    assert run["spans"][0]["remote_parent_id"] is None
    assert run["trace_id"]
    print("test_mcp_server_works_without_incoming_context ok")


if __name__ == "__main__":
    test_basic_dag()
    test_error_and_retry()
    test_budget_guard()
    test_async()
    test_scores()
    test_score_outside_run_is_safe()
    test_from_ragas()
    test_otlp_payload_shape()
    test_otlp_content_is_opt_in()
    test_multi_exporter_isolates_failures()
    test_traceparent_roundtrip()
    test_mcp_context_propagation()
    test_mcp_is_error_payload()
    test_mcp_server_works_without_incoming_context()
    print("all SDK tests passed")

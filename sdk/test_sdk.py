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
    print("all SDK tests passed")

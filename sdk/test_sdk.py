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
    assert scores["relevancy"]["span_id"] is not None  # scoped to the span
    assert scores["faithfulness"]["passed"] is False  # below threshold
    assert scores["faithfulness"]["span_id"] is None  # whole-run score
    print("test_scores ok")


def test_score_outside_run_is_safe():
    from agentlens import score

    assert score("orphan", 1.0) is None  # no active run: no-op, no crash
    print("test_score_outside_run_is_safe ok")


def test_from_ragas():
    from agentlens import from_ragas

    assert from_ragas({"faithfulness": 0.86, "answer_relevancy": 0.91, "name": "x"}) == {
        "faithfulness": 0.86,
        "answer_relevancy": 0.91,
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
        "chat gpt-4o",
        "execute_tool web_search",
        "invoke_agent research_agent",
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
    assert attrs["gen_ai.usage.input_tokens"] == "100"  # OTLP ints are strings
    assert attrs["gen_ai.operation.name"] == "chat"
    assert float(attrs["agentlens.cost.usd"]) > 0  # cost has no gen_ai home
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
    assert parse_traceparent("00-abc-def-01") is None  # wrong widths
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
    from agentlens import trace_mcp_session

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


def test_streaming_event_lifecycle():
    from agentlens.streaming import run_end_event, run_start_event, span_event  # noqa: F401

    events = []

    class Recorder:
        def export(self, run):
            events.append(("run_export", run.name))

        def export_event(self, event):
            name = (event.get("span") or {}).get("name") or event["run"]["name"]
            events.append((event["type"], name))

    lens = AgentLens(exporter=Recorder())

    @lens.tool("web_search")
    def web_search(q):
        return ["d"]

    @lens.trace("live_agent")
    def agent(q):
        web_search(q)
        return "done"

    agent("q")

    # every span opens before it closes, and the run brackets everything
    assert events[0] == ("run_start", "live_agent")
    assert events[-1] == ("run_export", "live_agent")
    assert events[-2] == ("run_end", "live_agent")
    starts = [n for t, n in events if t == "span_start"]
    ends = [n for t, n in events if t == "span_end"]
    assert starts == ["live_agent", "web_search"]
    assert ends == ["web_search", "live_agent"]  # inner closes first
    print("test_streaming_event_lifecycle ok")


def test_streaming_events_are_optional():
    # an exporter without export_event must still work untouched
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    @lens.trace("plain")
    def agent():
        return "ok"

    assert agent() == "ok"
    assert json.loads(open(path).read().strip())["name"] == "plain"
    print("test_streaming_events_are_optional ok")


def test_streaming_exporter_never_breaks_the_agent():
    class Hostile:
        def export(self, run):
            raise RuntimeError("server down")

        def export_event(self, event):
            raise RuntimeError("server down")

    lens = AgentLens(exporter=Hostile())

    @lens.tool("step")
    def step():
        return 1

    @lens.trace("resilient")
    def agent():
        return step() + 1

    # tracing failures must not surface to the caller
    assert agent() == 2
    print("test_streaming_exporter_never_breaks_the_agent ok")


def test_stream_exporter_bounds_its_queue():
    from agentlens.streaming import StreamExporter

    # nothing is listening on this port, so the drain thread can't keep up
    exporter = StreamExporter("http://127.0.0.1:9", max_queue=5, timeout=0.01)
    for i in range(200):
        exporter.export_event({"type": "span_start", "run_id": "r", "span": {"span_id": str(i)}})

    # memory stays bounded rather than growing with the agent's work
    assert exporter._q.qsize() <= 5
    assert exporter.dropped > 0
    print("test_stream_exporter_bounds_its_queue ok")


def test_ci_threshold_parsing():
    from agentlens.ci import _parse_thresholds

    assert _parse_thresholds(["grounding=0.85", "task_completion=0.8"]) == {
        "grounding": 0.85,
        "task_completion": 0.8,
    }
    assert _parse_thresholds([]) == {}

    for bad in (["grounding"], ["grounding=abc"]):
        try:
            _parse_thresholds(bad)
            raise AssertionError(f"should have rejected {bad}")
        except ValueError:
            pass
    print("test_ci_threshold_parsing ok")


def test_ci_unreachable_server_is_an_error_not_a_pass():
    import contextlib
    import io

    from agentlens.ci import EXIT_ERROR, main

    # The CLI prints a connection error, which is the point — but an
    # alarming-looking message in a passing CI log costs someone a minute
    # every time they read it, so capture it and assert on it instead.
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        # nothing is listening on port 9; a build must not read this as clean
        code = main(["--endpoint", "http://127.0.0.1:9", "gate", "--candidate-tag", "pr-1"])

    assert code == EXIT_ERROR, code
    assert "could not reach" in stderr.getvalue()
    print("test_ci_unreachable_server_is_an_error_not_a_pass ok")


def test_ci_parser_defaults():
    from agentlens.ci import build_parser

    args = build_parser().parse_args(["gate", "--candidate-tag", "pr-9"])
    assert args.max_regression == 0.05 and args.min_runs == 1
    assert args.baseline_tag is None and args.warn_only is False
    print("test_ci_parser_defaults ok")


def _record_run(record_outputs=True, fail_search=False):
    """Run a small agent live and return (run_dict, call_counter)."""
    from agentlens import SpanKind

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), record_outputs=record_outputs)
    calls = {"search": 0, "answer": 0}

    @lens.tool("search")
    def search(q):
        calls["search"] += 1
        if fail_search:
            raise ConnectionError("search API 503")
        return {"hits": [f"doc about {q}"], "count": 1}

    @lens.span("answer", kind=SpanKind.LLM)
    def answer(docs):
        calls["answer"] += 1
        return f"answer from {docs['count']} doc(s)"

    @lens.trace("qa_agent")
    def agent(q):
        return answer(search(q))

    try:
        agent("paris")
    except ConnectionError:
        pass
    return json.loads(open(path).read().strip()), calls, lens, agent


def test_replay_serves_recorded_outputs():
    from agentlens import Cassette, replay

    run, calls, _lens, agent = _record_run()
    assert calls == {"search": 1, "answer": 1}

    cassette = Cassette.from_run(run)
    assert cassette.span_count == 2
    assert all(not c.truncated for items in cassette.calls.values() for c in items)

    with replay(cassette) as session:
        result = agent("paris")

    # the recorded world was served; no tool or LLM call ran again
    assert calls == {"search": 1, "answer": 1}
    assert result == "answer from 1 doc(s)"
    assert session.report()["hits"] == 2
    assert session.report()["diverged"] is False
    print("test_replay_serves_recorded_outputs ok")


def test_replay_runs_agent_logic_for_real():
    from agentlens import Cassette, SpanKind, replay

    run, calls, _lens, _agent = _record_run()
    cassette = Cassette.from_run(run)

    # a second lens with *changed* agent code, same recorded side effects
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens2 = AgentLens(exporter=FileExporter(path))
    seen = {}

    @lens2.tool("search")
    def search(q):
        raise AssertionError("the network must not be touched during replay")

    @lens2.span("answer", kind=SpanKind.LLM)
    def answer(docs):
        raise AssertionError("the model must not be called during replay")

    @lens2.trace("qa_agent")
    def agent_v2(q):
        docs = search(q)
        seen["docs"] = docs  # today's code, yesterday's data
        return answer(docs).upper()  # the change under test

    with replay(cassette):
        result = agent_v2("paris")

    assert seen["docs"] == {"hits": ["doc about paris"], "count": 1}
    assert result == "ANSWER FROM 1 DOC(S)", result
    print("test_replay_runs_agent_logic_for_real ok")


def test_replay_reproduces_recorded_failures():
    from agentlens import Cassette, ReplayedError, replay

    run, calls, _lens, agent = _record_run(fail_search=True)
    assert run["status"] == "error"

    cassette = Cassette.from_run(run)
    with replay(cassette):
        raised = False
        try:
            agent("paris")
        except ReplayedError as e:
            raised = True
            assert "503" in str(e)
    assert raised, "a recorded failure should fail the same way on replay"
    assert calls["search"] == 1, "the failing call must not be retried against the network"
    print("test_replay_reproduces_recorded_failures ok")


def test_replay_strict_mode_catches_new_calls():
    from agentlens import Cassette, ReplayMiss, replay

    run, _calls, _lens, _agent = _record_run()
    cassette = Cassette.from_run(run)

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens2 = AgentLens(exporter=FileExporter(path))

    @lens2.tool("search")
    def search(q):
        return {"hits": [], "count": 0}

    @lens2.tool("translate")  # a call the recording never saw
    def translate(text):
        return "live network call"

    @lens2.trace("qa_agent")
    def agent_v2(q):
        return translate(search(q))

    # strict: an unrecorded call is an error, not a silent trip to the network
    with replay(cassette, strict=True) as session:
        missed = False
        try:
            agent_v2("paris")
        except ReplayMiss as e:
            missed = True
            assert "translate" in str(e)
        assert missed
        assert session.report()["misses"] == ["translate"]

    # lenient: falls through to the real function
    with replay(cassette, strict=False):
        assert agent_v2("paris") == "live network call"
    print("test_replay_strict_mode_catches_new_calls ok")


def test_replay_matches_calls_in_order():
    from agentlens import Cassette, replay

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), record_outputs=True)
    n = {"i": 0}

    @lens.tool("search")
    def search(q):
        n["i"] += 1
        return f"result {n['i']}"

    @lens.trace("agent")
    def agent():
        return [search("a"), search("b"), search("c")]

    assert agent() == ["result 1", "result 2", "result 3"]
    cassette = Cassette.from_run(json.loads(open(path).read().strip()))

    with replay(cassette):
        # same call, three times — order decides which recording answers
        assert agent() == ["result 1", "result 2", "result 3"]
    assert n["i"] == 3, "no extra live calls"
    print("test_replay_matches_calls_in_order ok")


def test_replay_reports_unused_recordings():
    from agentlens import Cassette, replay

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), record_outputs=True)

    @lens.tool("search")
    def search(q):
        return q

    @lens.trace("agent")
    def agent(times):
        return [search(str(i)) for i in range(times)]

    agent(3)
    cassette = Cassette.from_run(json.loads(open(path).read().strip()))

    lens2 = AgentLens(exporter=FileExporter(os.path.join(tempfile.mkdtemp(), "r.jsonl")))

    @lens2.tool("search")
    def search2(q):
        return q

    @lens2.trace("agent")
    def agent_v2():
        return search2("0")  # today's code makes fewer calls

    with replay(cassette) as session:
        agent_v2()
    report = session.report()
    assert report["unused"] == {"search": 2}, report
    assert report["diverged"] is True, "fewer calls than recorded is a divergence"
    print("test_replay_reports_unused_recordings ok")


def test_cassette_round_trips_through_disk():
    from agentlens import Cassette, replay

    run, _calls, _lens, agent = _record_run()
    path = os.path.join(tempfile.mkdtemp(), "fixtures", "run.json")
    Cassette.from_run(run).save(path)

    loaded = Cassette.load(path)
    assert loaded.run_id == run["run_id"]
    assert loaded.span_count == 2
    with replay(loaded):
        assert agent("paris") == "answer from 1 doc(s)"
    print("test_cassette_round_trips_through_disk ok")


def test_cassette_without_recording_is_marked_truncated():
    from agentlens import Cassette

    run, _calls, _lens, _agent = _record_run(record_outputs=False)
    cassette = Cassette.from_run(run)
    # previews are strings, not the original objects — flagged so a caller
    # knows why feeding them back may not behave
    assert all(c.truncated for items in cassette.calls.values() for c in items)
    print("test_cassette_without_recording_is_marked_truncated ok")


def test_divergence_pinpoints_the_first_difference():
    from agentlens import divergence

    original = {
        "status": "error",
        "spans": [
            {"name": "agent", "kind": "agent", "started_at": 1},
            {"name": "search", "kind": "tool", "started_at": 2},
            {"name": "answer", "kind": "llm", "started_at": 3},
        ],
    }
    changed = {
        "status": "success",
        "spans": [
            {"name": "agent", "kind": "agent", "started_at": 1},
            {"name": "search", "kind": "tool", "started_at": 2},
            {"name": "validate", "kind": "custom", "started_at": 2.5},
            {"name": "answer", "kind": "llm", "started_at": 3},
        ],
    }

    d = divergence(original, changed)
    assert d["identical"] is False
    assert d["first_divergence"]["index"] == 2
    assert d["first_divergence"]["replayed"] == "validate"
    assert "error → success" in d["summary"]

    assert divergence(original, original)["identical"] is True
    print("test_divergence_pinpoints_the_first_difference ok")


def test_replay_rejects_changed_inputs():
    """
    Serving a recorded output for arguments that were never sent is a lie:
    nobody knows what that API or model would have returned.
    """
    from agentlens import Cassette, InputMismatch, SpanKind, replay

    run, _calls, _lens, _agent = _record_run()
    cassette = Cassette.from_run(run)

    lens2 = AgentLens(exporter=FileExporter(os.path.join(tempfile.mkdtemp(), "r.jsonl")))

    @lens2.tool("search")
    def search(q):
        raise AssertionError("network must not be reached")

    @lens2.span("answer", kind=SpanKind.LLM)
    def answer(docs):
        raise AssertionError("model must not be called")

    @lens2.trace("qa_agent")
    def agent_v2(q):
        docs = search(q)
        # today's code sends the model something different than was recorded
        return answer({"hits": docs["hits"], "count": docs["count"], "rerank": True})

    with replay(cassette, strict=True) as session:
        caught = False
        try:
            agent_v2("paris")
        except InputMismatch as e:
            caught = True
            assert "different arguments" in str(e)
            assert "Re-record" in str(e)
        assert caught, "changed inputs must not silently reuse a recording"
        assert session.report()["input_mismatches"][0]["name"] == "answer"

    # opting out is possible, but it's a deliberate choice
    cassette2 = Cassette.from_run(run)
    with replay(cassette2, match_inputs=False) as session:
        agent_v2("paris")
        assert session.report()["hits"] == 2
    print("test_replay_rejects_changed_inputs ok")


def test_redaction_detects_common_secrets():
    from agentlens import Redactor

    r = Redactor()
    cases = {
        "email me at jane.doe@acme.com": ("acme.com", "jane.doe@"),
        "call (555) 123-4567 today": ("phone", "123-4567"),
        "ssn 123-45-6789 on file": ("ssn:redacted", "123-45-6789"),
        "key sk-proj-abcdefghij1234567890": ("openai_key:redacted", "sk-proj-abcdefghij"),
        "AKIAIOSFODNN7EXAMPLE": ("aws_key:redacted", "AKIAIOSFODNN7EXAMPLE"),
        "token ghp_abcdefghijklmnop1234": ("github_token:redacted", "ghp_abcdef"),
        "host 10.0.0.5 replied": ("ipv4:", "10.0.0.5"),
    }
    for text, (expect_present, expect_absent) in cases.items():
        out = r.redact_text(text)
        assert expect_present in out, f"{text!r} → {out!r}"
        assert expect_absent not in out, f"leaked in {out!r}"
    print("test_redaction_detects_common_secrets ok")


def test_redaction_leaves_ordinary_text_alone():
    from agentlens import Redactor

    r = Redactor()
    safe = [
        "The meeting is at 3pm on 2026-08-22 in room 4021.",
        "Order 12345678901234567 shipped via route 66.",
        "Version 1.2.3.4 of the parser handles 99.9% of cases.",
        "def summarize(docs: list[str]) -> str: return docs[0]",
        "Error: connection reset after 30000 ms",
    ]
    for text in safe:
        assert r.redact_text(text) == text, f"false positive on {text!r}"
    print("test_redaction_leaves_ordinary_text_alone ok")


def test_credit_cards_need_luhn():
    from agentlens import Redactor
    from agentlens.redaction import luhn_valid

    r = Redactor()
    # a real number is caught
    assert "4111" not in r.redact_text("card 4111 1111 1111 1111")
    assert "••••1111" in r.redact_text("card 4111 1111 1111 1111")
    # a long digit run that isn't a card is left alone
    assert luhn_valid("4111111111111111") is True
    assert luhn_valid("12345678901234567") is False
    assert r.redact_text("invoice 12345678901234567") == "invoice 12345678901234567"
    print("test_credit_cards_need_luhn ok")


def test_redaction_policies_and_hash_stability():
    from agentlens import Redactor

    r = Redactor(policies={"email": "hash", "phone": "drop", "ipv4": "allow"})
    out = r.redact_text("jane@acme.com / (555) 123-4567 / 10.0.0.5")
    assert "[email:" in out and "acme.com" not in out
    assert "[phone:redacted]" in out
    assert "10.0.0.5" in out, "an allowed detector should not be redacted"

    # the same value fingerprints identically, so a user can be correlated
    # across runs without the value being stored
    assert r.redact_text("jane@acme.com") == r.redact_text("jane@acme.com")
    assert r.redact_text("jane@acme.com") != r.redact_text("bob@acme.com")

    # a different secret produces different tokens
    assert Redactor(policies={"email": "hash"}, hash_secret="a").redact_text("j@x.com") != Redactor(
        policies={"email": "hash"}, hash_secret="b"
    ).redact_text("j@x.com")
    print("test_redaction_policies_and_hash_stability ok")


def test_redaction_by_field_name():
    from agentlens import Redactor

    r = Redactor()
    # a short random key looks like any other string; the field name is the
    # only reliable signal
    out = r.redact_value({"user": "amy", "api_key": "x7f2q", "nested": {"password": "hunter2"}})
    assert out["user"] == "amy"
    assert out["api_key"].startswith("[api_key:") and "x7f2q" not in str(out)
    assert "hunter2" not in str(out)
    print("test_redaction_by_field_name ok")


def test_redaction_applies_to_exported_runs():

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), redact=True)

    @lens.tool("lookup_customer")
    def lookup(email):
        return {"email": email, "phone": "(555) 123-4567", "api_key": "secret123"}

    @lens.trace("support_agent")
    def agent(email):
        return lookup(email)

    agent("jane.doe@acme.com")
    raw = open(path).read()

    assert "jane.doe@acme.com" not in raw, "raw email reached the exporter"
    assert "(555) 123-4567" not in raw
    assert "secret123" not in raw
    assert "acme.com" in raw, "masking should keep the domain for debugging"
    print("test_redaction_applies_to_exported_runs ok")


def test_redaction_covers_streaming_events():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    events = []

    class Recorder:
        def export(self, run):
            pass

        def export_event(self, event):
            events.append(json.dumps(event, default=str))

    lens = AgentLens(exporter=Recorder(), redact=True)

    @lens.tool("lookup")
    def lookup(email):
        return f"found {email}"

    @lens.trace("agent")
    def agent(email):
        return lookup(email)

    agent("jane.doe@acme.com")
    blob = "\n".join(events)
    # streaming would otherwise bypass everything the export path protects
    assert "jane.doe@acme.com" not in blob, "streaming leaked what export redacts"
    assert len(events) > 0
    print("test_redaction_covers_streaming_events ok")


def test_capture_content_false_drops_everything():
    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), capture_content=False)

    @lens.tool("lookup")
    def lookup(secret_text):
        return f"result for {secret_text}"

    @lens.trace("agent")
    def agent(text):
        return lookup(text)

    agent("classified operation bluebird")
    run = json.loads(open(path).read().strip())

    assert "bluebird" not in open(path).read()
    # structure survives even though content is gone: you still get the DAG,
    # timings, and status
    assert len(run["spans"]) == 2
    assert run["spans"][1]["name"] == "lookup"
    assert run["status"] == "success"
    print("test_capture_content_false_drops_everything ok")


def test_redaction_failure_drops_rather_than_leaks():
    from agentlens import Redactor

    class BrokenRedactor(Redactor):
        def redact_text(self, text):
            raise RuntimeError("detector exploded")

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path), redact=BrokenRedactor())

    @lens.tool("lookup")
    def lookup(email):
        return f"found {email}"

    @lens.trace("agent")
    def agent(email):
        return lookup(email)

    # the agent keeps working …
    assert agent("jane.doe@acme.com") == "found jane.doe@acme.com"
    raw = open(path).read()
    # … and a broken redactor fails closed rather than emitting raw data
    assert "jane.doe@acme.com" not in raw, "redaction failure leaked PII"
    assert "redaction failed" in raw
    print("test_redaction_failure_drops_rather_than_leaks ok")


def test_redaction_scan_reports_without_changing():
    from agentlens import Redactor

    r = Redactor()
    found = r.scan("jane@acme.com and bob@acme.com called (555) 123-4567")
    assert found["email"] == 2
    assert found["phone"] == 1
    assert "ssn" not in found
    print("test_redaction_scan_reports_without_changing ok")


def test_custom_patterns_take_priority():
    from agentlens import Redactor

    r = Redactor(extra_patterns={"employee_id": r"\bEMP-\d{6}\b"}, policies={"employee_id": "hash"})
    out = r.redact_text("ticket from EMP-004217 about billing")
    assert "EMP-004217" not in out
    assert "[employee_id:" in out
    print("test_custom_patterns_take_priority ok")


# --------------------------------------------------------------------------- #
# framework integrations
#
# Each is exercised against a fake that mirrors the framework's documented
# interface. That keeps the suite dependency-free and, more usefully, pins
# the exact shape each adapter relies on — so when a framework changes, the
# failure names the assumption that broke.
# --------------------------------------------------------------------------- #


def test_openai_agents_processor_builds_a_run():
    from agentlens.integrations.openai_agents import AgentLensTracingProcessor

    runs = []

    class Capture:
        def export(self, run):
            runs.append(json.loads(json.dumps(run.to_dict())))

    lens = AgentLens(exporter=Capture())
    proc = AgentLensTracingProcessor(lens)

    # fakes mirroring the SDK: trace + spans carrying typed span_data
    class Trace:
        trace_id = "trace_abc123"
        name = "support_workflow"
        group_id = "thread-9"

    class AgentSpanData:
        name = "triage_agent"

    class GenerationSpanData:
        model = "gpt-4o"
        usage = {"input_tokens": 800, "output_tokens": 200}
        input = "user question"
        output = "call lookup_order"

    class FunctionSpanData:
        name = "lookup_order"
        input = '{"order_id": "ORD-1"}'
        output = "shipped"

    class FakeSpan:
        def __init__(self, span_id, data, parent_id=None, error=None):
            self.trace_id = "trace_abc123"
            self.span_id = span_id
            self.parent_id = parent_id
            self.span_data = data
            self.error = error

    proc.on_trace_start(Trace())
    agent_span = FakeSpan("s1", AgentSpanData())
    gen_span = FakeSpan("s2", GenerationSpanData(), parent_id="s1")
    fn_span = FakeSpan("s3", FunctionSpanData(), parent_id="s1", error={"message": "order not found"})
    for sp in (agent_span, gen_span, fn_span):
        proc.on_span_start(sp)
        proc.on_span_end(sp)
    proc.on_trace_end(Trace())

    assert len(runs) == 1
    run = runs[0]
    assert run["name"] == "support_workflow"
    assert run["metadata"]["group_id"] == "thread-9"

    by_name = {s["name"]: s for s in run["spans"]}
    assert by_name["triage_agent"]["kind"] == "agent"
    assert by_name["gpt-4o"]["kind"] == "llm"
    assert by_name["gpt-4o"]["llm"]["total_tokens"] == 1000
    assert by_name["gpt-4o"]["llm"]["cost_usd"] > 0
    assert by_name["lookup_order"]["kind"] == "tool"
    # the SDK reports failures on the span, not by raising
    assert by_name["lookup_order"]["status"] == "error"
    assert "order not found" in by_name["lookup_order"]["error"]
    assert run["status"] == "error", "a failed span should fail the run"

    # nesting follows the SDK's parent_id, not arrival order
    assert by_name["gpt-4o"]["parent_id"] == by_name["triage_agent"]["span_id"]
    print("test_openai_agents_processor_builds_a_run ok")


def test_openai_agents_processor_handles_concurrent_traces():
    from agentlens.integrations.openai_agents import AgentLensTracingProcessor

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    proc = AgentLensTracingProcessor(AgentLens(exporter=Capture()))

    class Trace:
        def __init__(self, tid, name):
            self.trace_id, self.name, self.group_id = tid, name, None

    class Data:
        def __init__(self, name):
            self.name = name

    class FakeSpan:
        def __init__(self, tid, sid, name):
            self.trace_id, self.span_id, self.parent_id = tid, sid, None
            self.span_data, self.error = Data(name), None

    a, b = Trace("trace_a", "workflow_a"), Trace("trace_b", "workflow_b")
    proc.on_trace_start(a)
    proc.on_trace_start(b)  # interleaved, as concurrent workflows are
    for sp in (FakeSpan("trace_a", "1", "step_a"), FakeSpan("trace_b", "2", "step_b")):
        proc.on_span_start(sp)
        proc.on_span_end(sp)
    proc.on_trace_end(a)
    proc.on_trace_end(b)

    assert len(runs) == 2
    names = {r["name"]: [s["name"] for s in r["spans"]] for r in runs}
    assert names["workflow_a"] == ["workflow_a", "step_a"], names
    assert names["workflow_b"] == ["workflow_b", "step_b"], names
    print("test_openai_agents_processor_handles_concurrent_traces ok")


def test_openai_agents_shutdown_exports_open_traces():
    from agentlens.integrations.openai_agents import AgentLensTracingProcessor

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    proc = AgentLensTracingProcessor(AgentLens(exporter=Capture()))

    class Trace:
        trace_id, name, group_id = "trace_x", "interrupted", None

    proc.on_trace_start(Trace())
    proc.shutdown()  # process exiting mid-trace

    assert len(runs) == 1, "an open trace should not vanish on shutdown"
    assert runs[0]["status"] == "cancelled"
    print("test_openai_agents_shutdown_exports_open_traces ok")


def test_langgraph_records_each_node():
    from agentlens.integrations.langgraph import trace_graph

    runs = []

    class Capture:
        def export(self, run):
            runs.append(json.loads(json.dumps(run.to_dict())))

    lens = AgentLens(exporter=Capture())

    class FakeGraph:
        """Mirrors stream_mode='updates': one {node: delta} chunk per step."""

        def stream(self, inputs, config=None, stream_mode=None, **kwargs):
            assert stream_mode == "updates"
            yield {"classify": {"intent": "refund"}}
            yield {"retrieve": {"docs": ["policy.md"]}}
            yield {"respond": {"answer": "refund approved"}}

        def invoke(self, inputs, config=None, **kwargs):
            raise AssertionError("should have streamed instead")

    app = trace_graph(lens, FakeGraph(), run_name="support_graph")
    result = app.invoke({"question": "refund please"})

    assert result["answer"] == "refund approved"
    run = runs[0]
    assert [s["name"] for s in run["spans"]] == ["support_graph", "classify", "retrieve", "respond"]
    assert all(s["kind"] == "chain" for s in run["spans"][1:])

    # per-node state attribution is the point of a graph trace
    classify = run["spans"][1]
    assert classify["attributes"]["langgraph.node"] == "classify"
    assert classify["attributes"]["langgraph.step"] == 0
    assert classify["attributes"]["langgraph.state_keys_changed"] == "+intent"
    assert run["spans"][2]["attributes"]["langgraph.state_keys_changed"] == "+docs"
    print("test_langgraph_records_each_node ok")


def test_langgraph_falls_back_when_streaming_is_unsupported():
    from agentlens.integrations.langgraph import trace_graph

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    class OldGraph:
        def stream(self, inputs, config=None, **kwargs):
            raise TypeError("stream_mode is not supported on this version")

        def invoke(self, inputs, config=None, **kwargs):
            return {"answer": "ok"}

    app = trace_graph(AgentLens(exporter=Capture()), OldGraph(), run_name="legacy")
    assert app.invoke({"q": 1}) == {"answer": "ok"}
    # the run still lands, just without per-node spans
    assert runs[0]["status"] == "success"
    assert len(runs[0]["spans"]) == 1
    print("test_langgraph_falls_back_when_streaming_is_unsupported ok")


def test_langgraph_records_failures_and_passes_through():
    from agentlens.integrations.langgraph import trace_graph

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    class BrokenGraph:
        node_names = ["a", "b"]  # an attribute the wrapper doesn't know about

        def stream(self, inputs, config=None, stream_mode=None, **kwargs):
            yield {"classify": {"intent": "x"}}
            raise RuntimeError("node 'retrieve' crashed")

    app = trace_graph(AgentLens(exporter=Capture()), BrokenGraph(), run_name="g")
    raised = False
    try:
        app.invoke({"q": 1})
    except RuntimeError:
        raised = True
    assert raised, "the graph's error must reach the caller"
    assert runs[0]["status"] == "error"
    # the node that did run is still recorded
    assert [s["name"] for s in runs[0]["spans"]] == ["g", "classify"]
    # unknown attributes fall through to the wrapped graph
    assert app.node_names == ["a", "b"]
    print("test_langgraph_records_failures_and_passes_through ok")


def test_pydantic_ai_traces_run_and_tools():
    import asyncio

    from agentlens.integrations.pydantic_ai import trace_agent

    runs = []

    class Capture:
        def export(self, run):
            runs.append(json.loads(json.dumps(run.to_dict())))

    lens = AgentLens(exporter=Capture())

    def get_weather(city):
        return f"sunny in {city}"

    class Tool:
        def __init__(self, fn, name):
            self.function, self.name = fn, name

    class Result:
        output = "It is sunny in Lisbon."
        model_name = "openai:gpt-4o"
        usage = {"request_tokens": 500, "response_tokens": 120}

    class FakeAgent:
        name = "weather_agent"

        def __init__(self):
            self._function_tools = {"get_weather": Tool(get_weather, "get_weather")}

        async def run(self, prompt, **kwargs):
            # the framework calls the (now wrapped) tool during the run
            self._function_tools["get_weather"].function("Lisbon")
            return Result()

    agent = trace_agent(lens, FakeAgent())
    result = asyncio.run(agent.run("weather in Lisbon?"))

    assert result.output == "It is sunny in Lisbon."
    run = runs[0]
    names = [s["name"] for s in run["spans"]]
    assert "weather_agent" in names
    assert "get_weather" in names, "tool call should be its own node"
    assert "openai:gpt-4o" in names

    tool_span = next(s for s in run["spans"] if s["name"] == "get_weather")
    assert tool_span["kind"] == "tool"
    assert "sunny in Lisbon" in tool_span["outputs"]

    model_span = next(s for s in run["spans"] if s["kind"] == "llm")
    assert model_span["llm"]["total_tokens"] == 620
    assert model_span["llm"]["cost_usd"] > 0
    assert run["total_tokens"] == 620
    print("test_pydantic_ai_traces_run_and_tools ok")


def test_pydantic_ai_survives_an_unknown_tool_registry():
    import asyncio

    from agentlens.integrations.pydantic_ai import trace_agent

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    class Result:
        output = "done"
        model_name = ""
        usage = None

    class FutureAgent:
        """A version that moved its tool registry somewhere new."""

        name = "future_agent"

        async def run(self, prompt, **kwargs):
            return Result()

    agent = trace_agent(AgentLens(exporter=Capture()), FutureAgent())
    assert asyncio.run(agent.run("x")).output == "done"
    # no per-tool spans, but the run itself is intact rather than lost
    assert runs[0]["status"] == "success"
    assert runs[0]["spans"][0]["name"] == "future_agent"
    print("test_pydantic_ai_survives_an_unknown_tool_registry ok")


def test_pydantic_ai_records_failures():
    import asyncio

    from agentlens.integrations.pydantic_ai import trace_agent

    runs = []

    class Capture:
        def export(self, run):
            runs.append(run.to_dict())

    class FailingAgent:
        name = "failing_agent"

        async def run(self, prompt, **kwargs):
            raise ValueError("model refused")

    agent = trace_agent(AgentLens(exporter=Capture()), FailingAgent())
    raised = False
    try:
        asyncio.run(agent.run("x"))
    except ValueError:
        raised = True
    assert raised
    assert runs[0]["status"] == "error"
    assert "model refused" in runs[0]["error"]
    print("test_pydantic_ai_records_failures ok")


def test_no_module_calls_format_exception_directly():
    """
    Guard for a trap that already bit once.

    `traceback.format_exception(exc)` is 3.10+; on 3.9 it needs the
    (type, value, tb) triple. `agentlens.compat.format_exception` handles
    both — but a shim nothing enforces is a shim someone forgets, which is
    exactly what happened when the framework integrations were written in a
    later session and CI caught it on the 3.9 matrix leg.

    A linter can't see this: the arity change is a stdlib signature
    difference, not syntax. So it's checked here instead.
    """
    import pathlib

    package = pathlib.Path(__file__).parent / "agentlens"
    offenders = []
    for path in package.rglob("*.py"):
        if path.name == "compat.py":
            continue
        if "traceback.format_exception(" in path.read_text(encoding="utf-8"):
            offenders.append(str(path.relative_to(package)))

    assert not offenders, (
        "these modules call traceback.format_exception directly and will break "
        f"on Python 3.9 — use agentlens.compat.format_exception instead: {offenders}"
    )
    print("test_no_module_calls_format_exception_directly ok")


def test_compat_format_exception_matches_the_stdlib():
    from agentlens.compat import format_exception

    try:
        raise ValueError("boom")
    except ValueError as e:
        rendered = format_exception(e)

    assert rendered.splitlines()[-1] == "ValueError: boom"
    assert "Traceback (most recent call last)" in rendered
    print("test_compat_format_exception_matches_the_stdlib ok")


if __name__ == "__main__":
    test_no_module_calls_format_exception_directly()
    test_compat_format_exception_matches_the_stdlib()
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
    test_streaming_event_lifecycle()
    test_streaming_events_are_optional()
    test_streaming_exporter_never_breaks_the_agent()
    test_stream_exporter_bounds_its_queue()
    test_ci_threshold_parsing()
    test_ci_unreachable_server_is_an_error_not_a_pass()
    test_ci_parser_defaults()
    test_replay_serves_recorded_outputs()
    test_replay_runs_agent_logic_for_real()
    test_replay_reproduces_recorded_failures()
    test_replay_strict_mode_catches_new_calls()
    test_replay_matches_calls_in_order()
    test_replay_reports_unused_recordings()
    test_cassette_round_trips_through_disk()
    test_cassette_without_recording_is_marked_truncated()
    test_replay_rejects_changed_inputs()
    test_divergence_pinpoints_the_first_difference()
    test_redaction_detects_common_secrets()
    test_redaction_leaves_ordinary_text_alone()
    test_credit_cards_need_luhn()
    test_redaction_policies_and_hash_stability()
    test_redaction_by_field_name()
    test_redaction_applies_to_exported_runs()
    test_redaction_covers_streaming_events()
    test_capture_content_false_drops_everything()
    test_redaction_failure_drops_rather_than_leaks()
    test_redaction_scan_reports_without_changing()
    test_custom_patterns_take_priority()
    test_openai_agents_processor_builds_a_run()
    test_openai_agents_processor_handles_concurrent_traces()
    test_openai_agents_shutdown_exports_open_traces()
    test_langgraph_records_each_node()
    test_langgraph_falls_back_when_streaming_is_unsupported()
    test_langgraph_records_failures_and_passes_through()
    test_pydantic_ai_traces_run_and_tools()
    test_pydantic_ai_survives_an_unknown_tool_registry()
    test_pydantic_ai_records_failures()
    print("all SDK tests passed")

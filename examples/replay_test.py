"""
Turning a production failure into a regression test.

The workflow:

  1. Production traces with `record_outputs=True`, so tool and model
     responses are stored, not just previewed.
  2. A run fails. Pull its cassette:
       curl localhost:7430/api/runs/<run_id>/cassette > fixtures/bug-471.json
  3. Write a test that replays it. The agent's own code runs for real
     against the exact responses it saw in production — no network, no
     model spend, no flakiness.

Run with pytest, or directly:  python examples/replay_test.py
"""

import os

from agentlens import AgentLens, Cassette, SpanKind, replay

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "bug-471.json")

lens = AgentLens(record_outputs=True)


@lens.tool("web_search")
def web_search(query):
    raise AssertionError("under replay this never runs; in production it calls the search API")


@lens.span("answer", kind=SpanKind.LLM)
def answer(docs):
    raise AssertionError("under replay this never runs; in production it calls the model")


@lens.trace("qa_agent")
def qa_agent(query):
    result = answer(web_search(query))
    # the fix: a response with no citations used to raise IndexError here
    citation = result["citations"][0] if result["citations"] else "no source"
    return f"{result['text']} [{citation}]"


def test_bug_471_no_citations():
    """A model response with no citations must not crash the agent."""
    cassette = Cassette.load(FIXTURE)
    with replay(cassette) as session:
        result = qa_agent("capital of France")

    assert "no source" in result
    report = session.report()
    assert report["misses"] == [], "the fix should not introduce new external calls"
    assert report["input_mismatches"] == [], "and should not change what it sends them"


def _record_fixture_if_missing():
    """
    Normally you'd curl this from the server. Recorded here from a stand-in
    of the buggy production code so the example is self-contained — and so
    the inputs are captured exactly as the tracer writes them, rather than
    hand-typed.
    """
    if os.path.exists(FIXTURE):
        return

    from agentlens import FileExporter

    import tempfile

    rec = AgentLens(
        exporter=FileExporter(os.path.join(tempfile.mkdtemp(), "runs.jsonl")),
        record_outputs=True,
    )

    @rec.tool("web_search")
    def rec_search(query):
        return {"hits": ["Paris is the capital of France."], "count": 1}

    @rec.span("answer", kind=SpanKind.LLM)
    def rec_answer(docs):
        return {"text": "The capital of France is Paris.", "citations": []}

    @rec.trace("qa_agent")
    def buggy(query):
        result = rec_answer(rec_search(query))
        return result["text"] + " [" + result["citations"][0] + "]"   # the bug

    captured = {}
    rec.exporter.export = lambda run: captured.setdefault("run", run.to_dict())
    try:
        buggy("capital of France")
    except IndexError:
        pass
    Cassette.from_run(captured["run"]).save(FIXTURE)


if __name__ == "__main__":
    _record_fixture_if_missing()
    test_bug_471_no_citations()
    print("bug 471 regression test passed — no network, no model calls")

"""
Scoring an agent's output quality, two ways.

1. Inline — score inside the traced run when the metric is cheap to compute.
2. Post-hoc — run an eval suite (Ragas or your own) after the agent finishes
   and attach the scores by run_id.

Run the server first (docker compose up), then:

    python examples/eval_agent.py
"""

import time

from agentlens import AgentLens, SpanKind, from_ragas, score

ENDPOINT = "http://localhost:7430"
lens = AgentLens(endpoint=ENDPOINT)


@lens.span("retrieve", kind=SpanKind.RETRIEVAL)
def retrieve(question):
    time.sleep(0.2)
    return ["Paris is the capital of France.", "France is in Western Europe."]


@lens.span("answer", kind=SpanKind.LLM)
def answer(question, context):
    time.sleep(0.3)
    # a cheap inline check: did we actually ground the answer in context?
    grounded = len(context) / 3.0
    score("context_coverage", min(grounded, 1.0), threshold=0.5, on_span=True)
    return "The capital of France is Paris."


@lens.trace("qa_agent", tags=["eval", "rag"])
def qa_agent(question):
    context = retrieve(question)
    return answer(question, context)


def fake_ragas_evaluate(question, answer_text, context):
    """
    Stand-in for `ragas.evaluate(...)`. Swap this for the real call:

        from ragas import evaluate
        from ragas.metrics import faithfulness, answer_relevancy
        result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
        scores = from_ragas(result)
    """
    return {"faithfulness": 0.88, "answer_relevancy": 0.94, "context_precision": 0.71}


if __name__ == "__main__":
    question = "What is the capital of France?"
    result = qa_agent(question)
    print(f"answer: {result}")

    lens.exporter.flush()  # make sure the run landed before scoring it

    # find the run we just produced, then attach eval scores to it
    # in a real harness you'd capture run_id from the traced call; here we
    # read the most recent run back from the server
    import json
    import urllib.request

    from agentlens.context import current_run  # noqa: F401  (illustrative)

    with urllib.request.urlopen(f"{ENDPOINT}/api/runs?limit=1") as res:
        runs = json.loads(res.read())
    if not runs:
        raise SystemExit("No runs found — is the server running on :7430?")
    run_id = runs[0]["run_id"]

    raw = fake_ragas_evaluate(question, result, retrieve.__wrapped__(question))
    scores = from_ragas(raw)
    ok = lens.score_run(
        run_id,
        scores,
        source="ragas",
        thresholds={"faithfulness": 0.85, "answer_relevancy": 0.80, "context_precision": 0.75},
        comment="nightly eval suite",
    )
    print(f"scores attached: {ok} → {scores}")
    print("Open the Quality tab at http://localhost:5173 to see the trend.")

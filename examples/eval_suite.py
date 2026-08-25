"""
The eval suite CI runs on every PR.

Each case is a fixed input with an expected shape. Runs are tagged with the
branch identifier so the gate can find them, then compared against whatever
`main` last produced.

    AGENTLENS_RUN_TAG=pr-42 python examples/eval_suite.py
    python -m agentlens.ci gate --candidate-tag pr-42 --baseline-tag main
"""

import os
import time

from agentlens import AgentLens, SpanKind, score

ENDPOINT = os.getenv("AGENTLENS_ENDPOINT", "http://localhost:7430")
RUN_TAG = os.getenv("AGENTLENS_RUN_TAG", "local")

lens = AgentLens(endpoint=ENDPOINT)

CASES = [
    {"question": "What is the capital of France?", "must_contain": "Paris"},
    {"question": "Who wrote Pride and Prejudice?", "must_contain": "Austen"},
    {"question": "What is the boiling point of water at sea level?", "must_contain": "100"},
]

ANSWERS = {
    "What is the capital of France?": (
        "The capital of France is Paris.",
        ["Paris is the capital of France."],
    ),
    "Who wrote Pride and Prejudice?": (
        "Jane Austen wrote it in 1813.",
        ["Pride and Prejudice, by Jane Austen."],
    ),
    "What is the boiling point of water at sea level?": (
        "Water boils at 100°C at sea level.",
        ["Water boils at 100°C."],
    ),
}


@lens.span("retrieve", kind=SpanKind.RETRIEVAL)
def retrieve(question):
    time.sleep(0.05)
    return ANSWERS[question][1]


@lens.span("answer", kind=SpanKind.LLM)
def answer(question, context):
    time.sleep(0.05)
    return ANSWERS[question][0]


def run_case(case):
    @lens.trace("qa_agent", tags=[RUN_TAG, "eval"])
    def qa_agent(question):
        context = retrieve(question)
        result = answer(question, context)

        # deterministic checks, computed from the trace itself
        score("task_completion", 1.0 if case["must_contain"] in result else 0.0, threshold=0.8)
        grounded = sum(1 for c in context if any(w in c for w in result.split()[:6]))
        score("grounding", min(grounded / max(len(context), 1), 1.0), threshold=0.85)
        return result

    return qa_agent(case["question"])


if __name__ == "__main__":
    for case in CASES:
        print(f"  {case['question']} → {run_case(case)}")
    lens.exporter.flush()
    print(f"\n{len(CASES)} runs sent, tagged '{RUN_TAG}'.")
    print("Gate them with:")
    print(f"  python -m agentlens.ci gate --candidate-tag {RUN_TAG} --baseline-tag main \\")
    print("    --threshold grounding=0.85 --max-regression 0.03")

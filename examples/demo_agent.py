"""
A small research agent that exercises every AgentLens feature:
nested spans, tool calls, LLM metadata, a retry, and a budget guard.

Run the server first (docker compose up), then:

    pip install -e ./sdk
    python examples/demo_agent.py

Open http://localhost:5173 to explore the runs.
"""

import random
import time

from agentlens import AgentLens, BudgetExceeded, SpanKind

lens = AgentLens(endpoint="http://localhost:7430")


class FakeLLMResponse:
    """Stands in for an OpenAI/Anthropic response object."""

    def __init__(self, model, text, in_tok, out_tok):
        self.model = model
        self.text = text
        self.usage = type("U", (), {"prompt_tokens": in_tok, "completion_tokens": out_tok})()

    def __repr__(self):
        return self.text


@lens.llm_call("plan_steps", model="gpt-4o-mini", provider="openai")
def plan_steps(query):
    time.sleep(0.2)
    return FakeLLMResponse("gpt-4o-mini", "1. search 2. retrieve 3. synthesize", 320, 95)


@lens.tool("web_search")
def web_search(query):
    time.sleep(0.4)
    return [f"https://example.com/{i}" for i in range(8)]


@lens.span("retrieve_docs", kind=SpanKind.RETRIEVAL)
def retrieve_docs(urls):
    time.sleep(0.3)
    return [f"doc {u}" for u in urls[:5]]


@lens.span("synthesize", kind=SpanKind.LLM, retries=2)
def synthesize(docs, flaky=False):
    time.sleep(0.5)
    if flaky and random.random() < 0.7:
        raise TimeoutError("model timeout at 2000ms")
    return FakeLLMResponse("claude-sonnet-4", "Report: 3 key findings…", 1980, 445)


@lens.trace("research_agent", tags=["demo", "rag"], max_cost_usd=0.50)
def research_agent(query, flaky=False):
    plan_steps(query)
    urls = web_search(query)
    docs = retrieve_docs(urls)
    return synthesize(docs, flaky=flaky).text


if __name__ == "__main__":
    print(research_agent("agent observability landscape 2026"))
    try:
        print(research_agent("same query, flaky model", flaky=True))
    except (TimeoutError, BudgetExceeded) as e:
        print(f"run failed as expected: {e}")
    lens.exporter.flush()
    print("Done. Open http://localhost:5173 and pin both runs to diff them.")

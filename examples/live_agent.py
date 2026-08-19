"""
Watching an agent execute in real time.

Batch tracing sends one payload when the run ends, which means a run that
hangs, gets OOM-killed, or is still going never shows up at all — precisely
the runs you most want to look at. StreamExporter pushes each span as it
opens and closes, then the complete run at the end.

Start the stack (docker compose up), open http://localhost:5173, then:

    python examples/live_agent.py

The DAG draws itself node by node while this runs.
"""

import random
import time

from agentlens import AgentLens, SpanKind, StreamExporter

lens = AgentLens(exporter=StreamExporter("http://localhost:7430"))


class FakeResponse:
    def __init__(self, model, in_tok, out_tok):
        self.model = model
        self.usage = type("U", (), {"input_tokens": in_tok, "output_tokens": out_tok})()

    def __repr__(self):
        return "…"


@lens.llm_call("plan_steps", model="gpt-4o-mini", provider="openai")
def plan_steps(query):
    time.sleep(1.2)
    return FakeResponse("gpt-4o-mini", 320, 95)


@lens.tool("web_search")
def web_search(query):
    time.sleep(1.5)
    return [f"https://example.com/{i}" for i in range(6)]


@lens.span("fetch_page", kind=SpanKind.RETRIEVAL, retries=2)
def fetch_page(url):
    time.sleep(0.8)
    if random.random() < 0.4:
        raise TimeoutError(f"timed out fetching {url}")
    return f"content of {url}"


@lens.llm_call("synthesize", model="claude-sonnet-4", provider="anthropic")
def synthesize(pages):
    time.sleep(2.5)
    return FakeResponse("claude-sonnet-4", 1980, 445)


@lens.trace("live_research_agent", tags=["demo", "live"])
def research_agent(query):
    plan_steps(query)
    urls = web_search(query)
    pages = []
    for url in urls[:3]:
        try:
            pages.append(fetch_page(url))
        except TimeoutError:
            pass          # retries exhausted; the failed attempts stay in the DAG
    synthesize(pages)
    return f"report from {len(pages)} pages"


if __name__ == "__main__":
    print("Open http://localhost:5173 now — the run appears as it executes.")
    time.sleep(2)
    print(research_agent("agent observability standards"))
    lens.exporter.flush()
    print("Done. The run has moved from the live list into the stored runs.")

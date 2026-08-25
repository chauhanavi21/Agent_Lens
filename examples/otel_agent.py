"""
Sending agent traces to AgentLens and an OpenTelemetry backend at once.

The point of the bridge: AgentLens gives you the agent-shaped views (DAG,
run diffing, budget guards), while your existing observability stack gets
the same spans in the standard GenAI vocabulary — so agent traces sit
beside your service traces instead of in a silo.

Run a collector (or Jaeger/Tempo/Honeycomb) on :4318, then:

    python examples/otel_agent.py
"""

import time

from agentlens import AgentLens, HttpExporter, SpanKind
from agentlens.otel import MultiExporter, OTLPExporter

lens = AgentLens(
    exporter=MultiExporter(
        HttpExporter("http://localhost:7430"),  # AgentLens UI
        OTLPExporter(
            "http://localhost:4318",  # OTel collector
            service_name="research-agent",
            # headers={"x-honeycomb-team": "…"},        # or a SaaS backend
        ),
    )
)


class FakeResponse:
    def __init__(self, model, in_tok, out_tok):
        self.model = model
        self.usage = type("U", (), {"input_tokens": in_tok, "output_tokens": out_tok})()

    def __repr__(self):
        return "…"


@lens.tool("web_search")
def web_search(query):
    time.sleep(0.3)
    return [f"https://example.com/{i}" for i in range(5)]


@lens.span("retrieve_docs", kind=SpanKind.RETRIEVAL)
def retrieve_docs(urls):
    time.sleep(0.2)
    return [f"doc {u}" for u in urls]


@lens.llm_call("synthesize", model="claude-sonnet-4", provider="anthropic")
def synthesize(docs):
    time.sleep(0.4)
    return FakeResponse("claude-sonnet-4", 1980, 445)


@lens.trace("research_agent", tags=["prod", "otel"], max_cost_usd=0.50)
def research_agent(query):
    docs = retrieve_docs(web_search(query))
    synthesize(docs)
    return "report"


if __name__ == "__main__":
    print(research_agent("agent observability standards"))
    for exporter in lens.exporter.exporters:
        if hasattr(exporter, "flush"):
            exporter.flush()

    print("Sent to AgentLens (http://localhost:5173) and OTLP (:4318).")
    print()
    print("In your OTel backend, the run appears as a span tree:")
    print("  invoke_agent research_agent")
    print("    ├── execute_tool web_search")
    print("    ├── retrieval retrieve_docs")
    print("    └── chat claude-sonnet-4    gen_ai.usage.input_tokens=1980")
    print()
    print("Going the other way: point any OTel exporter at")
    print("  http://localhost:7430/api/ingest/otlp")
    print("and traces from other SDKs show up as AgentLens runs.")

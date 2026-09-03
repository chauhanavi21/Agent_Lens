# Framework integrations

One line each, no change to the agent itself. Runs from all of them land in
the same UI with the same DAG, diffing, and eval scoring.

```python
# OpenAI Agents SDK — register a processor, its own tracing does the rest
from agents import add_trace_processor
from agentlens.integrations.openai_agents import AgentLensTracingProcessor

add_trace_processor(AgentLensTracingProcessor(lens))

# LangGraph — each node becomes a span, tagged with the state keys it changed
from agentlens.integrations.langgraph import trace_graph

app = trace_graph(lens, graph.compile(), run_name="support_graph")

# Pydantic AI — wraps the agent and its registered tools
from agentlens.integrations.pydantic_ai import trace_agent

agent = trace_agent(lens, Agent("openai:gpt-4o", tools=[get_weather]))

# LangChain
from agentlens.integrations.langchain import AgentLensCallbackHandler

chain.invoke(inputs, config={"callbacks": [AgentLensCallbackHandler(lens)]})

# CrewAI
from agentlens.integrations.crewai import trace_crew

trace_crew(lens, crew, run_name="research_crew").kickoff(inputs={...})
```

Notes worth knowing:

- **Importing `agentlens` never pulls in a framework.** The adapters read
  framework objects by duck typing rather than importing their types —
  these APIs are still moving, and a renamed attribute should cost one
  field, not the whole trace. Each adapter is tested against a fake mirroring
  the documented interface, so when a framework changes, the failing test
  names the assumption that broke.
- **LangGraph** records per-node state deltas, so a loop reads as the same
  node repeating with a shrinking diff rather than an undifferentiated
  stack of steps. It falls back to plain `invoke` on versions without
  `stream_mode="updates"`.
- **OpenAI Agents SDK** buffers a trace until it ends, keeps concurrent
  workflows separate, and exports still-open traces on `shutdown()` rather
  than losing them at exit.
- **Pydantic AI** is already OTel-instrumented, so if you run Logfire or a
  collector, `instrument_all()` plus `/api/ingest/otlp` is the lighter
  path; the wrapper is for setups without one.

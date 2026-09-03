# AgentLens

**Open source observability runtime for AI agents.**
*Langfuse traces LLM calls. AgentLens traces the whole agent.*

Your agent failed in production at step 11 of a 15-step plan, and all your
tracing tool shows you is a list of LLM calls. Agents aren't a single call —
they're a **graph** of decisions: tools firing, sub-agents spawning,
retrieval steps, retries. AgentLens captures that execution DAG and gives
you a UI to inspect any node, diff two runs, and stop runaway costs before
they happen.

```bash
pip install agentlens      # or: npm i @agentlens/sdk
```

```python
from agentlens import AgentLens

lens = AgentLens(endpoint="http://localhost:7430")


@lens.trace("research_agent", tags=["prod"], max_cost_usd=0.10)
def research_agent(query: str) -> str:
    return summarize(retrieve_docs(query))
```

<img src="https://raw.githubusercontent.com/chauhanavi21/Agent_Lens/main/assets/architecture.svg" alt="AgentLens architecture" width="100%">

## Start here

| If you want to… | Read |
| --- | --- |
| Trace an agent and see its DAG | [Tracing your agent](guides/tracing.md) |
| Use it from Node or TypeScript | [TypeScript SDK](guides/typescript.md) |
| Wire up LangGraph, CrewAI, the OpenAI Agents SDK | [Framework integrations](guides/frameworks.md) |
| Score runs and fail a PR on regression | [Evals and the CI gate](guides/evals.md) |
| Turn a production failure into a test | [Trace replay](guides/replay.md) |
| Run it yourself | [Self-hosting](operations/self-hosting.md) |
| Keep prompts out of your trace store | [PII redaction](operations/privacy.md) |
| Stop the database growing forever | [Data lifecycle](operations/data-lifecycle.md) |

## What makes it different

- **The execution graph, not a call list** — every span, its parent, its
  retries, and its cost.
- **Run diffing** — pin two runs and see which step diverged, with quality
  regressions leading the verdict.
- **[Cross-process MCP tracing](reference/mcp.md)** — the agent and the tool
  servers it calls land in one waterfall.
- **[Deterministic replay](guides/replay.md)** — re-run an agent against
  recorded tool outputs, with no network and no model spend.
- **[A CI gate](guides/evals.md)** that fails a pull request when eval
  scores regress, not just when they breach a floor.
- **[OTLP in both directions](reference/opentelemetry.md)** — export to
  Grafana or Honeycomb, or ingest traces from any OpenTelemetry SDK.

## Why the design is the way it is

The decisions, what they cost, and the limitations that are deliberate are
written up in
[ARCHITECTURE.md](https://github.com/chauhanavi21/Agent_Lens/blob/main/ARCHITECTURE.md) —
including the ones I'd do differently.

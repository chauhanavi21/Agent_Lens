<div align="center">

# ◉ AgentLens

**Open source observability runtime for AI agents.**
*Langfuse traces LLM calls. AgentLens traces the whole agent.*

`pip install agentlens` · Apache 2.0 · zero SDK dependencies

</div>

---

Your agent failed in production at step 11 of a 15-step plan, and all your
tracing tool shows you is a list of LLM calls. Agents aren't a single call —
they're a **graph** of decisions: tools firing, sub-agents spawning, retrieval
steps, retries. AgentLens captures that full execution DAG and gives you a UI
to inspect any node, diff two runs, and stop runaway costs before they happen.

## Features

|                                  | AgentLens | Langfuse | Helicone |
| -------------------------------- | :-------: | :------: | :------: |
| LLM call tracing                 |     ✅     |    ✅     |    ✅     |
| **Agent execution graph (DAG)**  |     ✅     |    ❌     |    ❌     |
| **Run diffing**                  |     ✅     |    ❌     |    ❌     |
| **Retry lineage**                |     ✅     |    ❌     |    ❌     |
| **Budget guards (tokens/cost)**  |     ✅     |    ❌     |    ❌     |
| Self-hostable                    |     ✅     |    ✅     |    ❌     |
| Zero required deps (SDK)         |     ✅     |    ❌     |    ❌     |
| Framework agnostic               |     ✅     |    ✅     |    ✅     |

## Quickstart

### 1. Instrument your agent

```bash
pip install agentlens
```

```python
from agentlens import AgentLens, SpanKind

lens = AgentLens(endpoint="http://localhost:7430")

@lens.trace("research_agent", tags=["prod"], max_cost_usd=0.10)
def research_agent(query: str) -> str:
    docs = retrieve_docs(query)
    return summarize(docs)

@lens.span("retrieve_docs", kind=SpanKind.RETRIEVAL)
def retrieve_docs(query): ...

@lens.llm_call("summarize", model="gpt-4o")
def summarize(docs): ...   # token usage auto-extracted from the response
```

Every decorated function becomes a node in the DAG. Nesting, async, and
retries are captured automatically. Export happens on a background thread —
tracing never blocks the agent, and never crashes it.

### 2. Run the server + UI

```bash
git clone https://github.com/chauhanavi21/agentlens
cd agentlens
docker compose up
```

- Server: `http://localhost:7430`
- UI: `http://localhost:5173`

Try it immediately: `python examples/demo_agent.py` sends two runs (one clean,
one with retries and a failure) so you can explore the DAG and diff views.

### 3. What you'll see

- **Runs sidebar** — every agent run, filterable by status and name, live-polling
- **DAG view** — the execution tree, color-coded by span kind, error and retry
  markers, dashed retry-lineage edges
- **Span drawer** — click any node: inputs, outputs, exact prompt/response,
  token counts, cost, full error traceback
- **Run diff** — pin two runs (★), see which steps appeared, disappeared,
  flipped status, or got slower — with a one-line verdict naming the span
  where behavior first diverged

## SDK reference

```python
from agentlens import AgentLens, SpanKind
from agentlens.exporters import FileExporter

lens = AgentLens(
    endpoint="http://localhost:7430",   # or exporter=FileExporter("runs.jsonl")
    api_key="your-key",                 # optional server auth
    on_budget="raise",                  # "raise" | "pause" | "warn"
)

@lens.trace("my_agent", tags=["prod"], max_total_tokens=5000, max_cost_usd=0.05)
def my_agent(query): ...

@lens.span("retrieve", kind=SpanKind.RETRIEVAL)
def retrieve(query): ...

@lens.tool("web_search", retries=2)     # failed attempts stay in the DAG,
def web_search(query): ...              # linked by retry lineage

@lens.llm_call("chat", model="gpt-4o")  # auto token/cost from OpenAI- and
def chat(prompt): ...                   # Anthropic-style responses
```

Zero-config module decorators (`from agentlens import trace, tool`) print
one-line run summaries to the console — useful before you have a server.
Async functions work identically.

### Framework integrations

```python
# LangChain
from agentlens.integrations.langchain import AgentLensCallbackHandler
handler = AgentLensCallbackHandler(lens, run_name="my_chain")
chain.invoke(inputs, config={"callbacks": [handler]})
handler.end()

# CrewAI
from agentlens.integrations.crewai import trace_crew
trace_crew(lens, crew, run_name="research_crew").kickoff(inputs={...})
```

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Your Agent Code                       │
│   @lens.trace  @lens.span  @lens.tool  @lens.llm_call   │
└─────────────────┬───────────────────────────────────────┘
                  │  AgentRun JSON (background thread)
                  ▼
┌─────────────────────────────────────────────────────────┐
│              AgentLens Server (FastAPI)                  │
│  POST /api/ingest/run                                    │
│  GET  /api/runs   GET /api/runs/:id   POST /api/runs/diff│
└─────────────────┬───────────────────────────────────────┘
                  │
        ┌─────────┴──────────┐
        │     PostgreSQL      │
        │   runs (JSONB +     │
        │    GIN index)       │
        └─────────┬──────────┘
                  │
┌─────────────────┴───────────────────────────────────────┐
│                 AgentLens UI (React + D3)                │
│   DAG graph · span drawer · run diff · live polling      │
└─────────────────────────────────────────────────────────┘
```

## Self-hosting

### Environment variables

| Variable            | Default                                                        | Description                          |
| ------------------- | -------------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`      | `postgresql+asyncpg://agentlens:agentlens@postgres:5432/agentlens` | Postgres connection (SQLite works for dev) |
| `AGENTLENS_API_KEY` | `""` (no auth)                                                 | Require this key on ingest requests  |
| `CORS_ORIGINS`      | `http://localhost:5173`                                        | Comma-separated allowed origins      |
| `VITE_API_URL`      | `""` (demo mode)                                               | UI → server URL                      |

### Production checklist

- [ ] Set `AGENTLENS_API_KEY` to a strong random string
- [ ] Use a managed Postgres (RDS, Supabase, Neon)
- [ ] Put the server behind nginx/Caddy with TLS
- [ ] Set `CORS_ORIGINS` to your UI domain only
- [ ] Mount a persistent volume for Postgres data

## Roadmap

- [ ] Webhook alerts — Slack/email when a run exceeds budget or errors
- [ ] Eval integration — attach Ragas/custom eval scores to runs
- [ ] Timeline view — Gantt-style waterfall alongside the DAG
- [ ] LangGraph / AutoGPT native integrations
- [ ] TypeScript SDK — for LangChain.js and other JS agent frameworks
- [ ] OTEL bridge — export spans as OpenTelemetry traces
- [ ] Cloud hosted — managed AgentLens with team sharing

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: SDK stays
dependency-free, tracing never breaks the traced agent, and PRs welcome.

## License

Apache 2.0 — free to use, modify, and self-host. Commercial use permitted.

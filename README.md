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
| **Timeline / waterfall view**    |     ✅     |    ❌     |    ✅     |
| **Webhook alert rules**          |     ✅     |    ❌     |    ✅     |
| **Eval scores on the run graph** |     ✅     |    ✅     |    ❌     |
| **Quality regression in diffs**  |     ✅     |    ❌     |    ❌     |
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
- **Timeline view** — a waterfall of the same run: where wall-clock time
  actually went, which steps overlapped, and the slowest leaf step outlined
- **Run diff** — pin two runs (★), see which steps appeared, disappeared,
  flipped status, or got slower — with a one-line verdict naming the span
  where behavior first diverged
- **Quality** — eval scores per run and a sparkline per metric across runs,
  so a slow regression is visible before anyone files a bug
- **Alerts** — build webhook rules from the UI and see every rule that has
  fired, including failed deliveries

## Evals

Score a run inline, or attach results from an eval suite afterwards.

```python
from agentlens import score

@lens.trace("qa_agent")
def qa_agent(question):
    answer = generate(question)
    score("faithfulness", 0.86, source="ragas", threshold=0.85)
    return answer
```

```python
# post-hoc, from a nightly eval harness
from agentlens import from_ragas
from ragas import evaluate

result = evaluate(dataset, metrics=[faithfulness, answer_relevancy])
lens.score_run(run_id, from_ragas(result), source="ragas",
               thresholds={"faithfulness": 0.85})
```

A score below its threshold is marked failed. That failure shows on the run,
appears in a diff as a quality regression, and can trip an alert rule via the
`score:<name>`, `min_score`, or `failed_score_count` fields — so a quality
drop pages you the same way an error does. See `examples/eval_agent.py`.

## Alerts

Rules are declarative, stored server-side, and evaluated on every finished
run as a background task — a slow or broken webhook can never delay ingest
or fail the agent's export.

```bash
curl -X POST http://localhost:7430/api/alerts/rules -H 'Content-Type: application/json' -d '{
  "name": "Runs over budget",
  "field": "total_cost_usd",
  "op": "gt",
  "value": "0.50",
  "run_name": "research_agent",
  "webhook_url": "https://hooks.slack.com/services/…"
}'
```

Testable fields: `status`, `total_cost_usd`, `total_tokens`, `duration_ms`,
`span_count`, `error_span_count`, `retry_count`, `name`, `min_score`,
`failed_score_count`, and any named metric via `score:<name>`.
Operators: `gt`, `gte`, `lt`, `lte`, `eq`, `neq`, `contains`.

Payloads are Slack-compatible (`text`) and carry a structured `alert` object
for generic consumers. `POST /api/alerts/rules/{id}/test` sends a sample so
you can confirm the webhook works before you rely on it. Every firing is
recorded at `GET /api/alerts/events` with its delivery status.

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
│  POST /api/ingest/scores  GET /api/runs/scores           │
│  CRUD /api/alerts/rules   GET /api/alerts/events         │
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
│  DAG · timeline · diff · quality trends · alerts         │
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

- [x] Webhook alerts — Slack-compatible rules on any run field
- [x] Eval integration — Ragas/custom scores, thresholds, quality trends
- [x] Timeline view — Gantt-style waterfall alongside the DAG
- [ ] LangGraph / AutoGPT native integrations
- [ ] TypeScript SDK — for LangChain.js and other JS agent frameworks
- [ ] OTEL bridge — export spans as OpenTelemetry traces
- [ ] Cloud hosted — managed AgentLens with team sharing

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: SDK stays
dependency-free, tracing never breaks the traced agent, and PRs welcome.

## License

Apache 2.0 — free to use, modify, and self-host. Commercial use permitted.

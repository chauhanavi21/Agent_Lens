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
| **OTLP export _and_ ingest**     |     ✅     |   export  |    ❌     |
| **MCP tracing across processes** |     ✅     |    ❌     |    ❌     |
| **Live streaming DAG (SSE)**     |     ✅     |    ❌     |    ❌     |
| **LLM-as-judge on the trace**    |     ✅     |    ✅     |    ❌     |
| **CI gate on score regression**  |     ✅     |    ❌     |    ❌     |
| **Python _and_ TypeScript SDKs** |     ✅     |    ✅     |    ✅     |
| **Deterministic trace replay**   |     ✅     |    ❌     |    ❌     |
| **PII redaction in the SDK**     |     ✅     |  server   |    ❌     |
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

## PII redaction

Agent traces are unusually dangerous to store. A prompt is whatever the
user typed; tool arguments are whatever the agent decided to send. A trace
backend quietly accumulates support-ticket text, uploaded documents, and
API credentials nobody meant to log.

```python
lens = AgentLens(endpoint="…", redact=True)
```

Redaction runs **in the SDK, before export** — scrubbing at ingest would
still mean the raw values crossed the network and sat in an access log on
the way. (`AGENTLENS_REDACT_ON_INGEST=true` adds a server-side pass for
OTLP traffic from SDKs you don't control.)

### Policies

```python
from agentlens import AgentLens, Redactor

lens = AgentLens(endpoint="…", redact=Redactor(
    policies={
        "email": "hash",        # correlate a user across runs, store nothing
        "phone": "mask",        # keep a recognizable shape
        "credit_card": "drop",  # nothing survives
        "ipv4": "allow",        # internal service IPs are useful
    },
    extra_patterns={"order_id": r"\bORD-\d{8}\b"},
))
```

`hash` is the one that makes redacted traces still worth having: a
deterministic HMAC means the same customer produces the same token every
time, so you can group their runs and answer "did this user hit the bug
twice?" without the value being recoverable.

Detected out of the box: emails, phones, SSNs, credit cards, IBANs, IPv4,
JWTs, OpenAI/Anthropic/AWS/GitHub keys, and `Bearer` tokens — plus
field-name rules (`password`, `api_key`, `authorization`, …) that catch
short random secrets no pattern could.

### Accuracy

False positives are their own failure — a trace full of `[redacted]` is
useless. So detection is validated, not just matched:

- **Credit cards must pass Luhn.** `4111 1111 1111 1111` is redacted;
  order number `12345678901234567` is left alone.
- **IPv4 checks its context.** `1.2.3.4` after the word "version" is a
  version string, not an address.
- Ordinary text — dates, room numbers, semvers, error codes, code
  snippets — passes through untouched. There's a test asserting exactly
  that.

### Failing closed

- A redactor that throws **drops the field** rather than emitting raw data.
  The agent keeps running; the trace loses one value.
- Streaming events go through the same pass, or live view would bypass
  everything export protects.
- MCP server spans use the same path, so a tool server can't leak what the
  agent process redacts.
- `capture_content=False` drops inputs and outputs entirely — when content
  isn't needed, dropping beats scrubbing, since no detector catches
  everything. The DAG, timings, and status all survive.
- `redactor.scan(text)` reports what *would* be caught, for a dry run
  before you turn it on.

## Trace replay

A production failure is usually not reproducible: the search API returns
something else now, the rate limit cleared, the model is nondeterministic.
Replay pins the *outside world* to what it actually returned, then lets
your code run for real against it.

```python
from agentlens import Cassette, replay

def test_bug_471():
    cassette = Cassette.load("fixtures/bug-471.json")
    with replay(cassette):
        result = qa_agent("capital of France")
    assert "no source" in result
```

Pull a cassette straight from the server:

```bash
curl localhost:7430/api/runs/<run_id>/cassette > fixtures/bug-471.json
```

### What gets replayed, and what doesn't

Tool, LLM, retrieval, and MCP spans are served from the recording. Agent,
chain, and custom spans **execute normally**. That split is the whole
design — replaying the reasoning too would just be playing back a
transcript, and what you want is today's code meeting yesterday's inputs.

### Guardrails

- **Changed inputs are an error, not a silent reuse.** If your fix alters
  what a step sends, replay raises `InputMismatch` rather than serving a
  recording nobody knows applies to the new arguments. Opt out with
  `match_inputs=False` if you mean it.
- **Strict by default.** An unrecorded call raises `ReplayMiss` instead of
  quietly reaching the network — otherwise a deterministic regression test
  turns flaky again the moment someone adds a call.
- **Recorded failures replay as failures**, so the bug reproduces before
  you prove the fix.
- **Unused recordings are reported.** Making fewer calls than the original
  is a divergence worth seeing.
- **Replayed spans are labelled** in the DAG, so nobody mistakes a replay
  for real traffic.
- `divergence(original, replayed)` names the first step where the two runs
  parted ways.

Recording full outputs is opt-in (`AgentLens(record_outputs=True)`) since it
costs storage; without it a cassette falls back to truncated previews and
flags itself `truncated`.

## TypeScript SDK

Same model, same wire format, so a Python orchestrator calling a Node tool
service produces one DAG instead of two disconnected views.

```bash
npm install @agentlens/sdk
```

```ts
import { AgentLens, score } from '@agentlens/sdk';

const lens = new AgentLens({ endpoint: 'http://localhost:7430' });

const webSearch = lens.tool('web_search', async (q: string) => search(q), { retries: 2 });
const summarize = lens.llmCall('summarize', async (docs: string[]) => openai.chat(docs));

const agent = lens.trace('research_agent', async (query: string) => {
  const result = await summarize(await webSearch(query));
  score('grounding', 0.91, { threshold: 0.85 });
  return result;
}, { tags: ['prod'], maxCostUsd: 0.1 });
```

Zero runtime dependencies, full type inference through the wrappers, and
async nesting tracked with `AsyncLocalStorage` so it survives `await`,
`Promise.all`, and framework callbacks. Wrappers rather than decorators
because TypeScript decorators only apply to class methods, while most agent
code is plain functions.

Parity is enforced by test: a TS run and a Python run of the same agent are
posted to the server and diffed — they align node-for-node, with only
wall-clock latency differing. See `sdk-ts/`.

## LLM-as-judge and the CI gate

Ragas covers what you can compute. A judge covers what you can only
describe — did the agent actually answer, did it invent a tool result, did
it give up early. The judge reads the **execution trace**, not just the
final string, so it can see the retry loop that produced the answer.

```bash
curl -X POST localhost:7430/api/evals/judge \
  -d '{"run_id": "…", "rubrics": ["grounding", "task_completion"]}'
```

Built-in rubrics: `task_completion`, `tool_correctness`, `grounding`,
`efficiency`, `error_handling`. Judged scores are stored in the same shape
as inline and Ragas scores, so trends, diffs, and alerts treat them
identically.

### Gating a pull request

```bash
python -m agentlens.ci gate \
  --candidate-tag "pr-42" --baseline-tag main \
  --threshold grounding=0.85 --max-regression 0.03
```

```
1 eval check(s) failed: grounding: regressed -0.0700 (limit -0.0300)

  metric           branch  baseline   delta  result
  ---------------  ------  --------  ------  ------
  grounding         0.850     0.920  -0.070  FAIL  (regressed -0.0700)
  task_completion   0.905     0.900  +0.005  pass
```

The gate asks two questions, and the second is the one that earns its keep:

1. **Absolute** — is any metric below its floor? Catches a branch that was
   always bad.
2. **Relative** — did any metric drop more than `--max-regression` against
   the baseline? Catches a branch that made things *worse* while still
   passing every fixed threshold. `0.92 → 0.86` clears a 0.85 floor and is
   exactly the drift nobody notices until it's three releases old.

Also enforced: a metric the baseline measured but this branch stopped
producing fails as lost coverage, and errored runs fail by default.

Exit codes are `0` pass, `1` failed checks, `2` usage or connection error —
so an unreachable server can never read as a clean gate. See
`.github/workflows/eval-gate.yml` for a workflow that runs the suite, gates
the PR, and comments the table back.

## Live streaming

Batch tracing sends one payload when a run ends — so a run that hangs, gets
OOM-killed, or is simply still going never appears at all. Those are the
runs you most want to see.

```python
from agentlens import AgentLens, StreamExporter

lens = AgentLens(exporter=StreamExporter("http://localhost:7430"))
```

Each span is pushed as it opens and closes, and the UI subscribes over SSE
at `/api/stream`, so the DAG draws itself node by node while the agent
works. `GET /api/live/runs` lists what's executing right now; opening a live
run shows its partial DAG immediately.

Design notes:

- **Events are best-effort, the final run is the source of truth.** The
  exporter's queue is bounded and drops oldest-first, so a slow or dead
  server costs you the live view — never the agent's memory or its data.
- **Live state is in memory and disposable.** Persistence happens on the
  run's final export. A multi-process deployment swaps the broker for Redis
  pub/sub; the interface is small enough to be a drop-in.
- **Spans arriving before `run_start` are kept**, not dropped, so a browser
  connecting mid-run still sees a coherent DAG.
- **Reconnects trust the server's snapshot**, since `EventSource` can't tell
  you whether the gap lost events.

## MCP tracing

An MCP server is a peer service, and the interesting failures live on the
far side of the boundary. From the agent alone, "the tool was slow" and
"the model misread the result" look identical. MCP carries W3C trace
context in `params._meta`, so both sides can join one trace — across stdio
pipes as well as HTTP.

**Agent side** — wrap the client session:

```python
from agentlens import trace_mcp_session

session = trace_mcp_session(lens, session, server_name="github")
await session.call_tool("create_issue", {"title": "..."})
```

**Server side** — decorate the tool handler:

```python
from agentlens import mcp_server_span

@mcp_server_span(lens, server_name="github")
async def create_issue(arguments=None, _meta=None):
    ...   # spans recorded here nest inside the caller's DAG
```

The result is one waterfall:

```
issue_agent
  └── create_issue          (mcp · agent process)
        └── create_issue    (mcp · github server)
              └── github_api_post   412ms   ← the actual latency
```

Details worth knowing:

- **Either side can arrive first.** Stitching happens at read time on the
  shared trace id, so a late server run still merges and neither service
  has to know about the other's storage.
- **Server runs don't clutter the run list.** They appear inside the
  caller's DAG; pass `?include_remote=true` to list them on their own.
- **`isError` payloads count as failures.** MCP reports tool errors in the
  response body rather than by raising, which is easy to miss.
- **An unstitched server run is still readable** rather than silently
  dropped, so a server stays observable when its caller isn't instrumented.

## OpenTelemetry bridge

AgentLens speaks OTLP in both directions, using the OpenTelemetry GenAI
semantic conventions (v1.41.0).

**Out** — send agent traces to any OTel backend alongside AgentLens, so they
sit beside the rest of your telemetry instead of in a silo:

```python
from agentlens import AgentLens, HttpExporter
from agentlens.otel import MultiExporter, OTLPExporter

lens = AgentLens(exporter=MultiExporter(
    HttpExporter("http://localhost:7430"),                        # AgentLens UI
    OTLPExporter("http://localhost:4318", service_name="my-agent"),  # collector
))
```

A run arrives in Grafana, Tempo, Honeycomb, Jaeger, or Datadog as a proper
span tree:

```
invoke_agent research_agent
  ├── execute_tool web_search
  ├── retrieval retrieve_docs
  └── chat claude-sonnet-4      gen_ai.usage.input_tokens=1980
```

**In** — point any OTel exporter at `/api/ingest/otlp` and traces from other
SDKs become AgentLens runs, with the DAG, diffing, and alerting on top. No
SDK swap needed. See `otel-collector-config.yaml` for a collector that fans
traces to both at once.

### Notes on the spec

Every `gen_ai.*` attribute still carries **Development** stability in the
OTel registry, so names can change without a major version bump. AgentLens
dual-emits by default: GenAI attributes plus `agentlens.*` ones, which
carry what the spec has no place for yet — retry lineage, per-call cost,
and eval scores — and are namespaced so they can't collide with a future
OTel addition. Set `dual_emit=False` for pure convention output.

Prompt and completion content is **not** exported by default, since prompts
routinely carry user data. Opt in with `capture_content=True` or
`OTEL_GENAI_CAPTURE_MESSAGE_CONTENT=true`.

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

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                    Your Agent Code                       │
│   @lens.trace / lens.trace()  ·  Python + TypeScript      │
└─────────────────┬───────────────────────────────────────┘
                  │  AgentRun JSON (background thread)
                  ▼
┌─────────────────────────────────────────────────────────┐
│              AgentLens Server (FastAPI)                  │
│  POST /api/ingest/run                                    │
│  GET  /api/runs   GET /api/runs/:id   POST /api/runs/diff│
│  (runs/:id stitches remote MCP spans into one DAG)       │
│  POST /api/ingest/scores  POST /api/ingest/otlp          │
│  POST /api/ingest/event   GET  /api/stream (SSE)         │
│  POST /api/evals/judge    POST /api/evals/gate           │
│  GET  /api/runs/scores                                   │
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
│  live DAG · timeline · diff · quality · alerts           │
└─────────────────────────────────────────────────────────┘
```

## Self-hosting

### Environment variables

| Variable            | Default                                                        | Description                          |
| ------------------- | -------------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`      | `postgresql+asyncpg://agentlens:agentlens@postgres:5432/agentlens` | Postgres connection (SQLite works for dev) |
| `AGENTLENS_API_KEY` | `""` (no auth)                                                 | Require this key on ingest requests  |
| `AGENTLENS_HASH_SECRET` | `agentlens`                                                | Salt for redaction fingerprints      |
| `AGENTLENS_REDACT_ON_INGEST` | `false`                                               | Scrub foreign OTLP traces server-side |
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
- [x] MCP tracing — W3C trace context across MCP tool calls, cross-process DAG stitching
- [x] Live streaming ingest — SSE, live DAG, partial runs visible mid-flight
- [x] LLM-as-judge evals + CI gate that fails a PR on score regression
- [x] LangGraph / OpenAI Agents SDK / Pydantic AI integrations
- [x] TypeScript SDK — zero-dependency, wire-compatible with the Python SDK
- [x] OTEL bridge — OTLP export and ingest, GenAI semantic conventions
- [ ] Cloud hosted — managed AgentLens with team sharing

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: SDK stays
dependency-free, tracing never breaks the traced agent, and PRs welcome.

## License

Apache 2.0 — free to use, modify, and self-host. Commercial use permitted.

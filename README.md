<div align="center">

# ◉ AgentLens

**Open source observability runtime for AI agents.**
*Langfuse traces LLM calls. AgentLens traces the whole agent.*

[![CI](https://github.com/chauhanavi21/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/chauhanavi21/agentlens/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentlens)](https://pypi.org/project/agentlens/)
[![npm](https://img.shields.io/npm/v/@agentlens/sdk)](https://www.npmjs.com/package/@agentlens/sdk)
[![Python](https://img.shields.io/pypi/pyversions/agentlens)](https://pypi.org/project/agentlens/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

`pip install agentlens` · `npm i @agentlens/sdk` · Apache 2.0 · zero SDK dependencies

[Architecture & design decisions →](ARCHITECTURE.md)

<img src="assets/architecture.svg" alt="AgentLens architecture: agent process and MCP tool server emitting to the AgentLens server, which stores runs in Postgres, mirrors them to OTel backends, and serves the UI over REST and SSE" width="100%">

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
def summarize(docs): ...  # token usage auto-extracted from the response
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

### 3. Fill it with something to look at

An empty observability tool is impossible to evaluate, so there's a seeder:

```bash
python scripts/seed_demo.py          # ~45 runs across a week
python scripts/seed_demo.py --live   # then stream one in real time
```

That gives you three agents with different shapes, real failure modes
(retries, rate limits, budget pauses), an MCP trace stitched across two
processes, alert rules that have already fired, and a deliberate quality
regression in the last third of the window — so the Quality tab shows a
real downward trend and this actually fails:

```bash
python -m agentlens.ci gate --candidate-tag pr-118 --baseline-tag main \
  --threshold faithfulness=0.85 --max-regression 0.03
```

Everything it generates is synthetic — no API keys, no calls to model
providers. For a single traced run instead, `python examples/demo_agent.py`.

### 4. What you'll see

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

lens = AgentLens(
    endpoint="…",
    redact=Redactor(
        policies={
            "email": "hash",  # correlate a user across runs, store nothing
            "phone": "mask",  # keep a recognizable shape
            "credit_card": "drop",  # nothing survives
            "ipv4": "allow",  # internal service IPs are useful
        },
        extra_patterns={"order_id": r"\bORD-\d{8}\b"},
    ),
)
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
async def create_issue(arguments=None, _meta=None): ...  # spans recorded here nest inside the caller's DAG
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

lens = AgentLens(
    exporter=MultiExporter(
        HttpExporter("http://localhost:7430"),  # AgentLens UI
        OTLPExporter("http://localhost:4318", service_name="my-agent"),  # collector
    )
)
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
lens.score_run(run_id, from_ragas(result), source="ragas", thresholds={"faithfulness": 0.85})
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
    endpoint="http://localhost:7430",  # or exporter=FileExporter("runs.jsonl")
    api_key="your-key",  # optional server auth
    on_budget="raise",  # "raise" | "pause" | "warn"
)


@lens.trace("my_agent", tags=["prod"], max_total_tokens=5000, max_cost_usd=0.05)
def my_agent(query): ...


@lens.span("retrieve", kind=SpanKind.RETRIEVAL)
def retrieve(query): ...


@lens.tool("web_search", retries=2)  # failed attempts stay in the DAG,
def web_search(query): ...  # linked by retry lineage


@lens.llm_call("chat", model="gpt-4o")  # auto token/cost from OpenAI- and
def chat(prompt): ...  # Anthropic-style responses
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

The decisions behind this — why spans are JSONB on the run row, why MCP
stitching happens at read time, why the CI gate checks relative regression,
why redaction runs SDK-side — are written up in
**[ARCHITECTURE.md](ARCHITECTURE.md)**, along with known limitations.

<img src="assets/architecture.svg" alt="AgentLens architecture" width="100%">

Three processes, deliberately. The SDK must be safe to embed in anything;
the server owns storage and cross-run analysis; the UI is a plain client of
the API, so everything it does is scriptable.

## Cross-run analytics

A single run's DAG can't tell you whether `web_search` is always slow or
that run was unlucky. A derived span index answers that:

```bash
curl 'localhost:7430/api/analytics/spans?days=7'
```

```json
{"name": "synthesize", "kind": "llm", "calls": 412, "error_rate": 0.031,
 "retry_rate": 0.12, "p50_ms": 1840, "p95_ms": 4210, "p99_ms": 9800,
 "total_cost_usd": 5.21}
```

- `/api/analytics/spans` — per-step call counts, error and retry rates,
  p50/p95/p99 latency, tokens, and cost, ordered by total wall-clock time.
- `/api/analytics/models` — cost and tokens by model, so you can see where
  the money goes.
- `/api/analytics/outliers` — individual spans that ran far past their own
  p95, worst first. Aggregates say a step is slow; this says which run to
  open.

The index is **derived** from the JSONB on each run, not a second source of
truth. It's written on ingest and rebuildable at any time
(`POST /api/analytics/reindex`), so the worst case of it drifting is wasted
work rather than lost data.

## Data lifecycle

A trace store that only grows eventually gets deleted by whoever pays for
the disk — but deleting traces is also how you lose the run someone was
about to investigate. So retention is **off by default** and conservative
when on.

```bash
AGENTLENS_RETENTION_DAYS=30                    # drop runs older than 30 days
AGENTLENS_RETENTION_MAX_RUNS_PER_AGENT=1000    # keep the newest N per agent
AGENTLENS_PROTECT_TAGS=keep,incident           # never touch these
```

Or on demand — note that `dry_run` defaults to true, because deletion is
irreversible and the safe path should be what you get by forgetting a
parameter:

```bash
curl -X POST localhost:7430/api/runs/prune \
  -d '{"older_than_days": 30, "dry_run": true}'
```

Every selected run comes back with the reason it was chosen. Design points:

- **Count limits are per agent, not global.** "Keep the last 1000 runs" on a
  system where one agent runs 100x more often silently erases the quiet
  agent's entire history — usually the one you're debugging.
- **Deleting a run follows its trace forward.** An MCP server's run is only
  meaningful stitched into its caller, so it goes too. The reverse never
  happens: pruning a tool server's history can't destroy the agent traces
  referencing it.
- **A zero or negative retention value is ignored**, since it would mean
  "delete everything" and nobody types that on purpose.
- **The sweep runs shortly after startup**, not one interval later — a
  server restarting every few hours would otherwise never reach its first
  sweep.

Long histories page with a cursor rather than an offset (`/api/runs/page`):
with offsets, runs arriving at the head while you scroll shift every
subsequent page and you see duplicates.

## Self-hosting

### Environment variables

| Variable            | Default                                                        | Description                          |
| ------------------- | -------------------------------------------------------------- | ------------------------------------ |
| `DATABASE_URL`      | `postgresql+asyncpg://agentlens:agentlens@postgres:5432/agentlens` | Postgres connection (SQLite works for dev) |
| `AGENTLENS_API_KEY` | `""` (no auth)                                                 | Require this key on ingest requests  |
| `AGENTLENS_HASH_SECRET` | `agentlens`                                                | Salt for redaction fingerprints      |
| `AGENTLENS_RETENTION_DAYS` | _(off)_                                                 | Drop runs older than this            |
| `AGENTLENS_RETENTION_MAX_RUNS_PER_AGENT` | _(off)_                                   | Keep the newest N runs per agent     |
| `AGENTLENS_PROTECT_TAGS` | `keep`                                                    | Tags retention never deletes         |
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

## Development

```bash
make install     # SDKs, server, UI
make test        # every suite: python, server, typescript, interop
make lint        # ruff check + format check
make up          # docker compose: postgres + server + UI
```

CI runs the Python SDK across 3.9–3.13 (plus macOS and Windows), the server
across 3.10–3.13 and against real Postgres, the TypeScript SDK on Node
18/20/22, the UI test suite and build on Node 20/22, a cross-language
wire-compatibility check, lint, and both Docker images.

Test counts: **51** Python SDK, **82** server, **16** TypeScript SDK, and
**56** UI — the last of which run entirely in demo mode, so they double as a
check that the offline experience works with no server at all. Releases publish to PyPI via trusted publishing and to
npm with provenance — no long-lived tokens in either.

## Performance

"How much does tracing cost?" is the first question worth asking an
observability SDK, so there's a suite that answers it:

```bash
python scripts/benchmark.py
```

Measured on one core, Python 3.12, median of seven batches with GC disabled
during timing:

| Case | Median | Added | Share of one 800ms LLM call |
| --- | ---: | ---: | ---: |
| Untraced function call (baseline) | 1.2µs | — | — |
| Decorated, no active run | 1.5µs | +0.2µs | 0.0000% |
| Run with 1 span | 22µs | +21µs | 0.0026% |
| Run with 6 spans | 78µs | +77µs | 0.0096% |
| Run with 1 LLM span | 45µs | +44µs | 0.0055% |
| 2 spans + redaction | 120µs | +119µs | 0.0149% |

**~13µs per span**, ~560 bytes per span held until export, and **12,600
runs/sec** (75,900 spans/sec) sustained on a single core. Export runs on a
background thread and is excluded — these are what the agent's own thread
pays.

The comparison that matters is the last column. Overhead as a percentage of
a 1.2µs no-op reads like a catastrophe and means nothing; against the work
an agent actually does between spans, a fully traced six-span run costs
about a hundredth of a percent of one model call.

### What optimization actually taught me

Redaction dominated the export path at ~50µs per string. Two attempts:

- **Combining all 13 detectors into one regex alternation** — no measurable
  gain, so it was reverted. It was added complexity buying nothing.
- **A trigger-character pre-filter** — every built-in detector's matches
  contain a digit, an `@`, or one of a few literals, so one cheap check up
  front lets ordinary prose skip every detector. **56µs → 4µs**, a 12x win
  on the common case.

The pre-filter is only sound because that over-approximation holds, so
there's a test asserting every secret type contains a trigger hint, and
custom patterns disable the fast path rather than being guessed at. A
performance optimization that silently becomes a data leak is the worst
possible trade.

## Releases

Every package is versioned together — they share a wire format, and letting
them drift would mean maintaining a compatibility matrix. `scripts/release.py`
keeps the seven version strings in sync and CI fails a pull request if they
disagree.

```bash
python scripts/release.py check      # do all seven agree?
python scripts/release.py bump 0.4.0 # set them together
python scripts/release.py plan 0.4.0 # the release checklist
```

Publishing a GitHub release triggers PyPI (trusted publishing) and npm
(with provenance). See [CHANGELOG.md](CHANGELOG.md).

## Security

Redaction bypasses and ingest auth issues are the areas most worth
scrutiny — see [SECURITY.md](SECURITY.md), which also lists the limitations
that are deliberate (single shared token, best-effort pattern matching).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: SDK stays
dependency-free, tracing never breaks the traced agent, and PRs welcome.

## License

Apache 2.0 — free to use, modify, and self-host. Commercial use permitted.

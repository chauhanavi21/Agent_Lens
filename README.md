<div align="center">

# ◉ AgentLens

**Open source observability runtime for AI agents.**
*Langfuse traces LLM calls. AgentLens traces the whole agent.*

[![CI](https://github.com/chauhanavi21/agentlens/actions/workflows/ci.yml/badge.svg)](https://github.com/chauhanavi21/agentlens/actions/workflows/ci.yml)
[![PyPI](https://img.shields.io/pypi/v/agentlens)](https://pypi.org/project/agentlens/)
[![npm](https://img.shields.io/npm/v/@agentlens/sdk)](https://www.npmjs.com/package/@agentlens/sdk)
[![Python](https://img.shields.io/pypi/pyversions/agentlens)](https://pypi.org/project/agentlens/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)
[![Docs](https://img.shields.io/badge/docs-agentlens-blue)](https://chauhanavi21.github.io/Agent_Lens)

`pip install agentlens` · `npm i @agentlens/sdk` · Apache 2.0 · zero SDK dependencies

**[Documentation](https://chauhanavi21.github.io/Agent_Lens)** · [Architecture & design decisions](ARCHITECTURE.md)

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

```bash
pip install agentlens          # or: npm i @agentlens/sdk
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
def summarize(docs): ...  # token usage and cost captured automatically
```

Every decorated function becomes a node in the DAG. Nesting, async, and
retries are captured automatically. Export happens on a background thread —
tracing never blocks the agent, and never crashes it.

Then run the stack and give it something to show:

```bash
git clone https://github.com/chauhanavi21/Agent_Lens && cd Agent_Lens
docker compose up                    # server :7430, UI :5173
python scripts/seed_demo.py          # ~45 runs of realistic history
```

**→ Full walkthrough: [Tracing your agent](https://chauhanavi21.github.io/Agent_Lens/guides/tracing/)**

## Documentation

| | |
| --- | --- |
| [Tracing your agent](https://chauhanavi21.github.io/Agent_Lens/guides/tracing/) | Decorators, spans, retries, budget guards |
| [TypeScript SDK](https://chauhanavi21.github.io/Agent_Lens/guides/typescript/) | Same wire format, zero dependencies |
| [Framework integrations](https://chauhanavi21.github.io/Agent_Lens/guides/frameworks/) | LangGraph, CrewAI, OpenAI Agents SDK, Pydantic AI, LangChain |
| [Evals and the CI gate](https://chauhanavi21.github.io/Agent_Lens/guides/evals/) | Scores, LLM-as-judge, failing a PR on regression |
| [Trace replay](https://chauhanavi21.github.io/Agent_Lens/guides/replay/) | Turn a production failure into a deterministic test |
| [MCP tracing](https://chauhanavi21.github.io/Agent_Lens/reference/mcp/) | One waterfall across the agent and its tool servers |
| [OpenTelemetry bridge](https://chauhanavi21.github.io/Agent_Lens/reference/opentelemetry/) | OTLP export and ingest |
| [Live streaming](https://chauhanavi21.github.io/Agent_Lens/reference/streaming/) | Watch a DAG build itself over SSE |
| [Cross-run analytics](https://chauhanavi21.github.io/Agent_Lens/reference/analytics/) | p95 latency per step, cost by model, outliers |
| [Performance](https://chauhanavi21.github.io/Agent_Lens/reference/performance/) | ~13µs per span, measured |
| [Self-hosting](https://chauhanavi21.github.io/Agent_Lens/operations/self-hosting/) | Environment, Docker, production checklist |
| [PII redaction](https://chauhanavi21.github.io/Agent_Lens/operations/privacy/) | Mask, hash, and drop policies |
| [Data lifecycle](https://chauhanavi21.github.io/Agent_Lens/operations/data-lifecycle/) | Retention, pruning, pagination |

## Architecture

The decisions behind this — why spans are JSONB on the run row, why MCP
stitching happens at read time, why the CI gate checks relative regression,
why redaction runs SDK-side — are written up in
**[ARCHITECTURE.md](ARCHITECTURE.md)**, along with known limitations.

<img src="assets/architecture.svg" alt="AgentLens architecture" width="100%">

Three processes, deliberately. The SDK must be safe to embed in anything;
the server owns storage and cross-run analysis; the UI is a plain client of
the API, so everything it does is scriptable.

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
make test        # python, server, typescript, ui, interop
make lint        # ruff + version sync
make bench       # tracing overhead
make up          # docker compose
make docs        # serve the docs site locally
```

CI runs the Python SDK across 3.9–3.13 (plus macOS and Windows), the server
across 3.10–3.13 and against real PostgreSQL, the TypeScript SDK on Node
18/20/22, the UI suite on Node 20/22, a cross-language wire-compatibility
check, lint, and both Docker images.

Test counts: **58** Python SDK, **120** server, **16** TypeScript SDK,
**60** UI.

## Security

Redaction bypasses and ingest auth issues are the areas most worth
scrutiny — see [SECURITY.md](SECURITY.md), which also lists the limitations
that are deliberate (single shared token, best-effort pattern matching).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). The short version: SDK stays
dependency-free, tracing never breaks the traced agent, and PRs welcome.

## License

Apache 2.0 — free to use, modify, and self-host. Commercial use permitted.

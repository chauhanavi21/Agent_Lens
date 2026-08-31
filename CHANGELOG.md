# Changelog

All notable changes to AgentLens. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions follow
[semantic versioning](https://semver.org/spec/v2.0.0.html).

The Python SDK (`agentlens`), the TypeScript SDK (`@agentlens/sdk`), and the
server are versioned together — they share a wire format, and letting them
drift would mean tracking a compatibility matrix nobody wants to maintain.

## [Unreleased]

### Added

- **Data retention** — age and per-agent count rules, protected tags, a
  background sweep that runs shortly after startup, `DELETE /api/runs/{id}`,
  and `POST /api/runs/prune` which defaults to a dry run and explains why
  each run was selected.
- **Cursor pagination** (`/api/runs/page`) so a long history scrolls without
  the duplicate rows an offset produces under live traffic, plus "Load more"
  in the UI.
- **UI test suite** — 56 tests covering the D3 graph, the timeline, the SSE
  hook's event folding, the span drawer, run pinning and diffing, and the
  API client's demo mode. Runs on Node 20 and 22 in CI.
- Benchmark suite (`scripts/benchmark.py`) measuring per-span overhead,
  memory, and throughput, plus performance guards in the test suite and a
  weak-reference check that finished runs aren't retained.

### Fixed

- The FastAPI app served a hardcoded version that had drifted a full release
  behind. It now reads `__version__`, with a test keeping it derived.

### Changed

- Redaction skips the detector passes when a string contains none of the
  characters any detector could match — 56µs → 4µs on ordinary prose.
  Custom patterns disable the fast path, since their matches may contain no
  trigger character.

## [0.3.0] — 2026-08-26

The release that makes the project's claims testable: CI enforces the
version support it advertises, and the demo seeder means a fresh install
opens to something worth looking at.

### Added

- **Framework integrations** for the OpenAI Agents SDK (`TracingProcessor`),
  LangGraph (per-node spans tagged with the state keys each one changed),
  and Pydantic AI (wraps the agent and its registered tools). All read
  framework objects by duck typing, so importing `agentlens` never pulls in
  a framework.
- **Demo seeder** (`scripts/seed_demo.py`) — generates a week of realistic
  history across three agents, including retries, rate limits, budget
  pauses, a stitched MCP trace, alert rules that have already fired, and a
  deliberate quality regression the CI gate can catch.
- **CI across the full support matrix**: Python 3.9–3.13 on Linux plus macOS
  and Windows, the server on 3.10–3.13 and against real PostgreSQL,
  TypeScript on Node 18/20/22, cross-language wire compatibility, UI build,
  lint, and both container images.
- **Publishing workflows** — PyPI via trusted publishing (OIDC, no
  long-lived token) and npm with provenance. Both verify the release tag
  matches the packaged version before uploading.
- **Release tooling** (`scripts/release.py`) — checks that all seven version
  strings agree, bumps them together, and prints the release checklist.
- **`ARCHITECTURE.md`** — the design decisions, what they cost, and the
  known limitations.
- Server test suite: 57 tests covering ingest, diff, alerts, evals, the
  gate, OTLP, stitching, live streaming, cassettes, and the seeder.
- `Makefile`, issue and pull request templates, Dependabot, and a rendered
  SVG architecture diagram.

### Fixed

- **Python 3.9 support was advertised but broken.** `traceback.format_exception(exc)`
  is 3.10+; `agentlens/compat.py` now handles both signatures. The shim
  initially missed the framework integrations and CI caught it, so a test
  now scans the package for direct calls — a linter can't see this, since
  the arity change is a stdlib signature difference rather than syntax.
- **Node 18 support was advertised but shaky.** `globalThis.crypto` is only
  reliable from Node 19; the TypeScript SDK now falls back to `node:crypto`.
- The demo seeder created alert rules *after* ingesting runs, so nothing
  could fire and the Alerts tab stayed empty. Rules are created first.
- A dead variable in the score-diff logic, and exception chaining across the
  server routers so the original cause survives.

## [0.2.0] — 2026-08-19

### Added

- **PII redaction** in the SDK, before export — mask, hash, and drop
  policies, with Luhn validation on card numbers and context checks on IPv4
  so version strings aren't mistaken for addresses. A failing redactor drops
  the field rather than emitting raw data.
- **Trace replay** — cassettes recorded from a run, replayed so tool, LLM,
  retrieval, and MCP calls are served from the recording while the agent's
  own code runs for real. Changed inputs raise `InputMismatch` rather than
  serving a recording that no longer applies.
- **TypeScript SDK** (`@agentlens/sdk`) — zero dependencies, wire-compatible
  with the Python SDK, async context via `AsyncLocalStorage`.
- **LLM-as-judge evaluation** reading the execution trace rather than just
  the final answer, plus a **CI gate** that fails a build on relative score
  regression, not only on absolute floors.
- **Live streaming** over SSE — spans pushed as they open and close, so a
  run that hangs or is still executing is visible instead of invisible.
- **MCP tracing** with W3C trace context in `params._meta`, and read-time
  stitching so an agent and the tool servers it calls form one DAG.
- **OpenTelemetry bridge**, both directions: export to any OTel backend
  using the GenAI semantic conventions, and ingest OTLP from any other SDK.
- Eval scores, quality trends, webhook alert rules, and the timeline view.

## [0.1.0] — 2026-08-11

### Added

- Python SDK with `@lens.trace`, `@lens.span`, `@lens.tool`, and
  `@lens.llm_call`; retry lineage; budget guards.
- FastAPI server with PostgreSQL storage and run diffing.
- React + D3 UI: execution DAG, span drawer, run list.
- LangChain and CrewAI integrations, Docker Compose, Apache 2.0.

[Unreleased]: https://github.com/chauhanavi21/Agent_Lens/compare/v0.3.0...HEAD
[0.3.0]: https://github.com/chauhanavi21/Agent_Lens/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/chauhanavi21/Agent_Lens/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/chauhanavi21/Agent_Lens/releases/tag/v0.1.0

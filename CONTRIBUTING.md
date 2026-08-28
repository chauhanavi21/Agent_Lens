# Contributing to AgentLens

## Dev setup

```bash
git clone https://github.com/chauhanavi21/agentlens
cd agentlens

# SDK (zero deps)
cd sdk && pip install -e ".[dev]" && python test_sdk.py

# Server — SQLite dev mode needs no Postgres
cd ../server && pip install -e .
DATABASE_URL=sqlite+aiosqlite:///./dev.db uvicorn agentlens_server.main:app --reload --port 7430

# UI
cd ../ui && npm install
VITE_API_URL=http://localhost:7430 npm run dev
```

Read **[ARCHITECTURE.md](ARCHITECTURE.md)** first if you're changing
anything structural — it explains the constraints the guidelines below come
from, and the known limitations worth working on.

## Guidelines

- The SDK stays dependency-free. Integrations that need a framework go in
  `agentlens/integrations/` behind optional imports.
- Tracing must never crash or block the traced agent. Exporters swallow
  network errors; decorators pass through when no run is active.
- New span kinds need: enum value, UI color token, legend entry.
- Add a test in `sdk/test_sdk.py` or a server test for anything behavioral.
  Negative cases especially: most real bugs here surfaced from tests
  asserting what *shouldn't* happen (redaction leaving normal text alone,
  replay refusing changed inputs, the gate failing on a missing metric).
- Framework adapters read objects by duck typing and are tested against
  fakes of the documented interface. Don't import a framework's types.

## The UI

```bash
cd ui && npm install && npm test     # vitest + testing-library, jsdom
```

Tests run against demo mode with `fetch` stubbed to reject, so anything that
reaches for the network fails loudly rather than hanging. The SSE hook is
driven by a fake `EventSource`.

## The TypeScript SDK

```bash
cd sdk-ts && npm install && npm test
```

Both SDKs write the same wire format, enforced by an interop test that
diffs a TS run against a Python run of the same agent. If you change a
field on one side, change it on both.

## Releases

Bump the version in `sdk/pyproject.toml`, `server/pyproject.toml`, and
`ui/package.json` together.

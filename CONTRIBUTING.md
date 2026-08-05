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

## Guidelines

- The SDK stays dependency-free. Integrations that need a framework go in
  `agentlens/integrations/` behind optional imports.
- Tracing must never crash or block the traced agent. Exporters swallow
  network errors; decorators pass through when no run is active.
- New span kinds need: enum value, UI color token, legend entry.
- Add a test in `sdk/test_sdk.py` or a server test for anything behavioral.

## Releases

Bump the version in `sdk/pyproject.toml`, `server/pyproject.toml`, and
`ui/package.json` together.

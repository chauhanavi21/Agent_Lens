"""
Shared fixtures.

Each test gets its own SQLite file so cases can't leak state into each
other, and the app is imported *after* DATABASE_URL is set — the engine is
built at import time, so ordering matters here.
"""

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "sdk"))


@pytest.fixture
def db_url(tmp_path, monkeypatch):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    monkeypatch.setenv("DATABASE_URL", url)
    return url


@pytest.fixture
async def client(db_url):
    """An ASGI client with the app's lifespan run, so tables exist."""
    for module in [m for m in list(sys.modules) if m.startswith("agentlens_server")]:
        del sys.modules[module]

    from agentlens_server.main import app
    from httpx import ASGITransport, AsyncClient

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://test", timeout=30) as c,
    ):
        yield c


@pytest.fixture
def make_run():
    """
    Produce a real run by tracing a small agent, rather than hand-writing
    JSON. Hand-written fixtures drift from what the SDK actually emits —
    which is exactly the bug class these tests exist to catch.
    """
    from agentlens import AgentLens, FileExporter, SpanKind, score

    def _make(name="qa_agent", tags=(), faithfulness=0.92, fail=False, tokens=None):
        path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
        lens = AgentLens(exporter=FileExporter(path))

        @lens.tool("web_search")
        def web_search(q):
            return ["doc about " + q]

        @lens.span("summarize", kind=SpanKind.LLM)
        def summarize(docs):
            if fail:
                raise TimeoutError("model timeout")
            return "summary"

        @lens.trace(name, tags=list(tags))
        def agent(q):
            try:
                return summarize(web_search(q))
            finally:
                score("faithfulness", faithfulness, source="ragas", threshold=0.85)

        try:
            agent("paris")
        except TimeoutError:
            pass
        return json.loads(open(path).read().strip())

    return _make

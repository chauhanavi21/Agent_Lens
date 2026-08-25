#!/usr/bin/env python3
"""
Cross-language wire compatibility check.

Runs the same agent in Python and TypeScript, posts both to the server, and
asks the server's own diff engine to compare them. Identical structure means
the two SDKs genuinely agree — a claim worth testing rather than asserting,
since a polyglot system producing two disconnected DAGs is the failure this
project exists to avoid.

    python scripts/interop_check.py

Exits non-zero on any divergence beyond wall-clock timing.
"""

from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "sdk"))

os.environ.setdefault("DATABASE_URL", f"sqlite+aiosqlite:///{tempfile.mkdtemp()}/interop.db")

# Braces are everywhere in JS, so this uses a sentinel rather than
# str.format or an f-string.
TS_AGENT = """
import { AgentLens, MemoryExporter, score } from '__DIST__/src/index.js';

const exporter = new MemoryExporter();
const lens = new AgentLens({ exporter });

const search = lens.tool('web_search', async () => ['d'], { retries: 1 });
const synth = lens.llmCall('synthesize', async () => ({
  model: 'gpt-4o',
  usage: { prompt_tokens: 1000, completion_tokens: 500 },
}));

const agent = lens.trace('interop_agent', async (q) => {
  await search(q);
  await synth(q);
  score('faithfulness', 0.91, { source: 'ragas', threshold: 0.85 });
  return 'done';
}, { tags: ['ts', 'interop'] });

await agent('q');
process.stdout.write(JSON.stringify(exporter.runs[0]));
"""


def typescript_run() -> dict:
    dist = ROOT / "sdk-ts" / "dist"
    if not dist.exists():
        raise SystemExit("sdk-ts is not built. Run: cd sdk-ts && npm install && npm run build")

    script = Path(tempfile.mkdtemp()) / "agent.mjs"
    script.write_text(TS_AGENT.replace("__DIST__", dist.as_uri()))
    result = subprocess.run(["node", str(script)], capture_output=True, text=True)
    if result.returncode != 0:
        raise SystemExit(f"TypeScript agent failed:\n{result.stderr}")
    return json.loads(result.stdout)


def python_run() -> dict:
    from agentlens import AgentLens, FileExporter, score

    path = os.path.join(tempfile.mkdtemp(), "runs.jsonl")
    lens = AgentLens(exporter=FileExporter(path))

    @lens.tool("web_search", retries=1)
    def web_search(q):
        return ["d"]

    @lens.llm_call("synthesize", model="gpt-4o")
    def synthesize(q):
        class Response:
            model = "gpt-4o"
            usage = type("Usage", (), {"prompt_tokens": 1000, "completion_tokens": 500})()

        return Response()

    @lens.trace("interop_agent", tags=["py", "interop"])
    def agent(q):
        web_search(q)
        synthesize(q)
        score("faithfulness", 0.91, source="ragas", threshold=0.85)
        return "done"

    agent("q")
    return json.loads(open(path).read().strip())


async def main() -> int:
    from agentlens_server.main import app
    from httpx import ASGITransport, AsyncClient

    ts, py = typescript_run(), python_run()

    async with (
        app.router.lifespan_context(app),
        AsyncClient(transport=ASGITransport(app=app), base_url="http://interop") as c,
    ):
        for run in (ts, py):
            posted = await c.post("/api/ingest/run", json=run)
            if posted.status_code != 201:
                print(f"server rejected a run: {posted.status_code} {posted.text}")
                return 1

        stored_ts = (await c.get(f"/api/runs/{ts['run_id']}")).json()
        diff = (await c.post("/api/runs/diff", json={"run_a": ts["run_id"], "run_b": py["run_id"]})).json()

    problems = []

    if stored_ts["total_tokens"] != 1500:
        problems.append(f"TS token total is {stored_ts['total_tokens']}, expected 1500")
    if stored_ts["total_cost_usd"] != 0.0075:
        problems.append(f"TS cost is {stored_ts['total_cost_usd']}, expected 0.0075")
    if len(stored_ts["trace_id"]) != 32 or len(stored_ts["spans"][0]["span_id"]) != 16:
        problems.append("TS id widths do not match the OTLP-compatible format")
    if not stored_ts["scores"] or stored_ts["scores"][0]["passed"] is not True:
        problems.append("TS scores did not survive ingest")

    summary = diff["summary"]
    if summary["added"] or summary["removed"]:
        problems.append(f"DAGs differ structurally: +{summary['added']} -{summary['removed']}")

    # Only wall-clock timing may differ between the two runtimes.
    changed_fields = {field for c in diff["changed"] for field in c["changes"]}
    unexpected = changed_fields - {"duration_ms"}
    if unexpected:
        problems.append(f"unexpected differences beyond timing: {sorted(unexpected)}")

    if problems:
        print("Interop check FAILED:")
        for p in problems:
            print(f"  - {p}")
        print(f"\nverdict: {summary['verdict']}")
        return 1

    print("Interop check passed:")
    print(f"  spans      {[s['name'] for s in stored_ts['spans']]}")
    print(f"  tokens     {stored_ts['total_tokens']}")
    print(f"  cost       ${stored_ts['total_cost_usd']}")
    print("  the two SDKs align node-for-node; only latency differs")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))

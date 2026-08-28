#!/usr/bin/env python3
"""
Populate a fresh AgentLens install with realistic data.

An observability tool that opens to an empty screen is impossible to
evaluate — you can't tell whether the DAG view is good until there's a DAG
in it. This generates a week of plausible history: several agents, real
failure modes, a quality regression you can find in the Quality tab, an MCP
trace stitched across two processes, and tagged runs the CI gate can compare.

    python scripts/seed_demo.py                     # ~40 runs over 7 days
    python scripts/seed_demo.py --runs 200          # more history
    python scripts/seed_demo.py --live              # then stream one live
    python scripts/seed_demo.py --clean-first       # reset before seeding

Everything is synthetic. No API keys, no network calls to model providers.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "sdk"))

DEFAULT_ENDPOINT = "http://localhost:7430"

# --------------------------------------------------------------------------- #
# scenario definitions
#
# Each agent has a distinct shape so the UI shows variety: a deep RAG chain,
# a wide tool-calling support agent, and a short classifier. Durations and
# token counts are drawn from ranges that look like real traffic rather than
# round numbers.
# --------------------------------------------------------------------------- #

QUERIES = [
    "what changed in the refund policy last quarter",
    "summarize the incident report from tuesday",
    "which customers churned after the price change",
    "draft a reply about the delayed shipment",
    "compare our uptime to the SLA commitment",
    "find the root cause of the checkout errors",
    "who owns the billing service runbook",
    "what did the postmortem recommend",
]

TOOL_ERRORS = [
    ("ConnectionError", "search API returned 503 after 3s"),
    ("TimeoutError", "upstream timed out at 5000ms"),
    ("ValueError", "vector store returned malformed payload"),
    ("PermissionError", "index 'internal-docs' requires elevated scope"),
]

MODEL_ERRORS = [
    ("RateLimitError", "429: token rate limit exceeded, retry in 12s"),
    ("TimeoutError", "model timeout at 30000ms"),
    ("ContextLengthError", "prompt is 214k tokens, model limit is 200k"),
]


def hex_id(n: int = 8) -> str:
    return uuid.uuid4().hex[: n * 2]


def span(
    name: str,
    kind: str,
    started: float,
    duration_ms: float,
    *,
    parent: str | None = None,
    status: str = "success",
    inputs: str = "",
    outputs: str = "",
    error: str | None = None,
    retry_of: str | None = None,
    llm: dict | None = None,
    service: str | None = None,
    remote_parent_id: str | None = None,
    attributes: dict | None = None,
) -> dict:
    return {
        "span_id": hex_id(),
        "parent_id": parent,
        "name": name,
        "kind": kind,
        "status": status,
        "started_at": started,
        "ended_at": started + duration_ms / 1000,
        "duration_ms": round(duration_ms, 2),
        "inputs": inputs,
        "outputs": outputs,
        "error": error,
        "retry_of": retry_of,
        "remote_parent_id": remote_parent_id,
        "service": service,
        "llm": llm,
        "attributes": attributes or {},
    }


def llm_meta(
    model: str, provider: str, prompt_tokens: int, completion_tokens: int, preview: str = ""
) -> dict:
    from agentlens.cost import estimate_cost_usd

    return {
        "model": model,
        "provider": provider,
        "input_tokens": prompt_tokens,
        "output_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "cost_usd": round(estimate_cost_usd(model, prompt_tokens, completion_tokens), 6),
        "prompt_preview": preview or "…",
        "response_preview": "…",
        "temperature": 0.2,
    }


def finalize(run: dict) -> dict:
    """Roll span totals up to the run, the way the SDK does on export."""
    spans = run["spans"]
    ends = [s["ended_at"] for s in spans if s["ended_at"]]
    run["ended_at"] = max(ends) if ends else run["started_at"]
    run["duration_ms"] = round((run["ended_at"] - run["started_at"]) * 1000, 2)
    run["total_tokens"] = sum((s["llm"] or {}).get("total_tokens", 0) for s in spans)
    run["total_cost_usd"] = round(sum((s["llm"] or {}).get("cost_usd", 0.0) for s in spans), 6)
    return run


def new_run(name: str, started: float, tags: list[str]) -> dict:
    return {
        "run_id": uuid.uuid4().hex,
        "trace_id": uuid.uuid4().hex,
        "name": name,
        "tags": tags,
        "status": "success",
        "started_at": started,
        "ended_at": None,
        "duration_ms": None,
        "total_tokens": 0,
        "total_cost_usd": 0.0,
        "error": None,
        "metadata": {"seeded": True},
        "scores": [],
        "spans": [],
    }


def score(name: str, value: float, threshold: float, source: str = "ragas", comment: str = "") -> dict:
    return {
        "name": name,
        "value": round(value, 3),
        "source": source,
        "threshold": threshold,
        "passed": value >= threshold,
        "comment": comment,
        "span_id": None,
        "recorded_at": time.time(),
    }


# --------------------------------------------------------------------------- #
# agents
# --------------------------------------------------------------------------- #


def research_agent(started: float, tags: list[str], quality: float, rng: random.Random) -> dict:
    """A deep RAG chain: plan, search, fetch each result, synthesize."""
    run = new_run("research_agent", started, tags)
    root = span("research_agent", "agent", started, 0, inputs=json.dumps({"query": rng.choice(QUERIES)}))
    run["spans"].append(root)
    t = started + 0.05

    plan_ms = rng.uniform(700, 1400)
    run["spans"].append(
        span(
            "plan_steps",
            "llm",
            t,
            plan_ms,
            parent=root["span_id"],
            llm=llm_meta("gpt-4o-mini", "openai", rng.randint(260, 400), rng.randint(70, 140)),
            outputs="['search', 'fetch', 'synthesize']",
        )
    )
    t += plan_ms / 1000

    search_ms = rng.uniform(400, 1200)
    hits = rng.randint(4, 9)
    run["spans"].append(
        span(
            "web_search",
            "tool",
            t,
            search_ms,
            parent=root["span_id"],
            outputs=f"{hits} results",
        )
    )
    t += search_ms / 1000

    # fetching pages is where flakiness lives: some retry, some give up
    for i in range(min(hits, 3)):
        fetch_ms = rng.uniform(200, 900)
        if rng.random() < 0.22:
            etype, emsg = rng.choice(TOOL_ERRORS)
            failed = span(
                "fetch_page",
                "retrieval",
                t,
                fetch_ms,
                parent=root["span_id"],
                status="error",
                error=f"{etype}: {emsg}",
                inputs=f"url {i}",
            )
            run["spans"].append(failed)
            t += fetch_ms / 1000
            retry_ms = rng.uniform(200, 700)
            run["spans"].append(
                span(
                    "fetch_page",
                    "retrieval",
                    t,
                    retry_ms,
                    parent=root["span_id"],
                    retry_of=failed["span_id"],
                    outputs="4.2k chars",
                    inputs=f"url {i}",
                )
            )
            t += retry_ms / 1000
        else:
            run["spans"].append(
                span(
                    "fetch_page",
                    "retrieval",
                    t,
                    fetch_ms,
                    parent=root["span_id"],
                    outputs="4.2k chars",
                    inputs=f"url {i}",
                )
            )
            t += fetch_ms / 1000

    synth_ms = rng.uniform(1800, 4200)
    if rng.random() < 0.12:
        etype, emsg = rng.choice(MODEL_ERRORS)
        run["spans"].append(
            span(
                "synthesize",
                "llm",
                t,
                synth_ms,
                parent=root["span_id"],
                status="error",
                error=f"{etype}: {emsg}",
                llm=llm_meta("claude-sonnet-4", "anthropic", rng.randint(3000, 6000), 0),
            )
        )
        run["status"] = "error"
        run["error"] = f"{etype}: {emsg}"
        root["status"] = "error"
        root["error"] = f"{etype}: {emsg}"
    else:
        run["spans"].append(
            span(
                "synthesize",
                "llm",
                t,
                synth_ms,
                parent=root["span_id"],
                llm=llm_meta("claude-sonnet-4", "anthropic", rng.randint(1600, 2600), rng.randint(300, 600)),
                outputs="Report: 3 key findings…",
            )
        )
        run["scores"] = [
            score("faithfulness", min(quality + rng.uniform(-0.05, 0.05), 1.0), 0.85),
            score("answer_relevancy", min(quality + rng.uniform(-0.03, 0.08), 1.0), 0.80),
            score("context_precision", min(quality + rng.uniform(-0.10, 0.02), 1.0), 0.75),
        ]

    root["duration_ms"] = round((t + synth_ms / 1000 - started) * 1000, 2)
    root["ended_at"] = t + synth_ms / 1000
    return finalize(run)


def support_agent(started: float, tags: list[str], quality: float, rng: random.Random) -> dict:
    """Wide tool use: classify, then several lookups, then a drafted reply."""
    run = new_run("support_agent", started, tags)
    root = span("support_agent", "agent", started, 0, inputs='{"ticket": "where is my order?"}')
    run["spans"].append(root)
    t = started + 0.04

    classify_ms = rng.uniform(300, 700)
    run["spans"].append(
        span(
            "classify_intent",
            "llm",
            t,
            classify_ms,
            parent=root["span_id"],
            llm=llm_meta("gpt-4o-mini", "openai", rng.randint(150, 260), rng.randint(20, 50)),
            outputs="'order_status'",
        )
    )
    t += classify_ms / 1000

    for tool in ("lookup_order", "check_shipment", "fetch_customer"):
        tool_ms = rng.uniform(120, 600)
        run["spans"].append(
            span(
                tool,
                "tool",
                t,
                tool_ms,
                parent=root["span_id"],
                outputs="{…}",
                inputs='{"order_id": "ORD-48210033"}',
            )
        )
        t += tool_ms / 1000

    draft_ms = rng.uniform(900, 2200)
    run["spans"].append(
        span(
            "draft_reply",
            "llm",
            t,
            draft_ms,
            parent=root["span_id"],
            llm=llm_meta("gpt-4o", "openai", rng.randint(700, 1400), rng.randint(120, 300)),
            outputs="'Your order shipped on…'",
        )
    )
    t += draft_ms / 1000

    run["scores"] = [
        score("task_completion", min(quality + rng.uniform(-0.04, 0.06), 1.0), 0.80, source="llm_judge"),
        score("tool_correctness", min(quality + rng.uniform(-0.06, 0.04), 1.0), 0.75, source="llm_judge"),
    ]
    root["ended_at"] = t
    root["duration_ms"] = round((t - started) * 1000, 2)
    return finalize(run)


def triage_agent(started: float, tags: list[str], quality: float, rng: random.Random) -> dict:
    """A short classifier — fast, cheap, occasionally over budget on retries."""
    run = new_run("triage_agent", started, tags)
    root = span("triage_agent", "agent", started, 0, inputs='{"text": "…"}')
    run["spans"].append(root)
    t = started + 0.02

    cls_ms = rng.uniform(200, 500)
    run["spans"].append(
        span(
            "classify",
            "llm",
            t,
            cls_ms,
            parent=root["span_id"],
            llm=llm_meta("gpt-4o-mini", "openai", rng.randint(90, 200), rng.randint(10, 30)),
            outputs="'billing'",
        )
    )
    t += cls_ms / 1000

    if rng.random() < 0.10:
        run["status"] = "paused"
        run["error"] = "token budget exceeded: 5210 > 5000"
        root["status"] = "paused"

    run["scores"] = [score("task_completion", min(quality + rng.uniform(-0.02, 0.05), 1.0), 0.80)]
    root["ended_at"] = t
    root["duration_ms"] = round((t - started) * 1000, 2)
    return finalize(run)


def mcp_pair(started: float, rng: random.Random) -> tuple[dict, dict]:
    """
    An agent calling an MCP tool server, as two runs sharing a trace.

    Seeded because cross-process stitching is invisible until you see it:
    the agent run alone just shows 'create_issue took 900ms'.
    """
    trace = uuid.uuid4().hex

    agent = new_run("issue_agent", started, ["prod", "mcp"])
    agent["trace_id"] = trace
    root = span("issue_agent", "agent", started, 0, inputs='{"title": "DAG layout overlaps"}')
    agent["spans"].append(root)
    t = started + 0.03

    list_ms = rng.uniform(40, 120)
    agent["spans"].append(
        span(
            "list_tools",
            "mcp",
            t,
            list_ms,
            parent=root["span_id"],
            outputs="['create_issue', 'search_issues']",
            attributes={
                "mcp.server.name": "github",
                "mcp.method.name": "list_tools",
                "mcp.transport": "stdio",
            },
        )
    )
    t += list_ms / 1000

    call_ms = rng.uniform(700, 1400)
    client_span = span(
        "create_issue",
        "mcp",
        t,
        call_ms,
        parent=root["span_id"],
        inputs='{"title": "DAG layout overlaps"}',
        outputs="issue #4127 created",
        attributes={"mcp.server.name": "github", "mcp.method.name": "call_tool", "mcp.transport": "stdio"},
    )
    agent["spans"].append(client_span)
    t += call_ms / 1000
    root["ended_at"] = t
    root["duration_ms"] = round((t - started) * 1000, 2)

    # the server's own run: same trace, pointing back at the client span
    server_started = client_span["started_at"] + 0.02
    server = new_run("github.create_issue", server_started, ["mcp-server", "github"])
    server["trace_id"] = trace
    server_root = span(
        "create_issue",
        "mcp",
        server_started,
        call_ms - 60,
        service="github",
        remote_parent_id=client_span["span_id"],
        attributes={"mcp.tool.name": "create_issue", "mcp.server.name": "github", "mcp.side": "server"},
        outputs="issue #4127 created",
    )
    server["spans"].append(server_root)
    api_ms = call_ms - 200
    server["spans"].append(
        span(
            "github_api_post",
            "tool",
            server_started + 0.03,
            api_ms,
            parent=server_root["span_id"],
            service="github",
            outputs="201 Created",
            attributes={"http.status_code": 201},
        )
    )
    return finalize(agent), finalize(server)


# --------------------------------------------------------------------------- #
# posting
# --------------------------------------------------------------------------- #


def post(endpoint: str, path: str, payload: dict, api_key: str | None = None) -> tuple[int, str]:
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    req = urllib.request.Request(
        endpoint.rstrip("/") + path, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as res:
            return res.status, res.read().decode()[:200]
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()[:200]


def check_server(endpoint: str) -> None:
    try:
        with urllib.request.urlopen(endpoint.rstrip("/") + "/api/health", timeout=5):
            return
    except Exception as e:
        raise SystemExit(
            f"Can't reach AgentLens at {endpoint} ({e}).\n"
            "Start it with `docker compose up`, or pass --endpoint."
        ) from None


DEMO_WEBHOOK = "https://example.invalid/hooks/agentlens-demo"


def seed_alert_rules(endpoint: str, api_key: str | None) -> int:
    """
    Rules the seeded runs will actually trip, so the Alerts tab isn't empty.

    The webhook points at an unreachable host on purpose: the events show up
    with `delivered: false`, which exercises the delivery-failure state and
    avoids posting to somewhere real.
    """
    rules = [
        {
            "name": "Any failed run",
            "field": "status",
            "op": "eq",
            "value": "error",
            "webhook_url": DEMO_WEBHOOK,
        },
        {
            "name": "Runs over $0.05",
            "field": "total_cost_usd",
            "op": "gt",
            "value": "0.05",
            "webhook_url": DEMO_WEBHOOK,
        },
        {
            "name": "Faithfulness below 0.85",
            "field": "score:faithfulness",
            "op": "lt",
            "value": "0.85",
            "webhook_url": DEMO_WEBHOOK,
        },
        {
            "name": "Slow research runs",
            "field": "duration_ms",
            "op": "gt",
            "value": "8000",
            "run_name": "research_agent",
            "webhook_url": DEMO_WEBHOOK,
        },
    ]
    created = 0
    for rule in rules:
        status, _ = post(endpoint, "/api/alerts/rules", rule, api_key)
        created += status == 201
    return created


def stream_live_run(endpoint: str, api_key: str | None, rng: random.Random) -> None:
    """Push one run span by span so the live DAG has something to draw."""
    from agentlens import AgentLens, SpanKind, StreamExporter

    lens = AgentLens(exporter=StreamExporter(endpoint), record_outputs=True)

    @lens.llm_call("plan_steps", model="gpt-4o-mini", provider="openai")
    def plan(query):
        time.sleep(1.1)
        return {"model": "gpt-4o-mini", "usage": {"prompt_tokens": 320, "completion_tokens": 95}}

    @lens.tool("web_search")
    def search(query):
        time.sleep(1.6)
        return [f"https://example.com/{i}" for i in range(5)]

    @lens.span("fetch_page", kind=SpanKind.RETRIEVAL, retries=2)
    def fetch(url):
        time.sleep(0.7)
        if rng.random() < 0.35:
            raise TimeoutError(f"timed out fetching {url}")
        return "4.2k chars"

    @lens.llm_call("synthesize", model="claude-sonnet-4", provider="anthropic")
    def synthesize(pages):
        time.sleep(2.4)
        return {"model": "claude-sonnet-4", "usage": {"input_tokens": 1980, "output_tokens": 445}}

    @lens.trace("live_research_agent", tags=["demo", "live"])
    def agent(query):
        plan(query)
        pages = []
        for url in search(query)[:3]:
            try:
                pages.append(fetch(url))
            except TimeoutError:
                pass
        synthesize(pages)
        return f"report from {len(pages)} pages"

    print("  streaming a live run — watch the DAG build itself (~8s)")
    agent(rng.choice(QUERIES))
    lens.exporter.flush()


# --------------------------------------------------------------------------- #


def build_runs(count: int, days: int, rng: random.Random) -> list[dict]:
    """
    Generate history with a deliberate quality regression partway through,
    so the Quality tab shows a real downward trend and the CI gate demo has
    something to catch.
    """
    now = time.time()
    window = days * 86400
    runs: list[dict] = []

    for i in range(count):
        age = window * (1 - i / max(count - 1, 1))
        started = now - age + rng.uniform(-1800, 1800)
        progress = i / max(count - 1, 1)

        # quality holds, then drops around the two-thirds mark
        quality = 0.93 if progress < 0.66 else 0.93 - (progress - 0.66) * 0.75
        quality = max(quality, 0.58)

        # recent runs carry the branch tags the eval gate compares
        tags = ["prod"]
        if progress > 0.85:
            tags.append("pr-118")
        elif progress > 0.5:
            tags.append("main")

        pick = rng.random()
        if pick < 0.45:
            runs.append(research_agent(started, tags, quality, rng))
        elif pick < 0.8:
            runs.append(support_agent(started, tags, quality, rng))
        else:
            runs.append(triage_agent(started, tags, quality, rng))

    # a few MCP pairs scattered through the window
    for _ in range(max(count // 12, 2)):
        started = now - rng.uniform(0, window)
        agent_run, server_run = mcp_pair(started, rng)
        runs.extend([agent_run, server_run])

    return runs


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed AgentLens with demo data.")
    parser.add_argument("--endpoint", default=DEFAULT_ENDPOINT)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--runs", type=int, default=40, help="How many runs to generate (default: 40)")
    parser.add_argument("--days", type=int, default=7, help="Spread runs over this many days")
    parser.add_argument("--seed", type=int, default=7, help="RNG seed, so runs are reproducible")
    parser.add_argument("--live", action="store_true", help="Also stream one run in real time")
    parser.add_argument("--no-alerts", action="store_true", help="Skip creating alert rules")
    parser.add_argument("--clean-first", action="store_true", help="Warn about existing runs first")
    args = parser.parse_args()

    check_server(args.endpoint)
    rng = random.Random(args.seed)

    if args.clean_first:
        print("Note: AgentLens has no bulk-delete endpoint by design — drop the")
        print("      Postgres volume (`docker compose down -v`) for a clean slate.\n")

    # Rules first: alerts are evaluated at ingest, so a rule created after
    # the runs land can never fire and the Alerts tab stays empty.
    if not args.no_alerts:
        created = seed_alert_rules(args.endpoint, args.api_key)
        print(f"Created {created} alert rules")

    runs = build_runs(args.runs, args.days, rng)
    print(f"Seeding {len(runs)} runs over {args.days} days → {args.endpoint}")

    ok = failed = 0
    for run in sorted(runs, key=lambda r: r["started_at"]):
        status, body = post(args.endpoint, "/api/ingest/run", run, args.api_key)
        if status == 201:
            ok += 1
        else:
            failed += 1
            if failed == 1:
                print(f"  first failure: {status} {body}")

    print(f"  {ok} runs accepted" + (f", {failed} rejected" if failed else ""))

    if not args.no_alerts:
        # alert evaluation runs as a background task after ingest returns
        time.sleep(1.0)

    if args.live:
        stream_live_run(args.endpoint, args.api_key, rng)

    statuses = {}
    for r in runs:
        statuses[r["status"]] = statuses.get(r["status"], 0) + 1

    print("\nWhat to look at:")
    print(f"  Runs      {statuses} — click a failed one to see where it broke")
    print("  Timeline  toggle Graph → Timeline on a research_agent run")
    print("  Diff      pin (★) an early run and a recent one, then open Diff")
    print("  Quality   faithfulness declines in the last third of the window")
    print("  Alerts    rules the seeded failures tripped (delivery fails by design)")
    print("  MCP       an issue_agent run nests its github server's spans")
    print("\n  Eval gate demo:")
    print("    python -m agentlens.ci gate --candidate-tag pr-118 --baseline-tag main \\")
    print("      --threshold faithfulness=0.85 --max-regression 0.03")
    print("\n  Open http://localhost:5173")
    return 0


if __name__ == "__main__":
    sys.exit(main())

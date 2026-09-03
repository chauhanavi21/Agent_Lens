# Quickstart

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

# SDK reference

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

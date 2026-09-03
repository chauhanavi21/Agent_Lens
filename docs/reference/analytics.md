# Cross-run analytics

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

### Cost totals say what they don't cover

An unrecognized model used to estimate at $0.00 — which reads as "this step
was free" when it means "nobody priced this," and a total summing those
zeros is confidently wrong in a way nobody checks. So every cost carries its
provenance: `reported` (the provider told us), `table` (matched a price),
`free` (priced at zero on purpose), or `unpriced`.

```json
{"total_cost_usd": 0.0075, "unpriced_models": ["mystery-model-v9"],
 "unpriced_tokens": 10000, "cost_coverage": 0.13,
 "warning": "10,000 tokens across 1 unpriced model(s) are not included…"}
```

Prices go stale, so the table isn't the answer — configuring it is:

```bash
AGENTLENS_COST_TABLE='{"my-model": [1.50, 6.00]}'   # or a path to a JSON file
```

A provider-reported cost always wins over the table, since gateways like
OpenRouter and LiteLLM return the actual charge and a local table is a guess
about someone else's billing.

The index is **derived** from the JSONB on each run, not a second source of
truth. It's written on ingest and rebuildable at any time
(`POST /api/analytics/reindex`), so the worst case of it drifting is wasted
work rather than lost data.

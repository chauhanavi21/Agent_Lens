# AgentLens architecture

This document explains **why** AgentLens is built the way it is. The README
covers what it does; this covers the decisions, what they cost, and what I'd
reconsider.

---

## 1. The problem

LLM observability tools trace the *model client*: prompt in, completion out,
tokens counted. That was the right abstraction when an "AI feature" was one
API call.

An agent is not one API call. It's a graph of decisions — a planning step
picks tools, tools return data that changes the next decision, failures
trigger retries, sub-agents spawn. When one fails in production, the useful
questions are structural:

- Which step diverged from the run that worked yesterday?
- Was the tool slow, or did the model misread a result it got quickly?
- How much did this run cost, and which node spent it?
- Did quality drop even though nothing errored?

None of those are answerable from a list of LLM calls. AgentLens records the
**execution graph** — every span, its parent, its retries, its cost — and
builds the features that only become possible once you have it: run diffing,
cross-process stitching, deterministic replay, quality gates.

---

## 2. System shape

```
┌──────────────────────────────────────────────────────────────┐
│  Agent process                                                │
│    Python SDK  @lens.trace / @lens.span / @lens.tool          │
│    TypeScript  lens.trace(name, fn) / lens.tool(name, fn)     │
│    ─ context propagation (contextvars / AsyncLocalStorage)    │
│    ─ redaction, then export, off the hot path                 │
└───────────────┬──────────────────────────────────────────────┘
                │  run JSON  ·  span events (SSE)  ·  OTLP
                ▼
┌──────────────────────────────────────────────────────────────┐
│  Server (FastAPI)                                             │
│    ingest  ·  read-time stitching  ·  diff  ·  evals  ·  gate │
│    live store (memory) + SSE broker                           │
└───────────────┬──────────────────────────────────────────────┘
                │
        ┌───────┴────────┐              ┌──────────────────────┐
        │  PostgreSQL    │              │  OTel backends       │
        │  runs (JSONB)  │              │  Tempo / Honeycomb   │
        └───────┬────────┘              └──────────────────────┘
                │
┌───────────────┴──────────────────────────────────────────────┐
│  UI (React + D3)                                              │
│    live DAG · timeline · span drawer · diff · quality · alerts│
└──────────────────────────────────────────────────────────────┘
```

Three processes, deliberately. The SDK must be safe to embed in anything;
the server owns storage and cross-run analysis; the UI is a plain client of
the API, so everything it does is scriptable.

---

## 3. Data model

### 3.1 Spans as JSONB on the run row, not a normalized table

Every span could be a row in a `spans` table with a foreign key. Instead the
whole span list lives as a JSONB column on the run.

**Why.** Agent runs are read whole. Every view — DAG, timeline, span drawer,
diff — needs all spans for one run and never needs "all spans named
`web_search` across all runs" as its primary access pattern. A normalized
table means a join and a re-assembly step on every read, to support a query
shape the product doesn't have.

**What it costs.** Cross-run span analytics ("p95 latency of `web_search`
across 10k runs") requires a JSONB scan rather than an indexed column. A GIN
index makes containment queries workable, but this is genuinely worse than a
normalized schema would be.

**When I'd change it.** If span-level analytics became a headline feature, I
would keep JSONB as the source of truth and add a derived `span_stats` table
written on ingest. Denormalizing *for* the query is better than normalizing
*against* the dominant read.

### 3.2 One wire format, two SDKs

The TypeScript SDK emits byte-identical payloads to the Python SDK — same
field names, same `trace_id` width, same seconds-with-fraction timestamps.

This is enforced by test, not by convention: the interop test runs the same
agent in both languages, posts both runs, and asks the server's own diff
engine to compare them. It asserts zero spans added or removed and identical
token counts; only `duration_ms` differs. A Python orchestrator calling a
Node tool service produces **one** DAG.

### 3.3 Previews are truncated, and that has consequences

`inputs` and `outputs` are truncated string previews. Good for a UI, useless
for replay — you can't feed a truncated repr back into an agent. So replay
needs `record_outputs=True`, which stores a full JSON copy in span
attributes, and cassettes built without it mark themselves `truncated`.

The alternative — always storing full payloads — makes every trace a
liability and a storage cost. Opt-in was the right default; flagging the
limitation in the data was the part that made it honest.

---

## 4. SDK

### 4.1 Zero runtime dependencies

Both SDKs. The Python SDK uses `urllib`; the TypeScript SDK uses `fetch` and
`node:async_hooks`. Even OTLP export is hand-rolled JSON over HTTP rather
than pulling in `opentelemetry-sdk`.

**Why.** An observability SDK is embedded in someone else's dependency tree.
Every package it drags in is a version conflict waiting to happen in a
codebase that already has strong opinions about `httpx` or `pydantic`. The
cost is a few hundred lines of protocol code, paid once.

### 4.1a Supporting an old Python is a claim, not a hope

The SDK advertises 3.9, which means `traceback.format_exception(exc)` — the
single-argument form added in 3.10 — is off limits. `agentlens/compat.py`
handles both signatures.

That shim was added when the CI matrix was written, and it still shipped
broken: the framework integrations, written in a later session, called the
stdlib directly and the 3.9 matrix leg failed on push. A linter can't see
this, because the arity change is a stdlib signature difference rather than
syntax — `vermin` reports the package as 3.8-compatible either way.

So the enforcement is a test that scans the package source for direct calls
and names the offending files. A shim nothing enforces is a shim someone
forgets.

### 4.2 Tracing must never break the traced agent

This is the invariant everything else bends around:

- Exporters swallow their own failures.
- Decorators pass through untouched when no run is active.
- `preview()` never throws — circular references, throwing getters, and
  BigInts all degrade to a placeholder.
- A redactor that raises **drops the field** rather than emitting raw data
  or propagating.
- Streaming events are best-effort with a bounded queue that drops
  oldest-first.

There are tests for each: a `Hostile` exporter that raises on every call, a
`BrokenRedactor`, a `StreamExporter` pointed at a closed port. In all of
them the agent returns its correct result.

### 4.3 Decorators in Python, wrappers in TypeScript

Not an inconsistency. Python decorators apply to any function. TypeScript
decorators only apply to class methods, and most agent code is plain
functions, closures, and callbacks handed to a framework. Wrappers work
everywhere and preserve full type inference — `lens.trace(name, fn)` returns
a function with `fn`'s exact signature.

### 4.4 Context propagation

`contextvars` in Python, `AsyncLocalStorage` in Node. Both survive `await`,
`Promise.all`/`asyncio.gather`, and framework callbacks without threading a
context object through user code.

The failure mode this guards against is subtle: with naive
"current span is a module global", three concurrent branches collapse onto
one parent and the DAG silently lies. The TypeScript suite has a test that
fires three concurrent branches with staggered timing and asserts each leaf
attaches to its own parent.

### 4.5 Retry lineage is a first-class field

A failed attempt isn't discarded — it stays in the DAG with `retry_of`
pointing at the previous attempt. "This succeeded after three tries" and
"this succeeded" are different facts, and only the first explains a latency
spike.

### 4.6 Budget guards raise by default

`max_cost_usd` throws `BudgetExceeded` rather than logging. A runaway agent
loop is a billing incident; the default should stop it. `on_budget="pause"`
and `"warn"` exist for people who'd rather degrade than fail.

---

## 5. Cross-process tracing

### 5.1 MCP context in `params._meta`

MCP passes `_meta` through untouched, so W3C trace context rides there
(SEP-414). The agent process injects `traceparent`; the tool server extracts
it. Context crosses a stdio pipe the same way it crosses HTTP.

**Bug worth remembering:** I initially injected context *before* opening the
client span, so the server's parent pointed at the agent root instead of the
MCP call. The DAG looked plausible and was wrong. Injection has to happen
inside the span.

### 5.2 Stitching at read time, not write time

An MCP server exports its own run. It doesn't know — and shouldn't need to
know — which agent called it. Both sides share a trace id, and the server's
root span carries the caller's span id as `remote_parent_id`. The AgentLens
server grafts them together **when the run is read**.

**Why not rewrite the caller's row on ingest?** Because then arrival order
matters. Read-time stitching means either side can land first, a late server
run still merges, and each service keeps owning its own data. The cost is
doing the graft on every read; it's a dictionary walk over one run's spans,
which is nothing next to the correctness it buys.

Unstitched remote runs stay readable on their own rather than being dropped,
so a tool server is still observable when its caller isn't instrumented.

### 5.3 `service` names the recording process

Not the target. A client-side MCP span was recorded by the *agent*, so the
server name belongs in `mcp.server.name`. I got this wrong first — both
sides claimed `service: "github"` and became indistinguishable once
stitched.

---

## 6. OpenTelemetry

### 6.1 Dual-emit, because the spec is still moving

Every `gen_ai.*` attribute carries **Development** stability in the OTel
registry — names can change without a major version bump. So AgentLens emits
GenAI attributes *and* namespaced `agentlens.*` ones.

The native namespace also carries what the spec has no home for: retry
lineage, per-call cost, eval scores. A full SDK → OTLP → receiver round trip
is lossless, verified by test.

### 6.2 The bridge runs both directions

Export lets agent traces sit beside service traces in Tempo or Honeycomb.
Ingest (`/api/ingest/otlp`) lets an agent instrumented with *any* OTel SDK
get the DAG and diffing without swapping libraries. That second direction is
what keeps AgentLens from being a silo — tested against a foreign trace
built from raw GenAI conventions with no AgentLens attributes at all.

### 6.3 Prompt content is off by default

Prompts routinely carry user data, and a trace backend is a bad place to
discover you've been storing SSNs. `capture_content=True` opts in.

---

## 7. Quality: scores, judge, gate

### 7.1 One score shape, four sources

Inline scores, Ragas output, LLM-judge results, and OTLP-imported scores all
land in the same structure. Everything downstream — trends, diffs, alerts,
the CI gate — treats them identically. Adding a scoring method requires no
downstream change.

### 7.2 The judge reads the trace, not the answer

Given only a final string you can check plausibility. Given the DAG you can
see the retry loop, the tool that returned nothing, the step that was
skipped. The judge prompt renders the execution tree.

The provider is *injected* rather than imported, so scoring logic is
testable without a network call and swappable between vendors. The response
parser tolerates fenced JSON, surrounding prose, and bare numbers — models
do all three often enough that strict parsing would fail runs for cosmetic
reasons.

### 7.3 The gate checks relative regression, not just floors

Two questions:

1. **Absolute** — is any metric below its floor? Catches a branch that was
   always bad.
2. **Relative** — did any metric drop more than `max_regression` versus a
   baseline?

The second is the one that earns its keep. `0.92 → 0.86` clears a `0.85`
floor and is exactly the drift nobody notices until it's three releases old.
A metric the baseline measured but the branch stopped producing also fails —
silently dropping coverage is how a gate becomes theater.

**Exit codes:** `0` pass, `1` failed checks, `2` usage or connection error.
An unreachable server exits 2, never 0. A gate that turns green when the
backend is down is worse than no gate.

---

## 8. Replay

### 8.1 Replay the world, run the code

Tool, LLM, retrieval, and MCP spans are served from the recording. Agent,
chain, and custom spans **execute for real**. Replaying the reasoning too
would just play back a transcript; the point is today's code meeting
yesterday's inputs.

### 8.2 Changed inputs are an error

If your fix alters what a step sends, replay raises `InputMismatch` instead
of serving a recording. Nobody knows what that model would have returned for
arguments it never saw — serving the old output is a lie that produces a
green test.

This caught a bug in my own example: a hand-written fixture whose input
string didn't match what the tracer actually records. That's precisely the
class of error the check exists for.

### 8.3 Strict by default

An unrecorded call raises rather than quietly reaching the network.
Otherwise a deterministic regression test turns flaky the moment someone
adds a call — which is the failure mode replay exists to eliminate.

---

## 9. Privacy

### 9.1 Redact in the SDK, before export

Scrubbing at ingest would still mean raw values crossed the network and sat
in an access log on the way. The only place a secret is reliably contained
is the process that produced it. Server-side redaction exists
(`AGENTLENS_REDACT_ON_INGEST`) but only for OTLP traffic from SDKs you don't
control.

### 9.2 `hash` is what keeps redacted traces useful

A deterministic HMAC means the same customer produces the same token every
run. You can still group their runs and ask "did this user hit the bug
twice?" without the value being recoverable. Mask keeps a recognizable
shape; drop leaves nothing.

### 9.3 False positives are their own failure

A trace full of `[redacted]` is useless, so detection is validated rather
than just matched:

- Credit cards must pass **Luhn** (`luhn_valid`) — `4111 1111 1111 1111` is redacted, order
  number `12345678901234567` is not.
- IPv4 checks **context** — `1.2.3.4` after the word "version" is a version
  string.
- Field-name rules work on **preview strings** too, not just structured
  values, since `{"api_key": "x7f2q"}` becomes a string the moment it's
  recorded.

A test asserts dates, room numbers, semvers, error codes, and code snippets
pass through untouched.

---

## 10. Live streaming

### 10.1 Events are best-effort; the final run is the truth

The exporter's queue is bounded and drops oldest-first. A dead server costs
you the live view, never the agent's memory or its data. The complete run
still posts at the end.

**Why stream at all?** A run that hangs, gets OOM-killed, or is still
executing never reaches its final export — and those are the runs you most
want to look at. Batch-only tracing loses them entirely.

### 10.2 In-memory live state, disposable by design

Partial runs live in memory; persistence happens on final export. A
multi-process deployment swaps the broker for Redis pub/sub — the interface
is `publish`/`subscribe`/`unsubscribe`, deliberately small enough to be a
drop-in.

Spans arriving before `run_start` synthesize a shell run rather than being
dropped, so a browser connecting mid-run still sees a coherent DAG.

---

## 11. Framework integrations

All five (LangChain, LangGraph, CrewAI, OpenAI Agents SDK, Pydantic AI) read
framework objects by **duck typing** rather than importing their types.
Importing `agentlens` never pulls in a framework.

These APIs are still moving. A renamed attribute should cost one field, not
the whole trace. Each adapter is tested against a fake mirroring the
documented interface — so when a framework changes, the failing test names
the assumption that broke rather than just going red.

Where a framework already emits OTel (Pydantic AI with Logfire), the README
points at `/api/ingest/otlp` instead of the wrapper. Recommending against
your own code where a simpler path exists is usually right.

---

## 11a. Performance

The SDK is on the hot path of the thing it observes, so the cost is
measured rather than asserted: `scripts/benchmark.py` reports ~13µs per
span, ~560 bytes per span retained until export, and ~12,600 traced runs/sec
on one core. Export is on a background thread and excluded.

Two things that decision-making taught me:

**Framing matters as much as measurement.** Reporting overhead against a
1.2µs no-op produces numbers like "+6183%", which is true and useless. The
honest denominator is the work an agent does between spans — against one
800ms model call, a six-span traced run costs 0.01%.

**Two optimizations, one kept.** Merging the redaction detectors into a
single regex alternation produced no measurable gain and was reverted;
keeping complexity that buys nothing is worse than not trying. A
trigger-character pre-filter took digit-free prose from 56µs to 4µs and was
kept — but only because every built-in detector's matches provably contain
a trigger hint. That soundness condition is tested per secret type, and
custom patterns disable the fast path rather than having their trigger
characters guessed at. An optimization that turns into a data leak is the
worst possible trade.

`server/tests/test_performance.py` holds loose ceilings — several times the
measured values, since CI runners are noisy — to catch order-of-magnitude
regressions, plus a weak-reference check that the tracer doesn't retain
finished runs.

## 12. Testing strategy

51 Python tests, 16 TypeScript tests, plus end-to-end suites per subsystem.
Three things they deliberately cover:

1. **Adversarial infrastructure.** Hostile exporters, broken redactors, dead
   ports, malformed OTLP. The assertion is always that the *agent* still
   works.
2. **Cross-language and cross-process agreement.** The interop test and the
   MCP stitching test both verify that two independent producers assemble
   into one correct graph.
3. **Negative cases.** Redaction leaving ordinary text alone. The gate
   failing on a missing metric. Replay refusing changed inputs. Most of the
   real bugs surfaced here rather than in happy-path tests.

Two harness lessons: httpx's ASGI transport buffers streamed responses, so
SSE has to be tested against a real uvicorn server; and `node --test` needs
a file glob rather than a directory.

---

## 13. Known limitations

Being honest about these matters more than the feature list:

- **Cross-run span analytics are slow.** JSONB scan, no derived stats table
  yet (§3.1).
- **The live broker is single-process.** Redis swap is designed for but not
  implemented.
- **Replay matches calls by order and name.** An agent whose call sequence
  varies run to run under identical inputs will produce misleading matches —
  though the input check turns most of those into explicit errors.
- **Cost estimation is a static price table.** It drifts, and it silently
  returns 0 for unknown models rather than flagging them.
- **No auth beyond a shared bearer token.** Fine for self-hosting behind a
  VPN, not fine for multi-tenant.
- **Framework adapters are tested against fakes, not the real libraries.**
  They pin my understanding of each interface, which is not the same as
  pinning the interface.
- **The UI has no pagination.** It will struggle past a few hundred runs.

---

## 14. What I'd do differently

- **Add the derived stats table earlier.** JSONB was right for reads, but I
  deferred the analytics path long enough that it's now the clearest gap.
- **Design cassettes before shipping `outputs` as previews.** Replay ended
  up needing a parallel storage path (`record_outputs`) that a slightly
  different initial data model would have avoided.
- **Enforce compatibility shims at the point they're added.** The 3.9 shim
  and the test that guards it should have landed in the same commit; instead
  the gap was found by CI two sessions later.
- **Write this document sooner.** Several decisions here — read-time
  stitching, SDK-side redaction, relative regression — were only articulated
  properly when I wrote them down, and one (the `service` field naming the
  recording process) was outright wrong until explaining it exposed the
  problem.

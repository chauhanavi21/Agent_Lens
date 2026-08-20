# @agentlens/sdk

TypeScript SDK for [AgentLens](https://github.com/chauhanavi21/agentlens) —
open source observability for AI agents.

Zero runtime dependencies. Wire-compatible with the Python SDK, so a
polyglot system produces **one** DAG rather than two disconnected views.

```bash
npm install @agentlens/sdk
```

## Usage

```ts
import { AgentLens, score } from '@agentlens/sdk';

const lens = new AgentLens({ endpoint: 'http://localhost:7430' });

const webSearch = lens.tool('web_search', async (q: string) => search(q), { retries: 2 });

const summarize = lens.llmCall('summarize', async (docs: string[]) => openai.chat(docs), {
  provider: 'openai',
});

const agent = lens.trace(
  'research_agent',
  async (query: string) => {
    const result = await summarize(await webSearch(query));
    score('grounding', 0.91, { threshold: 0.85 });
    return result;
  },
  { tags: ['prod'], maxCostUsd: 0.1 },
);
```

Every wrapped function becomes a node in the execution graph. Types flow
through: `agent` keeps the exact signature of the function you passed in.

### Why wrappers instead of decorators

TypeScript decorators only apply to class methods, and most agent code is
plain functions, closures, and callbacks passed to a framework. Wrapping
works everywhere and keeps full type inference.

### Async context

Nesting is tracked with `AsyncLocalStorage`, so it survives `await`,
`Promise.all`, and callbacks with no context object threaded through your
code. Three concurrent branches produce three correctly-parented subtrees,
not one flat list.

## API

| Call | Purpose |
| --- | --- |
| `lens.trace(name, fn, opts)` | Agent entrypoint — owns a run |
| `lens.span(name, fn, { kind, retries })` | A step in the graph |
| `lens.tool(name, fn, { retries })` | Shorthand for a tool-kind span |
| `lens.llmCall(name, fn, { model, provider })` | Auto token + cost capture |
| `score(name, value, { threshold, source })` | Attach an eval score |

Exporters: `HttpExporter` (with `{ stream: true }` for live SSE),
`FileExporter`, `ConsoleExporter`, `MemoryExporter` (tests), and
`MultiExporter` to fan out.

Budget guards (`maxTotalTokens`, `maxCostUsd`) throw `BudgetExceededError`
by default; pass `onBudget: 'pause' | 'warn'` to change that.

## Development

```bash
npm install
npm test        # builds, then runs node:test
```

Apache 2.0.

# TypeScript SDK

Same model, same wire format, so a Python orchestrator calling a Node tool
service produces one DAG instead of two disconnected views.

```bash
npm install @agentlens/sdk
```

```ts
import { AgentLens, score } from '@agentlens/sdk';

const lens = new AgentLens({ endpoint: 'http://localhost:7430' });

const webSearch = lens.tool('web_search', async (q: string) => search(q), { retries: 2 });
const summarize = lens.llmCall('summarize', async (docs: string[]) => openai.chat(docs));

const agent = lens.trace('research_agent', async (query: string) => {
  const result = await summarize(await webSearch(query));
  score('grounding', 0.91, { threshold: 0.85 });
  return result;
}, { tags: ['prod'], maxCostUsd: 0.1 });
```

Zero runtime dependencies, full type inference through the wrappers, and
async nesting tracked with `AsyncLocalStorage` so it survives `await`,
`Promise.all`, and framework callbacks. Wrappers rather than decorators
because TypeScript decorators only apply to class methods, while most agent
code is plain functions.

Parity is enforced by test: a TS run and a Python run of the same agent are
posted to the server and diffed — they align node-for-node, with only
wall-clock latency differing. See `sdk-ts/`.

/**
 * A traced agent in TypeScript.
 *
 *   npm install @agentlens/sdk
 *   npx tsx examples/agent.ts
 */

import { AgentLens, HttpExporter, score } from '@agentlens/sdk';

const exporter = new HttpExporter('http://localhost:7430', { stream: true });
const lens = new AgentLens({ exporter });

interface ChatResponse {
  model: string;
  usage: { prompt_tokens: number; completion_tokens: number };
  text: string;
}

const webSearch = lens.tool(
  'web_search',
  async (query: string): Promise<string[]> => {
    await new Promise((r) => setTimeout(r, 300));
    if (Math.random() < 0.3) throw new Error('search API returned 503');
    return [`https://example.com/${encodeURIComponent(query)}`];
  },
  { retries: 2 }, // failed attempts stay in the DAG, linked as retries
);

const synthesize = lens.llmCall(
  'synthesize',
  async (docs: string[]): Promise<ChatResponse> => {
    await new Promise((r) => setTimeout(r, 500));
    return {
      model: 'gpt-4o',
      usage: { prompt_tokens: 1980, completion_tokens: 445 },
      text: `report from ${docs.length} source(s)`,
    };
  },
  { provider: 'openai' },
);

const researchAgent = lens.trace(
  'research_agent',
  async (query: string): Promise<string> => {
    const docs = await webSearch(query);
    const response = await synthesize(docs);
    score('grounding', 0.91, { source: 'custom', threshold: 0.85 });
    return response.text;
  },
  { tags: ['prod', 'ts'], maxCostUsd: 0.5 },
);

const result = await researchAgent('agent observability standards');
console.log(result);
await exporter.flush();
console.log('Open http://localhost:5173 to see the DAG.');

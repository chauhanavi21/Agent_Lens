// Two sample runs of the same agent: one clean, one with a retry + failure.
// Lets the UI demo the DAG, span drawer, and run diffing with zero setup.
const t0 = Date.now() / 1000 - 3600

function span(o) {
  return { parent_id: null, status: 'success', inputs: '', outputs: '', error: null, retry_of: null, llm: null, attributes: {}, ...o, duration_ms: +((o.ended_at - o.started_at) * 1000).toFixed(1) }
}

export const demoRuns = [
  {
    run_id: 'demo-run-a', name: 'research_agent', tags: ['prod', 'rag'], status: 'success',
    started_at: t0, ended_at: t0 + 4.2, duration_ms: 4200, total_tokens: 2840, total_cost_usd: 0.0161, error: null, metadata: {},
    scores: [
      { name: 'faithfulness', value: 0.91, source: 'ragas', threshold: 0.85, passed: true, comment: '', span_id: null },
      { name: 'answer_relevancy', value: 0.88, source: 'ragas', threshold: 0.8, passed: true, comment: '', span_id: null },
    ],
    spans: [
      span({ span_id: 'a1', name: 'research_agent', kind: 'agent', started_at: t0, ended_at: t0 + 4.2, inputs: "{'query': 'agent observability landscape 2026'}", outputs: "'Report: 3 key findings…'" }),
      span({ span_id: 'a2', parent_id: 'a1', name: 'plan_steps', kind: 'llm', started_at: t0 + 0.05, ended_at: t0 + 0.9, inputs: "'Break the query into research steps'", outputs: "['search', 'retrieve', 'synthesize']", llm: { model: 'gpt-4o-mini', provider: 'openai', input_tokens: 320, output_tokens: 95, total_tokens: 415, cost_usd: 0.000105, prompt_preview: 'Break the query into research steps…', response_preview: '1. search 2. retrieve 3. synthesize' } }),
      span({ span_id: 'a3', parent_id: 'a1', name: 'web_search', kind: 'tool', started_at: t0 + 0.95, ended_at: t0 + 1.7, inputs: "'agent observability 2026'", outputs: '8 results' }),
      span({ span_id: 'a4', parent_id: 'a1', name: 'retrieve_docs', kind: 'retrieval', started_at: t0 + 1.75, ended_at: t0 + 2.3, inputs: '8 urls', outputs: '5 documents (12.4k chars)' }),
      span({ span_id: 'a5', parent_id: 'a1', name: 'synthesize', kind: 'llm', started_at: t0 + 2.35, ended_at: t0 + 4.1, inputs: '5 documents', outputs: "'Report: 3 key findings…'", llm: { model: 'claude-sonnet-4', provider: 'anthropic', input_tokens: 1980, output_tokens: 445, total_tokens: 2425, cost_usd: 0.0126, prompt_preview: 'Synthesize these documents into a report…', response_preview: 'Report: 3 key findings…' } }),
    ],
  },
  {
    run_id: 'demo-run-b', name: 'research_agent', tags: ['prod', 'rag'], status: 'error',
    started_at: t0 + 1800, ended_at: t0 + 1808.9, duration_ms: 8900, total_tokens: 6210, total_cost_usd: 0.0342,
    error: 'RuntimeError: synthesis model timeout after 2 retries', metadata: {},
    scores: [
      { name: 'faithfulness', value: 0.62, source: 'ragas', threshold: 0.85, passed: false, comment: 'partial context, two claims unsupported', span_id: null },
      { name: 'answer_relevancy', value: 0.81, source: 'ragas', threshold: 0.8, passed: true, comment: '', span_id: null },
    ],
    spans: [
      span({ span_id: 'b1', name: 'research_agent', kind: 'agent', started_at: t0 + 1800, ended_at: t0 + 1808.9, status: 'error', inputs: "{'query': 'agent observability landscape 2026'}", error: 'RuntimeError: synthesis model timeout' }),
      span({ span_id: 'b2', parent_id: 'b1', name: 'plan_steps', kind: 'llm', started_at: t0 + 1800.05, ended_at: t0 + 1801.2, inputs: "'Break the query into research steps'", outputs: "['search', 'retrieve', 'synthesize']", llm: { model: 'gpt-4o-mini', provider: 'openai', input_tokens: 320, output_tokens: 110, total_tokens: 430, cost_usd: 0.000114, prompt_preview: 'Break the query into research steps…', response_preview: '1. search 2. retrieve 3. synthesize' } }),
      span({ span_id: 'b3', parent_id: 'b1', name: 'web_search', kind: 'tool', started_at: t0 + 1801.25, ended_at: t0 + 1803.4, inputs: "'agent observability 2026'", outputs: '11 results' }),
      span({ span_id: 'b4', parent_id: 'b1', name: 'retrieve_docs', kind: 'retrieval', started_at: t0 + 1803.45, ended_at: t0 + 1804.6, inputs: '11 urls', outputs: '9 documents (31.7k chars)' }),
      span({ span_id: 'b5', parent_id: 'b1', name: 'synthesize', kind: 'llm', started_at: t0 + 1804.65, ended_at: t0 + 1806.7, status: 'error', inputs: '9 documents', error: 'TimeoutError: model timeout at 2000ms', llm: { model: 'claude-sonnet-4', provider: 'anthropic', input_tokens: 5340, output_tokens: 0, total_tokens: 5340, cost_usd: 0.016, prompt_preview: 'Synthesize these documents into a report…', response_preview: '' } }),
      span({ span_id: 'b6', parent_id: 'b1', name: 'synthesize', kind: 'llm', retry_of: 'b5', started_at: t0 + 1806.75, ended_at: t0 + 1808.8, status: 'error', inputs: '9 documents', error: 'TimeoutError: model timeout at 2000ms', llm: { model: 'claude-sonnet-4', provider: 'anthropic', input_tokens: 440, output_tokens: 0, total_tokens: 440, cost_usd: 0.0013, prompt_preview: 'Synthesize these documents…', response_preview: '' } }),
    ],
  },
]

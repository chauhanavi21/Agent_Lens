import assert from 'node:assert/strict';
import { test } from 'node:test';

import {
  AgentLens,
  BudgetExceededError,
  currentRun,
  MemoryExporter,
  score,
  type RunData,
} from '../src/index.js';

function setup(options: Partial<ConstructorParameters<typeof AgentLens>[0]> = {}) {
  const exporter = new MemoryExporter();
  return { exporter, lens: new AgentLens({ exporter, ...options }) };
}

const only = (exporter: MemoryExporter): RunData => {
  assert.equal(exporter.runs.length, 1, 'expected exactly one exported run');
  return exporter.runs[0]!;
};

test('builds a DAG with correct parentage', async () => {
  const { lens, exporter } = setup();

  const webSearch = lens.tool('web_search', async (q: string) => [`result for ${q}`]);
  const summarize = lens.span('summarize', async (docs: string[]) => `summary of ${docs.length}`, {
    kind: 'llm',
  });
  const agent = lens.trace('research_agent', async (q: string) => summarize(await webSearch(q)), {
    tags: ['test'],
  });

  assert.equal(await agent('quantum'), 'summary of 1');

  const run = only(exporter);
  assert.equal(run.status, 'success');
  assert.deepEqual(run.spans.map((s) => s.name), ['research_agent', 'web_search', 'summarize']);
  const rootId = run.spans[0]!.span_id;
  assert.equal(run.spans[1]!.parent_id, rootId);
  assert.equal(run.spans[2]!.parent_id, rootId);
  assert.equal(run.spans[0]!.parent_id, null);
  assert.equal(run.tags[0], 'test');
});

test('nests spans by async call depth, not by call order', async () => {
  const { lens, exporter } = setup();

  const inner = lens.tool('inner', async () => 'i');
  const outer = lens.span('outer', async () => inner(), { kind: 'chain' });
  const agent = lens.trace('agent', async () => outer());

  await agent();

  const run = only(exporter);
  const byName = Object.fromEntries(run.spans.map((s) => [s.name, s]));
  assert.equal(byName['outer']!.parent_id, byName['agent']!.span_id);
  assert.equal(byName['inner']!.parent_id, byName['outer']!.span_id, 'inner should nest under outer');
});

test('concurrent spans stay attached to their own parents', async () => {
  const { lens, exporter } = setup();

  const leaf = lens.tool('leaf', async (n: number) => {
    await new Promise((r) => setTimeout(r, 10 - n * 3));
    return n;
  });
  const branch = lens.span('branch', async (n: number) => leaf(n), { kind: 'chain' });
  const agent = lens.trace('agent', async () => Promise.all([branch(0), branch(1), branch(2)]));

  assert.deepEqual(await agent(), [0, 1, 2]);

  const run = only(exporter);
  const branches = run.spans.filter((s) => s.name === 'branch');
  const leaves = run.spans.filter((s) => s.name === 'leaf');
  assert.equal(branches.length, 3);
  assert.equal(leaves.length, 3);
  // each leaf belongs to a distinct branch — the classic async context bug
  const parents = new Set(leaves.map((l) => l.parent_id));
  assert.equal(parents.size, 3, 'leaves collapsed onto one parent');
  for (const l of leaves) {
    assert.ok(branches.some((b) => b.span_id === l.parent_id));
  }
});

test('records retries as linked attempts', async () => {
  const { lens, exporter } = setup();
  let calls = 0;

  const flaky = lens.tool(
    'flaky',
    async () => {
      calls += 1;
      if (calls < 3) throw new Error('boom');
      return 'ok';
    },
    { retries: 2 },
  );
  const agent = lens.trace('retry_agent', async () => flaky());

  assert.equal(await agent(), 'ok');

  const run = only(exporter);
  const attempts = run.spans.filter((s) => s.name === 'flaky');
  assert.equal(attempts.length, 3);
  assert.equal(attempts[0]!.status, 'error');
  assert.equal(attempts[1]!.retry_of, attempts[0]!.span_id);
  assert.equal(attempts[2]!.status, 'success');
  assert.equal(attempts[0]!.retry_of, null);
});

test('propagates errors while still exporting the run', async () => {
  const { lens, exporter } = setup();

  const failing = lens.tool('failing', async () => {
    throw new Error('upstream 503');
  });
  const agent = lens.trace('agent', async () => failing());

  await assert.rejects(agent(), /upstream 503/);

  const run = only(exporter);
  assert.equal(run.status, 'error');
  assert.match(run.error ?? '', /upstream 503/);
  assert.equal(run.spans[1]!.status, 'error');
});

test('extracts token usage and estimates cost', async () => {
  const { lens, exporter } = setup();

  const chat = lens.llmCall(
    'chat',
    async (_prompt: string) => ({ model: 'gpt-4o', usage: { prompt_tokens: 1000, completion_tokens: 500 } }),
    { provider: 'openai' },
  );
  const agent = lens.trace('agent', async () => chat('hello'));

  await agent();

  const run = only(exporter);
  const llm = run.spans.find((s) => s.kind === 'llm')!;
  assert.equal(llm.llm?.input_tokens, 1000);
  assert.equal(llm.llm?.output_tokens, 500);
  assert.equal(llm.llm?.total_tokens, 1500);
  // 1000 * 2.50/1M + 500 * 10.00/1M
  assert.equal(llm.llm?.cost_usd, 0.0075);
  assert.equal(run.total_tokens, 1500);
});

test('handles Anthropic-shaped usage too', async () => {
  const { lens, exporter } = setup();
  const chat = lens.llmCall('chat', async () => ({
    model: 'claude-sonnet-4',
    usage: { input_tokens: 200, output_tokens: 100 },
  }));
  await lens.trace('a', async () => chat())();
  const llm = only(exporter).spans.find((s) => s.kind === 'llm')!;
  assert.equal(llm.llm?.total_tokens, 300);
  assert.ok((llm.llm?.cost_usd ?? 0) > 0);
});

test('trips the budget guard and pauses the run', async () => {
  const { lens, exporter } = setup();

  const chat = lens.llmCall('chat', async () => ({
    model: 'gpt-4o',
    usage: { prompt_tokens: 4000, completion_tokens: 2000 },
  }));
  const agent = lens.trace(
    'budget_agent',
    async () => {
      await chat();
      await chat();
      return 'done';
    },
    { maxTotalTokens: 5000 },
  );

  await assert.rejects(agent(), (e: unknown) => e instanceof BudgetExceededError);

  const run = only(exporter);
  assert.equal(run.status, 'paused');
  assert.equal(run.total_tokens, 6000, 'guard should trip on the first call');
});

test('supports synchronous functions', () => {
  const { lens, exporter } = setup();
  const step = lens.tool('step', (n: number) => n * 2);
  const agent = lens.trace('sync_agent', (n: number) => step(n) + 1);

  assert.equal(agent(20), 41);
  const run = only(exporter);
  assert.equal(run.spans.length, 2);
  assert.equal(run.status, 'success');
});

test('passes through when called outside a run', async () => {
  const { lens, exporter } = setup();
  const step = lens.tool('orphan', async (n: number) => n + 1);

  assert.equal(await step(1), 2);
  assert.equal(exporter.runs.length, 0, 'an untraced call should not invent a run');
  assert.equal(currentRun(), null);
});

test('attaches scores with pass/fail against thresholds', async () => {
  const { lens, exporter } = setup();

  const answer = lens.span(
    'answer',
    async () => {
      score('relevancy', 0.91, { source: 'ragas', threshold: 0.8, onSpan: true });
      return 'a';
    },
    { kind: 'llm' },
  );
  const agent = lens.trace('qa', async () => {
    const out = await answer();
    score('faithfulness', 0.72, { source: 'ragas', threshold: 0.85, comment: 'invented a date' });
    return out;
  });

  await agent();

  const run = only(exporter);
  const scores = Object.fromEntries(run.scores.map((s) => [s.name, s]));
  assert.equal(scores['relevancy']!.passed, true);
  assert.ok(scores['relevancy']!.span_id, 'span-scoped score should carry a span id');
  assert.equal(scores['faithfulness']!.passed, false);
  assert.equal(scores['faithfulness']!.span_id, null);
});

test('scoring outside a run is a safe no-op', () => {
  assert.doesNotThrow(() => score('orphan', 1));
});

test('emits span lifecycle events in order', async () => {
  const { lens, exporter } = setup();
  const step = lens.tool('step', async () => 1);
  await lens.trace('live', async () => step())();

  const types = exporter.events.map((e) => e['type']);
  assert.equal(types[0], 'run_start');
  assert.equal(types.at(-1), 'run_end');
  assert.equal(types.filter((t) => t === 'span_start').length, 2);
  assert.equal(types.filter((t) => t === 'span_end').length, 2);
});

test('a hostile exporter never breaks the agent', async () => {
  const hostile = {
    export() {
      throw new Error('server down');
    },
    exportEvent() {
      throw new Error('server down');
    },
  };
  const lens = new AgentLens({ exporter: hostile });
  const step = lens.tool('step', async () => 1);
  const agent = lens.trace('resilient', async () => (await step()) + 1);

  assert.equal(await agent(), 2);
});

test('previews survive circular and unserializable values', async () => {
  const { lens, exporter } = setup();
  const circular: Record<string, unknown> = { name: 'loop' };
  circular['self'] = circular;

  const agent = lens.trace('agent', (input: unknown) => {
    void input;
    return 'ok';
  });
  assert.equal(agent(circular), 'ok');
  assert.match(only(exporter).spans[0]!.inputs, /Circular/);
});

test('wire format matches the Python SDK', async () => {
  const { lens, exporter } = setup();
  await lens.trace('agent', async () => 'x', { tags: ['a'] })();
  const run = only(exporter);

  for (const key of [
    'run_id', 'trace_id', 'name', 'tags', 'status', 'started_at', 'ended_at',
    'duration_ms', 'total_tokens', 'total_cost_usd', 'error', 'metadata', 'scores', 'spans',
  ]) {
    assert.ok(key in run, `run payload is missing ${key}`);
  }
  for (const key of [
    'span_id', 'parent_id', 'name', 'kind', 'status', 'started_at', 'ended_at',
    'duration_ms', 'inputs', 'outputs', 'error', 'retry_of', 'remote_parent_id',
    'service', 'llm', 'attributes',
  ]) {
    assert.ok(key in run.spans[0]!, `span payload is missing ${key}`);
  }
  assert.equal(run.trace_id.length, 32);
  assert.equal(run.spans[0]!.span_id.length, 16);
});

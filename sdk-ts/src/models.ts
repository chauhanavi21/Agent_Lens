/**
 * Core data model. Kept byte-compatible with the Python SDK's wire format:
 * both write to the same ingest endpoint, and a polyglot system should
 * produce one DAG, not two dialects.
 */

export type SpanStatus = 'running' | 'success' | 'error' | 'cancelled' | 'paused';

export type SpanKind = 'agent' | 'tool' | 'llm' | 'chain' | 'retrieval' | 'mcp' | 'custom';

export interface LLMMetadata {
  model: string;
  provider: string;
  input_tokens: number;
  output_tokens: number;
  total_tokens: number;
  cost_usd: number;
  prompt_preview: string;
  response_preview: string;
  temperature: number | null;
}

export interface SpanData {
  span_id: string;
  parent_id: string | null;
  name: string;
  kind: SpanKind;
  status: SpanStatus;
  started_at: number;
  ended_at: number | null;
  duration_ms: number | null;
  inputs: string;
  outputs: string;
  error: string | null;
  retry_of: string | null;
  remote_parent_id: string | null;
  service: string | null;
  llm: LLMMetadata | null;
  attributes: Record<string, unknown>;
}

export interface ScoreData {
  name: string;
  value: number;
  source: string;
  threshold: number | null;
  passed: boolean | null;
  comment: string;
  span_id: string | null;
  recorded_at: number;
}

export interface RunData {
  run_id: string;
  trace_id: string;
  name: string;
  tags: string[];
  status: SpanStatus;
  started_at: number;
  ended_at: number | null;
  duration_ms: number | null;
  total_tokens: number;
  total_cost_usd: number;
  error: string | null;
  metadata: Record<string, unknown>;
  scores: ScoreData[];
  spans: SpanData[];
}

/** Times are seconds-with-fraction, matching Python's time.time(). */
export const now = (): number => Date.now() / 1000;

export function randomHex(bytes: number): string {
  const out = new Uint8Array(bytes);
  globalThis.crypto.getRandomValues(out);
  return Array.from(out, (b) => b.toString(16).padStart(2, '0')).join('');
}

/**
 * Stringify a value for a span's inputs/outputs. Never throws: a value with
 * a circular reference or a throwing getter must not take down the agent
 * that's merely being observed.
 */
export function preview(value: unknown, limit = 500): string {
  let text: string;
  try {
    if (typeof value === 'string') text = value;
    else {
      const seen = new WeakSet<object>();
      text = JSON.stringify(value, (_k, v) => {
        if (typeof v === 'bigint') return `${v}n`;
        if (typeof v === 'function') return `[Function ${v.name || 'anonymous'}]`;
        if (typeof v === 'object' && v !== null) {
          if (seen.has(v)) return '[Circular]';
          seen.add(v);
        }
        return v;
      }) ?? String(value);
    }
  } catch {
    text = '<unserializable>';
  }
  return text.length <= limit ? text : `${text.slice(0, limit)}…`;
}

export class Span {
  readonly span_id: string;
  parent_id: string | null = null;
  name: string;
  kind: SpanKind;
  status: SpanStatus = 'running';
  started_at: number = now();
  ended_at: number | null = null;
  inputs = '';
  outputs = '';
  error: string | null = null;
  retry_of: string | null = null;
  remote_parent_id: string | null = null;
  service: string | null = null;
  llm: LLMMetadata | null = null;
  attributes: Record<string, unknown> = {};

  constructor(name: string, kind: SpanKind = 'custom', parentId: string | null = null) {
    this.span_id = randomHex(8);
    this.name = name;
    this.kind = kind;
    this.parent_id = parentId;
  }

  get durationMs(): number | null {
    return this.ended_at === null ? null : Math.round((this.ended_at - this.started_at) * 100000) / 100;
  }

  finish(status: SpanStatus = 'success', error?: string): void {
    this.ended_at = now();
    this.status = status;
    if (error) this.error = error;
  }

  toJSON(): SpanData {
    return {
      span_id: this.span_id,
      parent_id: this.parent_id,
      name: this.name,
      kind: this.kind,
      status: this.status,
      started_at: this.started_at,
      ended_at: this.ended_at,
      duration_ms: this.durationMs,
      inputs: this.inputs,
      outputs: this.outputs,
      error: this.error,
      retry_of: this.retry_of,
      remote_parent_id: this.remote_parent_id,
      service: this.service,
      llm: this.llm,
      attributes: this.attributes,
    };
  }
}

export class AgentRun {
  readonly run_id: string;
  trace_id: string;
  name: string;
  tags: string[];
  status: SpanStatus = 'running';
  started_at: number = now();
  ended_at: number | null = null;
  error: string | null = null;
  spans: Span[] = [];
  scores: ScoreData[] = [];
  metadata: Record<string, unknown> = {};
  maxTotalTokens: number | null = null;
  maxCostUsd: number | null = null;

  constructor(name: string, tags: string[] = []) {
    this.run_id = randomHex(16);
    this.trace_id = randomHex(16);
    this.name = name;
    this.tags = tags;
  }

  get durationMs(): number | null {
    return this.ended_at === null ? null : Math.round((this.ended_at - this.started_at) * 100000) / 100;
  }

  get totalTokens(): number {
    return this.spans.reduce((sum, s) => sum + (s.llm?.total_tokens ?? 0), 0);
  }

  get totalCostUsd(): number {
    return Math.round(this.spans.reduce((sum, s) => sum + (s.llm?.cost_usd ?? 0), 0) * 1e6) / 1e6;
  }

  /** A human-readable reason when a budget guard has tripped, else null. */
  overBudget(): string | null {
    if (this.maxTotalTokens !== null && this.totalTokens > this.maxTotalTokens) {
      return `token budget exceeded: ${this.totalTokens} > ${this.maxTotalTokens}`;
    }
    if (this.maxCostUsd !== null && this.totalCostUsd > this.maxCostUsd) {
      return `cost budget exceeded: $${this.totalCostUsd.toFixed(4)} > $${this.maxCostUsd.toFixed(4)}`;
    }
    return null;
  }

  finish(status: SpanStatus = 'success', error?: string): void {
    this.ended_at = now();
    this.status = status;
    if (error) this.error = error;
  }

  toJSON(): RunData {
    return {
      run_id: this.run_id,
      trace_id: this.trace_id,
      name: this.name,
      tags: this.tags,
      status: this.status,
      started_at: this.started_at,
      ended_at: this.ended_at,
      duration_ms: this.durationMs,
      total_tokens: this.totalTokens,
      total_cost_usd: this.totalCostUsd,
      error: this.error,
      metadata: this.metadata,
      scores: this.scores,
      spans: this.spans.map((s) => s.toJSON()),
    };
  }
}

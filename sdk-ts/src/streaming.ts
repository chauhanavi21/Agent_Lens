/** Span lifecycle events, matching the Python SDK's wire format. */

import type { AgentRun, Span } from './models.js';
import { now } from './models.js';

export function runStartEvent(run: AgentRun): Record<string, unknown> {
  return {
    type: 'run_start',
    ts: now(),
    run_id: run.run_id,
    trace_id: run.trace_id,
    run: {
      run_id: run.run_id,
      trace_id: run.trace_id,
      name: run.name,
      tags: run.tags,
      status: 'running',
      started_at: run.started_at,
    },
  };
}

export function spanEvent(run: AgentRun, span: Span, type: 'span_start' | 'span_end'): Record<string, unknown> {
  return { type, ts: now(), run_id: run.run_id, trace_id: run.trace_id, span: span.toJSON() };
}

export function runEndEvent(run: AgentRun): Record<string, unknown> {
  return { type: 'run_end', ts: now(), run_id: run.run_id, run: run.toJSON() };
}

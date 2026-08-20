/**
 * Run/span propagation via AsyncLocalStorage — Node's equivalent of
 * Python's contextvars. Nesting survives await boundaries, Promise.all,
 * and callbacks without any explicit passing of a context object.
 */

import { AsyncLocalStorage } from 'node:async_hooks';
import type { AgentRun, Span } from './models.js';

export interface TraceContext {
  run: AgentRun;
  span: Span | null;
}

const storage = new AsyncLocalStorage<TraceContext>();

export function currentContext(): TraceContext | undefined {
  return storage.getStore();
}

export function currentRun(): AgentRun | null {
  return storage.getStore()?.run ?? null;
}

export function currentSpan(): Span | null {
  return storage.getStore()?.span ?? null;
}

/** Run `fn` with the given context active for its whole async subtree. */
export function withContext<T>(ctx: TraceContext, fn: () => T): T {
  return storage.run(ctx, fn);
}

/** Run `fn` with `span` as the active span, keeping the current run. */
export function withSpan<T>(span: Span, fn: () => T): T {
  const ctx = storage.getStore();
  if (!ctx) return fn();
  return storage.run({ run: ctx.run, span }, fn);
}

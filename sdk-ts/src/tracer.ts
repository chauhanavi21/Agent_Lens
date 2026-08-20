/**
 * The tracer. Python uses decorators; JavaScript's are still awkward and
 * don't apply to plain functions, so this wraps instead — which also works
 * on arrow functions, closures, and third-party callbacks.
 *
 *   const lens = new AgentLens({ endpoint: 'http://localhost:7430' });
 *
 *   const search = lens.tool('web_search', async (q: string) => { … });
 *
 *   const agent = lens.trace('research_agent', async (q: string) => {
 *     return summarize(await search(q));
 *   }, { maxCostUsd: 0.1 });
 *
 * Sync and async functions both work; the return type is preserved.
 */

import { currentContext, currentRun, currentSpan, withContext, withSpan } from './context.js';
import { estimateCostUsd, extractUsage, type CostTable } from './cost.js';
import { ConsoleExporter, HttpExporter, type Exporter } from './exporters.js';
import { AgentRun, Span, preview, type SpanKind } from './models.js';
import { runEndEvent, runStartEvent, spanEvent } from './streaming.js';

export class BudgetExceededError extends Error {
  constructor(readonly run: AgentRun, readonly reason: string) {
    super(`AgentLens budget guard: ${reason} (run=${run.name})`);
    this.name = 'BudgetExceededError';
  }
}

export interface AgentLensOptions {
  endpoint?: string;
  apiKey?: string;
  exporter?: Exporter;
  costTable?: CostTable;
  onBudget?: 'raise' | 'pause' | 'warn';
  stream?: boolean;
}

export interface TraceOptions {
  tags?: string[];
  maxTotalTokens?: number;
  maxCostUsd?: number;
  metadata?: Record<string, unknown>;
}

export interface SpanOptions {
  kind?: SpanKind;
  retries?: number;
}

export interface LLMOptions {
  model?: string;
  provider?: string;
}

type AnyFn<A extends unknown[], R> = (...args: A) => R;

function errorText(e: unknown): string {
  if (e instanceof Error) return e.stack || `${e.name}: ${e.message}`;
  return String(e);
}

function isPromise(value: unknown): value is Promise<unknown> {
  return typeof (value as { then?: unknown } | null)?.then === 'function';
}

export class AgentLens {
  readonly exporter: Exporter;
  readonly costTable: CostTable;
  private readonly onBudget: 'raise' | 'pause' | 'warn';

  constructor(options: AgentLensOptions = {}) {
    this.exporter =
      options.exporter ??
      (options.endpoint
        ? new HttpExporter(options.endpoint, { apiKey: options.apiKey, stream: options.stream })
        : new ConsoleExporter());
    this.costTable = options.costTable ?? {};
    this.onBudget = options.onBudget ?? 'raise';
  }

  private emit(event: Record<string, unknown>): void {
    try {
      this.exporter.exportEvent?.(event);
    } catch {
      /* a live view is never worth failing a run over */
    }
  }

  /** Wrap a function as an agent entrypoint: it owns a run. */
  trace<A extends unknown[], R>(name: string, fn: AnyFn<A, R>, options: TraceOptions = {}): AnyFn<A, R> {
    return ((...args: A): R => {
      const run = new AgentRun(name, options.tags ?? []);
      run.maxTotalTokens = options.maxTotalTokens ?? null;
      run.maxCostUsd = options.maxCostUsd ?? null;
      run.metadata = options.metadata ?? {};

      const root = new Span(name, 'agent');
      root.inputs = preview(args);
      run.spans.push(root);

      const settle = (result: unknown, error: unknown): void => {
        if (error === undefined) {
          root.outputs = preview(result);
          root.finish('success');
          run.finish('success');
        } else if (error instanceof BudgetExceededError) {
          root.finish('paused', error.message);
          run.finish('paused', error.message);
        } else {
          root.finish('error', errorText(error));
          run.finish('error', error instanceof Error ? error.message : String(error));
        }
        this.emit(spanEvent(run, root, 'span_end'));
        this.emit(runEndEvent(run));
        try {
          this.exporter.export(run);
        } catch {
          /* tracing must not take the agent down */
        }
      };

      return withContext({ run, span: root }, () => {
        this.emit(runStartEvent(run));
        this.emit(spanEvent(run, root, 'span_start'));
        try {
          const result = fn(...args);
          if (isPromise(result)) {
            return result.then(
              (value) => {
                settle(value, undefined);
                return value;
              },
              (err) => {
                settle(undefined, err);
                throw err;
              },
            ) as R;
          }
          settle(result, undefined);
          return result;
        } catch (err) {
          settle(undefined, err);
          throw err;
        }
      });
    }) as AnyFn<A, R>;
  }

  /** Wrap a function as a child step of the current run. */
  span<A extends unknown[], R>(name: string, fn: AnyFn<A, R>, options: SpanOptions = {}): AnyFn<A, R> {
    const kind = options.kind ?? 'custom';
    const retries = options.retries ?? 0;

    return ((...args: A): R => {
      const ctx = currentContext();
      if (!ctx) return fn(...args); // untraced call passes straight through

      const attempt = (n: number, retryOf: string | null): R => {
        const span = new Span(name, kind, ctx.span?.span_id ?? null);
        span.retry_of = retryOf;
        span.inputs = preview(args);
        ctx.run.spans.push(span);
        this.emit(spanEvent(ctx.run, span, 'span_start'));

        const closeOk = (value: unknown): void => {
          span.outputs = preview(value);
          span.finish('success');
          this.emit(spanEvent(ctx.run, span, 'span_end'));
          this.checkBudget(ctx.run);
        };
        const closeErr = (err: unknown): void => {
          span.finish('error', errorText(err));
          this.emit(spanEvent(ctx.run, span, 'span_end'));
        };

        return withSpan(span, () => {
          try {
            const result = fn(...args);
            if (isPromise(result)) {
              return result.then(
                (value) => {
                  closeOk(value);
                  return value;
                },
                (err) => {
                  if (err instanceof BudgetExceededError) {
                    span.finish('success');
                    throw err;
                  }
                  closeErr(err);
                  // failed attempts stay in the DAG, linked by retry lineage
                  if (n < retries) return attempt(n + 1, span.span_id);
                  throw err;
                },
              ) as R;
            }
            closeOk(result);
            return result;
          } catch (err) {
            if (err instanceof BudgetExceededError) {
              span.finish('success');
              throw err;
            }
            closeErr(err);
            if (n < retries) return attempt(n + 1, span.span_id);
            throw err;
          }
        });
      };

      return attempt(0, null);
    }) as AnyFn<A, R>;
  }

  /** Shorthand for a tool-kind span. */
  tool<A extends unknown[], R>(name: string, fn: AnyFn<A, R>, options: { retries?: number } = {}): AnyFn<A, R> {
    return this.span(name, fn, { kind: 'tool', retries: options.retries ?? 0 });
  }

  /**
   * Trace an LLM call, reading token usage off the response and estimating
   * cost from the model name.
   */
  llmCall<A extends unknown[], R>(name: string, fn: AnyFn<A, R>, options: LLMOptions = {}): AnyFn<A, R> {
    const inner = this.span(name, fn, { kind: 'llm' });

    const record = (args: A, result: unknown): void => {
      const run = currentRun();
      if (!run) return;
      const span = [...run.spans].reverse().find((s) => s.kind === 'llm' && s.name === name);
      if (!span) return;

      const usage = extractUsage(result);
      const model = usage.model || options.model || '';
      span.llm = {
        model,
        provider: options.provider ?? '',
        input_tokens: usage.input,
        output_tokens: usage.output,
        total_tokens: usage.input + usage.output,
        cost_usd: estimateCostUsd(model, usage.input, usage.output, this.costTable),
        prompt_preview: preview(args[0]),
        response_preview: preview(result),
        temperature: null,
      };
      this.checkBudget(run);
    };

    return ((...args: A): R => {
      const result = inner(...args);
      if (isPromise(result)) {
        return result.then((value) => {
          record(args, value);
          return value;
        }) as R;
      }
      record(args, result);
      return result;
    }) as AnyFn<A, R>;
  }

  private checkBudget(run: AgentRun): void {
    const reason = run.overBudget();
    if (!reason) return;
    if (this.onBudget === 'raise') throw new BudgetExceededError(run, reason);
    if (this.onBudget === 'pause') {
      run.status = 'paused';
      run.error = reason;
    } else {
      console.warn(`[agentlens] WARNING: ${reason}`);
    }
  }
}

/** Attach a score to the active run. A no-op outside one, never a throw. */
export function score(
  name: string,
  value: number,
  options: { source?: string; threshold?: number; comment?: string; onSpan?: boolean } = {},
): void {
  const run = currentRun();
  if (!run) return;
  const threshold = options.threshold ?? null;
  run.scores.push({
    name,
    value,
    source: options.source ?? 'custom',
    threshold,
    passed: threshold === null ? null : value >= threshold,
    comment: options.comment ?? '',
    span_id: options.onSpan ? (currentSpan()?.span_id ?? null) : null,
    recorded_at: Date.now() / 1000,
  });
}

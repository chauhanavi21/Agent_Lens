/**
 * AgentLens — open source observability runtime for AI agents.
 *
 * Traces the whole agent, not just its LLM calls: the execution DAG, tool
 * calls, retries, cost per node, and run diffs. Wire-compatible with the
 * Python SDK, so a polyglot system produces one DAG.
 */

export { currentRun, currentSpan, withContext, withSpan } from './context.js';
export {
  DEFAULT_COST_TABLE,
  estimateCost,
  estimateCostUsd,
  extractReportedCost,
  extractUsage,
  lookupPrice,
  type CostSource,
  type CostTable,
} from './cost.js';
export {
  ConsoleExporter,
  FileExporter,
  HttpExporter,
  MemoryExporter,
  MultiExporter,
  type Exporter,
} from './exporters.js';
export {
  AgentRun,
  Span,
  preview,
  type LLMMetadata,
  type RunData,
  type ScoreData,
  type SpanData,
  type SpanKind,
  type SpanStatus,
} from './models.js';
export { runEndEvent, runStartEvent, spanEvent } from './streaming.js';
export {
  AgentLens,
  BudgetExceededError,
  score,
  type AgentLensOptions,
  type LLMOptions,
  type SpanOptions,
  type TraceOptions,
} from './tracer.js';

export const VERSION = '0.3.0';

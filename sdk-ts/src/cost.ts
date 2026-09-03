/** Best-effort cost estimation. Prices are USD per 1M tokens (input, output). */

export type CostTable = Record<string, [number, number]>;

export const DEFAULT_COST_TABLE: CostTable = {
  'gpt-4o': [2.5, 10.0],
  'gpt-4o-mini': [0.15, 0.6],
  'gpt-4.1': [2.0, 8.0],
  'gpt-4.1-mini': [0.4, 1.6],
  'o3-mini': [1.1, 4.4],
  'claude-3-5-haiku': [0.8, 4.0],
  'claude-sonnet-4': [3.0, 15.0],
  'claude-opus-4': [15.0, 75.0],
  'llama-3.1-70b': [0.6, 0.8],
  'llama-3.1-8b': [0.1, 0.2],
  'mixtral-8x7b': [0.5, 0.5],
};

/**
 * Where a cost figure came from.
 *
 * An unrecognized model estimates at 0, which reads as "this was free" when
 * it means "nobody priced this". Carrying the provenance lets a total say
 * how much of itself it actually knows.
 */
export type CostSource = 'reported' | 'table' | 'unpriced' | 'free';

export function lookupPrice(model: string, overrides: CostTable = {}): [number, number] | null {
  const table = { ...DEFAULT_COST_TABLE, ...overrides };
  const lowered = (model || '').toLowerCase();
  if (!lowered) return null;
  // longest key first, so gpt-4o-mini isn't billed at gpt-4o rates
  const key = Object.keys(table)
    .sort((a, b) => b.length - a.length)
    .find((k) => lowered.includes(k));
  return key ? table[key]! : null;
}

export function estimateCost(
  model: string,
  inputTokens: number,
  outputTokens: number,
  overrides: CostTable = {},
  reportedCost?: number | null,
): { cost: number; source: CostSource } {
  // a provider-reported cost is authoritative; a local table is a guess
  // about someone else's billing
  if (typeof reportedCost === 'number' && Number.isFinite(reportedCost)) {
    return { cost: Math.round(reportedCost * 1e6) / 1e6, source: 'reported' };
  }

  const price = lookupPrice(model, overrides);
  if (!price) return { cost: 0, source: 'unpriced' };

  const [inPrice, outPrice] = price;
  if (inPrice === 0 && outPrice === 0) return { cost: 0, source: 'free' };
  return { cost: (inputTokens * inPrice + outputTokens * outPrice) / 1_000_000, source: 'table' };
}

/** A cost the provider already calculated, if the response carries one. */
export function extractReportedCost(result: unknown): number | null {
  if (typeof result !== 'object' || result === null) return null;
  const r = result as Record<string, unknown>;
  const holders = [r, r['usage']];
  for (const holder of holders) {
    if (typeof holder !== 'object' || holder === null) continue;
    for (const key of ['cost', 'total_cost', 'cost_usd']) {
      const value = (holder as Record<string, unknown>)[key];
      if (typeof value === 'number' && Number.isFinite(value)) return value;
    }
  }
  return null;
}

export function estimateCostUsd(
  model: string,
  inputTokens: number,
  outputTokens: number,
  overrides: CostTable = {},
): number {
  return estimateCost(model, inputTokens, outputTokens, overrides).cost;
}

/** Pull token usage out of an OpenAI- or Anthropic-shaped response. */
export function extractUsage(result: unknown): { input: number; output: number; model: string } {
  const out = { input: 0, output: 0, model: '' };
  if (typeof result !== 'object' || result === null) return out;
  const r = result as Record<string, any>;
  if (typeof r['model'] === 'string') out.model = r['model'];
  const usage = r['usage'];
  if (typeof usage === 'object' && usage !== null) {
    const u = usage as Record<string, any>;
    out.input = Number(u['prompt_tokens'] ?? u['input_tokens'] ?? u['inputTokens'] ?? 0) || 0;
    out.output = Number(u['completion_tokens'] ?? u['output_tokens'] ?? u['outputTokens'] ?? 0) || 0;
  }
  return out;
}

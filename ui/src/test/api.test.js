import { beforeEach, describe, expect, test, vi } from 'vitest'

/**
 * Demo mode is what a visitor gets with no server running, so it has to work
 * without touching the network at all — these tests fail loudly if it does.
 */

let api

beforeEach(async () => {
  vi.resetModules()
  vi.stubEnv('VITE_API_URL', '')
  globalThis.fetch = vi.fn(() => Promise.reject(new Error('demo mode must not fetch')))
  api = await import('../api')
})

describe('api in demo mode', () => {
  test('reports demo mode and serves bundled runs', async () => {
    expect(api.DEMO).toBe(true)
    const runs = await api.listRuns()
    expect(runs.length).toBeGreaterThan(0)
    expect(runs[0]).toHaveProperty('span_count')
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  test('filters by status and name', async () => {
    expect(await api.listRuns({ status: 'error' })).toHaveLength(1)
    expect(await api.listRuns({ name: 'research' })).toHaveLength(2)
    expect(await api.listRuns({ name: 'nope' })).toHaveLength(0)
  })

  test('fetches a single run with its spans', async () => {
    const run = await api.getRun('demo-run-a')
    expect(run.spans.length).toBeGreaterThan(0)
    expect(run.scores.length).toBeGreaterThan(0)
  })

  test('diffs two runs locally', async () => {
    const diff = await api.diffRuns('demo-run-a', 'demo-run-b')
    expect(diff.summary.verdict).toBeTruthy()
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  test('score trends are ordered oldest first', async () => {
    const trends = await api.scoreTrends()
    expect(trends.metrics).toContain('faithfulness')
    const series = trends.series.faithfulness
    expect(series.length).toBeGreaterThan(1)
    for (let i = 1; i < series.length; i += 1) {
      expect(series[i].started_at).toBeGreaterThanOrEqual(series[i - 1].started_at)
    }
  })

  test('alert rules can be created and deleted in memory', async () => {
    const before = (await api.listRules()).length
    const rule = await api.createRule({ name: 'x', field: 'status', op: 'eq', value: 'error', webhook_url: 'https://example.invalid' })
    expect((await api.listRules()).length).toBe(before + 1)

    await api.deleteRule(rule.id)
    expect((await api.listRules()).length).toBe(before)
    expect(globalThis.fetch).not.toHaveBeenCalled()
  })

  test('test-firing a rule explains that demo mode sends nothing', async () => {
    const result = await api.testRule('demo1')
    expect(result.delivered).toBe(false)
    expect(result.error).toMatch(/demo/)
  })

  test('the demo data includes a stitched MCP trace', async () => {
    // the offline demo should still show cross-process tracing
    const run = await api.getRun('demo-run-a')
    const mcp = run.spans.filter((s) => s.kind === 'mcp')
    expect(mcp.length).toBeGreaterThan(0)
    expect(run.spans.some((s) => s.service === 'brave-search')).toBe(true)
  })
})

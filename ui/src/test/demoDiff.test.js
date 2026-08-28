import { describe, expect, test } from 'vitest'
import { demoDiff } from '../demoDiff'

/**
 * Demo mode mirrors the server's diff logic in the browser. The two must
 * agree, or the offline demo teaches people something the product doesn't
 * actually do — so these mirror server/tests/test_diff.py case for case.
 */

const run = (id, status, faith, duration, { extraSpan = false } = {}) => ({
  run_id: id,
  name: 'agent',
  status,
  duration_ms: duration,
  total_tokens: 100,
  total_cost_usd: 0.01,
  scores: [{ name: 'faithfulness', value: faith, passed: faith >= 0.85 }],
  spans: [
    { span_id: `${id}1`, parent_id: null, name: 'agent', kind: 'agent', status, started_at: 1, duration_ms: duration },
    { span_id: `${id}2`, parent_id: `${id}1`, name: 'search', kind: 'tool', status: 'success', started_at: 1.1, duration_ms: 100 },
    ...(extraSpan
      ? [{ span_id: `${id}3`, parent_id: `${id}1`, name: 'rerank', kind: 'tool', status: 'success', started_at: 1.3, duration_ms: 100 }]
      : []),
  ],
})

describe('demoDiff', () => {
  test('identical runs report equivalence', () => {
    const d = demoDiff(run('a', 'success', 0.9, 500), run('b', 'success', 0.9, 500))
    expect(d.summary.verdict).toMatch(/structurally/)
    expect(d.summary.changed).toBe(0)
  })

  test('a quality drop leads the verdict even when the DAG matches', () => {
    const d = demoDiff(run('a', 'success', 0.92, 500), run('b', 'success', 0.61, 500))
    expect(d.summary.verdict).toMatch(/^Quality dropped/)
    expect(d.scores[0].delta).toBeLessThan(0)
  })

  test('a status flip names the deepest span, not the root', () => {
    const a = run('a', 'success', 0.9, 500)
    const b = run('b', 'error', 0.9, 500)
    b.spans[1].status = 'error'
    expect(demoDiff(a, b).summary.verdict).toMatch(/agent\.search/)
  })

  test('added spans are reported by path', () => {
    const d = demoDiff(run('a', 'success', 0.9, 500), run('b', 'success', 0.9, 500, { extraSpan: true }))
    expect(d.summary.added).toBe(1)
    expect(d.added[0].path).toBe('agent.rerank#0')
    expect(d.summary.removed).toBe(0)
  })

  test('small latency shifts are noise, large ones are findings', () => {
    expect(demoDiff(run('a', 'success', 0.9, 500), run('b', 'success', 0.9, 520)).summary.changed).toBe(0)
    expect(demoDiff(run('a', 'success', 0.9, 500), run('b', 'success', 0.9, 1500)).summary.changed).toBe(1)
  })

  test('unchanged scores are omitted rather than listed as zero deltas', () => {
    const d = demoDiff(run('a', 'success', 0.9, 500), run('b', 'success', 0.9, 500))
    expect(d.scores).toEqual([])
  })
})

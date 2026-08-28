import { act, renderHook, waitFor } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'

/**
 * The SSE hook folds a stream of span events into run state. Getting this
 * wrong shows up as a DAG that grows duplicate nodes or never clears a
 * finished run, so the folding is tested directly rather than through the UI.
 */

class FakeEventSource {
  static instances = []

  constructor(url) {
    this.url = url
    this.listeners = {}
    this.closed = false
    FakeEventSource.instances.push(this)
  }

  addEventListener(type, fn) {
    ;(this.listeners[type] ||= []).push(fn)
  }

  close() {
    this.closed = true
  }

  emit(type, payload) {
    const event = { data: JSON.stringify(payload) }
    for (const fn of this.listeners[type] || []) fn(event)
    if (typeof this.onmessage === 'function') this.onmessage(event)
  }

  open() {
    this.onopen?.()
  }

  fail() {
    this.onerror?.()
  }
}

const spanEvent = (runId, spanId, name, type = 'span_start', status = 'running') => ({
  type,
  run_id: runId,
  ts: Date.now() / 1000,
  span: { span_id: spanId, parent_id: null, name, kind: 'tool', status, started_at: 1 },
})

let useLiveRuns

beforeEach(async () => {
  vi.resetModules()
  FakeEventSource.instances = []
  globalThis.EventSource = FakeEventSource
  // the hook is inert in demo mode, so give it a server to "connect" to
  vi.stubEnv('VITE_API_URL', 'http://localhost:7430')
  ;({ default: useLiveRuns } = await import('../useLiveRuns'))
})

afterEach(() => {
  vi.unstubAllEnvs()
})

describe('useLiveRuns', () => {
  test('opens one stream and reports connection state', async () => {
    const { result } = renderHook(() => useLiveRuns())
    expect(FakeEventSource.instances).toHaveLength(1)
    expect(result.current.connected).toBe(false)

    act(() => FakeEventSource.instances[0].open())
    await waitFor(() => expect(result.current.connected).toBe(true))

    act(() => FakeEventSource.instances[0].fail())
    await waitFor(() => expect(result.current.connected).toBe(false))
  })

  test('builds a run from its events', async () => {
    const { result } = renderHook(() => useLiveRuns())
    const es = FakeEventSource.instances[0]

    act(() =>
      es.emit('run_start', {
        type: 'run_start',
        run_id: 'r1',
        run: { run_id: 'r1', name: 'agent', tags: [], status: 'running', started_at: 1 },
      }),
    )
    act(() => es.emit('span_start', spanEvent('r1', 's1', 'search')))

    await waitFor(() => {
      expect(result.current.liveRuns).toHaveLength(1)
      expect(result.current.liveRuns[0].spans).toHaveLength(1)
    })
  })

  test('span_end replaces span_start rather than duplicating the node', async () => {
    const { result } = renderHook(() => useLiveRuns())
    const es = FakeEventSource.instances[0]

    act(() => es.emit('span_start', spanEvent('r1', 's1', 'search')))
    act(() => es.emit('span_end', spanEvent('r1', 's1', 'search', 'span_end', 'success')))

    await waitFor(() => {
      const spans = result.current.liveById['r1'].spans
      expect(spans).toHaveLength(1)
      expect(spans[0].status).toBe('success')
    })
  })

  test('events arriving before run_start are kept, not dropped', async () => {
    // a browser can connect mid-run; dropping these shows a broken DAG
    const { result } = renderHook(() => useLiveRuns())
    act(() => FakeEventSource.instances[0].emit('span_start', spanEvent('ghost', 's1', 'orphan')))

    await waitFor(() => {
      expect(result.current.liveById['ghost'].spans).toHaveLength(1)
      expect(result.current.liveById['ghost'].name).toMatch(/starting/)
    })
  })

  test('run_end removes the run from live state', async () => {
    const { result } = renderHook(() => useLiveRuns())
    const es = FakeEventSource.instances[0]

    act(() => es.emit('span_start', spanEvent('r1', 's1', 'search')))
    await waitFor(() => expect(result.current.liveRuns).toHaveLength(1))

    act(() => es.emit('run_end', { type: 'run_end', run_id: 'r1', run: { run_id: 'r1' } }))
    await waitFor(() => expect(result.current.liveRuns).toHaveLength(0))
  })

  test('malformed event data is ignored instead of crashing the view', () => {
    const { result } = renderHook(() => useLiveRuns())
    const es = FakeEventSource.instances[0]

    expect(() => {
      act(() => {
        for (const fn of es.listeners['span_start'] || []) fn({ data: 'not json{' })
      })
    }).not.toThrow()
    expect(result.current.liveRuns).toHaveLength(0)
  })

  test('closes the stream on unmount', () => {
    const { unmount } = renderHook(() => useLiveRuns())
    const es = FakeEventSource.instances[0]
    unmount()
    expect(es.closed).toBe(true)
  })
})

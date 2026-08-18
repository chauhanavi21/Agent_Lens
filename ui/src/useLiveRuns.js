import { useEffect, useRef, useState } from 'react'
import { DEMO, streamUrl } from './api'

/**
 * Subscribe to the server's SSE stream and fold events into live run state.
 *
 * EventSource reconnects on its own, but it can't tell us whether the gap
 * lost events — so on every (re)connect we treat the server's snapshot as
 * authoritative rather than trusting locally accumulated state.
 */
export default function useLiveRuns() {
  const [liveRuns, setLiveRuns] = useState({})   // run_id -> partial run
  const [connected, setConnected] = useState(false)
  const [lastEventAt, setLastEventAt] = useState(null)
  const sourceRef = useRef(null)

  useEffect(() => {
    if (DEMO) return   // no server to stream from

    let closed = false
    const es = new EventSource(streamUrl())
    sourceRef.current = es

    const fold = e => {
      let event
      try { event = JSON.parse(e.data) } catch { return }
      setLastEventAt(Date.now())

      setLiveRuns(prev => {
        const next = { ...prev }
        if (event.type === 'run_start') {
          next[event.run_id] = { ...event.run, spans: [] }
        } else if (event.type === 'span_start' || event.type === 'span_end') {
          const run = next[event.run_id] || {
            run_id: event.run_id, name: '(starting…)', tags: [],
            status: 'running', started_at: event.ts, spans: [],
          }
          const spans = run.spans.filter(s => s.span_id !== event.span.span_id)
          next[event.run_id] = { ...run, spans: [...spans, event.span] }
        } else if (event.type === 'run_end') {
          delete next[event.run_id]
        }
        return next
      })
    }

    es.onopen = () => { if (!closed) setConnected(true) }
    es.onerror = () => { if (!closed) setConnected(false) }
    es.onmessage = fold
    for (const t of ['connected', 'run_start', 'span_start', 'span_end', 'run_end']) {
      es.addEventListener(t, fold)
    }

    return () => { closed = true; es.close() }
  }, [])

  return { liveRuns: Object.values(liveRuns), liveById: liveRuns, connected, lastEventAt }
}

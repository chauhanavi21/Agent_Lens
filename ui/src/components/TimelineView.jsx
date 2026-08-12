import React, { useMemo, useState } from 'react'

const KIND_COLOR = {
  agent: 'var(--kind-agent)', llm: 'var(--kind-llm)', tool: 'var(--kind-tool)',
  retrieval: 'var(--kind-retrieval)', chain: 'var(--kind-chain)', custom: 'var(--kind-custom)',
}

const fmt = ms => (ms >= 1000 ? `${(ms / 1000).toFixed(2)}s` : `${Math.round(ms)}ms`)

/**
 * Waterfall view: where the wall-clock time actually went. The DAG shows
 * structure; this shows duration, overlap, and the critical path.
 */
export default function TimelineView({ run, selectedSpan, onSelectSpan }) {
  const [hover, setHover] = useState(null)

  const { rows, total, t0 } = useMemo(() => {
    const spans = run.spans
    const byId = Object.fromEntries(spans.map(s => [s.span_id, s]))
    const depth = s => {
      let d = 0, cur = s
      while (cur?.parent_id && byId[cur.parent_id]) { d++; cur = byId[cur.parent_id] }
      return d
    }
    const start = Math.min(...spans.map(s => s.started_at))
    const end = Math.max(...spans.map(s => s.ended_at ?? s.started_at))
    const ordered = [...spans].sort((a, b) => a.started_at - b.started_at)
    return {
      rows: ordered.map(s => ({ span: s, depth: depth(s) })),
      total: Math.max((end - start) * 1000, 1),
      t0: start,
    }
  }, [run])

  // critical path: the longest-running leaf chain, highlighted for triage
  const criticalId = useMemo(() => {
    const leaf = run.spans
      .filter(s => !run.spans.some(c => c.parent_id === s.span_id))
      .sort((a, b) => (b.duration_ms || 0) - (a.duration_ms || 0))[0]
    return leaf?.span_id
  }, [run])

  const ticks = [0, 0.25, 0.5, 0.75, 1]

  return (
    <div className="timeline">
      <div className="timeline-axis">
        {ticks.map(t => (
          <span key={t} className="tick" style={{ left: `${t * 100}%` }}>{fmt(total * t)}</span>
        ))}
      </div>
      <div className="timeline-rows">
        {rows.map(({ span: s, depth }) => {
          const offset = ((s.started_at - t0) * 1000 / total) * 100
          const width = Math.max(((s.duration_ms ?? 0) / total) * 100, 0.6)
          const isSel = selectedSpan?.span_id === s.span_id
          return (
            <div
              key={s.span_id}
              className={`tl-row ${isSel ? 'selected' : ''}`}
              onClick={() => onSelectSpan(s)}
              onMouseEnter={() => setHover(s.span_id)}
              onMouseLeave={() => setHover(null)}
            >
              <div className="tl-label" style={{ paddingLeft: depth * 14 }}>
                <span className="tl-dot" style={{ background: KIND_COLOR[s.kind] }} />
                <span className="tl-name">{s.name}</span>
                {s.retry_of && <span className="tl-flag retry">↻</span>}
                {s.status === 'error' && <span className="tl-flag err">✕</span>}
              </div>
              <div className="tl-track">
                <div
                  className={`tl-bar status-${s.status} ${s.span_id === criticalId ? 'critical' : ''}`}
                  style={{ left: `${offset}%`, width: `${width}%`, background: KIND_COLOR[s.kind] }}
                />
                {hover === s.span_id && (
                  <div className="tl-tip" style={{ left: `${Math.min(offset, 70)}%` }}>
                    {fmt(s.duration_ms ?? 0)}
                    {s.llm?.total_tokens ? ` · ${s.llm.total_tokens} tok` : ''}
                    {s.llm?.cost_usd ? ` · $${s.llm.cost_usd.toFixed(4)}` : ''}
                  </div>
                )}
              </div>
              <div className="tl-dur">{fmt(s.duration_ms ?? 0)}</div>
            </div>
          )
        })}
      </div>
      <div className="timeline-foot">
        Slowest leaf step is outlined. Click any bar for full span detail.
      </div>
    </div>
  )
}

import React, { useEffect, useMemo, useState } from 'react'
import { scoreTrends } from '../api'

/**
 * Quality over time. Sparkline per metric, oldest run on the left, so a
 * slow regression is visible before anyone files a bug about it.
 */
export default function ScoreTrend({ agentName }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    scoreTrends(agentName).then(setData).catch(e => setError(String(e.message || e)))
  }, [agentName])

  if (error) return <div className="empty-inline">Couldn't load score history: {error}</div>
  if (!data) return <div className="empty-inline">Loading score history…</div>
  if (!data.metrics.length) {
    return (
      <div className="empty-inline">
        No scores recorded yet. Call <code>score("faithfulness", 0.86)</code> inside a traced run,
        or post eval results to <code>/api/ingest/scores</code>.
      </div>
    )
  }

  return (
    <div className="trend-grid">
      {data.metrics.map(m => (
        <Spark key={m} name={m} points={data.series[m]} />
      ))}
    </div>
  )
}

function Spark({ name, points }) {
  const { path, dots, last, delta } = useMemo(() => {
    const W = 220, H = 46, pad = 4
    const vals = points.map(p => p.value)
    const lo = Math.min(...vals, 0), hi = Math.max(...vals, 1)
    const span = hi - lo || 1
    const x = i => pad + (i / Math.max(points.length - 1, 1)) * (W - pad * 2)
    const y = v => H - pad - ((v - lo) / span) * (H - pad * 2)
    return {
      path: points.map((p, i) => `${i ? 'L' : 'M'}${x(i).toFixed(1)},${y(p.value).toFixed(1)}`).join(' '),
      dots: points.map((p, i) => ({ cx: x(i), cy: y(p.value), passed: p.passed, run: p.run_id })),
      last: points[points.length - 1],
      delta: points.length > 1 ? points[points.length - 1].value - points[0].value : 0,
    }
  }, [points])

  return (
    <div className="trend-card">
      <div className="trend-head">
        <span className="trend-name">{name}</span>
        <span className={`trend-delta ${delta < 0 ? 'down' : delta > 0 ? 'up' : ''}`}>
          {delta > 0 ? '+' : ''}{delta.toFixed(2)}
        </span>
      </div>
      <svg viewBox="0 0 220 46" className="spark" role="img" aria-label={`${name} over ${points.length} runs`}>
        <path d={path} className="spark-line" />
        {dots.map((d, i) => (
          <circle key={i} cx={d.cx} cy={d.cy} r="2.6"
            className={d.passed === false ? 'spark-dot fail' : 'spark-dot'} />
        ))}
      </svg>
      <div className="trend-foot">
        latest <strong>{last.value.toFixed(2)}</strong> · {points.length} run{points.length === 1 ? '' : 's'}
      </div>
    </div>
  )
}

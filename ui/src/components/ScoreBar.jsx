import React from 'react'

/** Compact score chips: value, pass/fail against threshold, source. */
export default function ScoreBar({ scores, compact = false }) {
  if (!scores?.length) return null
  return (
    <div className={compact ? 'scores compact' : 'scores'}>
      {scores.map(s => {
        const state = s.passed === false ? 'fail' : s.passed === true ? 'pass' : 'none'
        return (
          <span key={s.name + (s.span_id || '')} className={`score score-${state}`} title={s.comment || undefined}>
            <span className="score-name">{s.name}</span>
            <span className="score-val">{Number(s.value).toFixed(2)}</span>
            {s.threshold != null && !compact && <span className="score-thr">/ {s.threshold}</span>}
            {!compact && s.source && <span className="score-src">{s.source}</span>}
          </span>
        )
      })}
    </div>
  )
}

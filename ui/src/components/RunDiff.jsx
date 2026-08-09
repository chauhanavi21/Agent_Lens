import React, { useEffect, useState } from 'react'
import { diffRuns } from '../api'
import StatusBadge from './StatusBadge'

function RunCard({ label, r }) {
  return (
    <div className="diff-card">
      <div className="diff-card-label">{label}</div>
      <div className="diff-card-name">{r.name} <StatusBadge status={r.status} /></div>
      <div className="diff-card-meta">
        <span>{r.duration_ms >= 1000 ? (r.duration_ms / 1000).toFixed(1) + 's' : Math.round(r.duration_ms || 0) + 'ms'}</span>
        <span>{(r.total_tokens || 0).toLocaleString()} tok</span>
        <span>${(r.total_cost_usd || 0).toFixed(4)}</span>
      </div>
      <code className="diff-card-id">{r.run_id}</code>
    </div>
  )
}

export default function RunDiff({ runA, runB }) {
  const [diff, setDiff] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    setDiff(null)
    diffRuns(runA, runB).then(setDiff).catch(e => setError(String(e.message || e)))
  }, [runA, runB])

  if (error) return <div className="empty">Diff failed: {error}</div>
  if (!diff) return <div className="empty">Comparing runs…</div>

  return (
    <div className="diff-wrap">
      <div className="diff-heads">
        <RunCard label="Run A" r={diff.run_a} />
        <div className="diff-vs">vs</div>
        <RunCard label="Run B" r={diff.run_b} />
      </div>

      <div className="diff-verdict">{diff.summary.verdict}</div>

      {diff.changed.length > 0 && (
        <section className="diff-section">
          <h3>Changed ({diff.changed.length})</h3>
          {diff.changed.map(c => (
            <div key={c.path} className="diff-item">
              <code className="diff-path">{c.path}</code>
              <div className="diff-changes">
                {c.changes.status && (
                  <span className="chip chip-status">
                    status <StatusBadge status={c.changes.status.a} /> → <StatusBadge status={c.changes.status.b} />
                  </span>
                )}
                {c.changes.duration_ms && (
                  <span className={`chip ${c.changes.duration_ms.pct > 0 ? 'chip-slower' : 'chip-faster'}`}>
                    {Math.round(c.changes.duration_ms.a)}ms → {Math.round(c.changes.duration_ms.b)}ms ({c.changes.duration_ms.pct > 0 ? '+' : ''}{c.changes.duration_ms.pct}%)
                  </span>
                )}
                {c.changes.tokens && <span className="chip">{c.changes.tokens.a} → {c.changes.tokens.b} tok</span>}
                {c.changes.cost_usd && <span className="chip">${(c.changes.cost_usd.a).toFixed(4)} → ${(c.changes.cost_usd.b).toFixed(4)}</span>}
                {c.changes.error && <span className="chip chip-error">error changed</span>}
              </div>
            </div>
          ))}
        </section>
      )}

      {diff.added.length > 0 && (
        <section className="diff-section">
          <h3>Only in Run B ({diff.added.length})</h3>
          {diff.added.map(x => (
            <div key={x.path} className="diff-item added">
              <code className="diff-path">+ {x.path}</code> <StatusBadge status={x.status} />
            </div>
          ))}
        </section>
      )}

      {diff.removed.length > 0 && (
        <section className="diff-section">
          <h3>Only in Run A ({diff.removed.length})</h3>
          {diff.removed.map(x => (
            <div key={x.path} className="diff-item removed">
              <code className="diff-path">− {x.path}</code> <StatusBadge status={x.status} />
            </div>
          ))}
        </section>
      )}
    </div>
  )
}

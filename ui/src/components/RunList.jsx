import React from 'react'
import ScoreBar from './ScoreBar'
import StatusBadge from './StatusBadge'

function ago(ts) {
  const s = Math.max(0, Date.now() / 1000 - ts)
  if (s < 60) return `${Math.round(s)}s ago`
  if (s < 3600) return `${Math.round(s / 60)}m ago`
  if (s < 86400) return `${Math.round(s / 3600)}h ago`
  return `${Math.round(s / 86400)}d ago`
}

export default function RunList({ runs, selectedId, onSelect, filters, onFilters, diffPair, onTogglePin }) {
  return (
    <aside className="sidebar">
      <div className="filters">
        <input
          placeholder="Filter by name"
          value={filters.name}
          onChange={e => onFilters({ ...filters, name: e.target.value })}
        />
        <select value={filters.status} onChange={e => onFilters({ ...filters, status: e.target.value })}>
          <option value="">All statuses</option>
          <option value="success">success</option>
          <option value="error">error</option>
          <option value="paused">paused</option>
          <option value="running">running</option>
        </select>
      </div>
      <ul className="run-list">
        {runs.map(r => (
          <li
            key={r.run_id}
            className={[
              'run-item',
              r.live ? 'live' : '',
              r.run_id === selectedId ? 'selected' : '',
              diffPair.includes(r.run_id) ? 'pinned' : '',
            ].join(' ')}
            onClick={() => onSelect(r.run_id)}
          >
            <div className="run-item-top">
              <span className="run-name">{r.name}</span>
              {r.live ? <span className="badge badge-running pulse">running</span> : <StatusBadge status={r.status} />}
            </div>
            <div className="run-item-meta">
              <span>{r.live ? 'now' : ago(r.started_at)}</span>
              <span>{r.span_count} spans</span>
              <span>{r.total_tokens.toLocaleString()} tok</span>
              <span>${r.total_cost_usd.toFixed(4)}</span>
            </div>
            <ScoreBar scores={r.scores} compact />
            <div className="run-item-bottom">
              {(r.tags || []).map(t => <span key={t} className="tag">{t}</span>)}
              <button
                className={diffPair.includes(r.run_id) ? 'pin pinned' : 'pin'}
                onClick={e => { e.stopPropagation(); onTogglePin(r.run_id) }}
                title="Pin for diff"
              >
                {diffPair.includes(r.run_id) ? '★' : '☆'}
              </button>
            </div>
          </li>
        ))}
        {!runs.length && <li className="run-empty">No runs match these filters.</li>}
      </ul>
    </aside>
  )
}

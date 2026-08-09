import React, { useCallback, useEffect, useState } from 'react'
import { DEMO, getRun, listRuns } from './api'
import DagView from './components/DagView'
import RunDiff from './components/RunDiff'
import RunList from './components/RunList'
import SpanDrawer from './components/SpanDrawer'

export default function App() {
  const [runs, setRuns] = useState([])
  const [filters, setFilters] = useState({ status: '', name: '' })
  const [selectedId, setSelectedId] = useState(null)
  const [run, setRun] = useState(null)
  const [span, setSpan] = useState(null)
  const [diffPair, setDiffPair] = useState([]) // [runIdA, runIdB]
  const [view, setView] = useState('dag')     // 'dag' | 'diff'
  const [error, setError] = useState(null)

  const refresh = useCallback(async () => {
    try {
      const rs = await listRuns(filters)
      setRuns(rs)
      setError(null)
      if (!selectedId && rs.length) setSelectedId(rs[0].run_id)
    } catch (e) {
      setError(String(e.message || e))
    }
  }, [filters, selectedId])

  useEffect(() => {
    refresh()
    const id = setInterval(refresh, 5000) // live polling
    return () => clearInterval(id)
  }, [refresh])

  useEffect(() => {
    if (!selectedId) return
    setSpan(null)
    getRun(selectedId).then(setRun).catch(e => setError(String(e.message || e)))
  }, [selectedId])

  const togglePin = runId => {
    setDiffPair(prev => {
      if (prev.includes(runId)) return prev.filter(id => id !== runId)
      return [...prev.slice(-1), runId]
    })
  }

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="brand-mark">◉</span> AgentLens
          <span className="brand-sub">agent observability runtime</span>
        </div>
        <nav className="tabs">
          <button className={view === 'dag' ? 'tab active' : 'tab'} onClick={() => setView('dag')}>Runs</button>
          <button
            className={view === 'diff' ? 'tab active' : 'tab'}
            disabled={diffPair.length !== 2}
            title={diffPair.length !== 2 ? 'Pin two runs to compare' : 'Compare pinned runs'}
            onClick={() => setView('diff')}
          >
            Diff {diffPair.length ? `(${diffPair.length}/2)` : ''}
          </button>
        </nav>
        {DEMO && <span className="demo-pill">demo data — set VITE_API_URL to connect a server</span>}
      </header>

      {error && <div className="error-bar">Can't reach the server: {error}. Retrying…</div>}

      <div className="layout">
        <RunList
          runs={runs}
          selectedId={selectedId}
          onSelect={id => { setSelectedId(id); setView('dag') }}
          filters={filters}
          onFilters={setFilters}
          diffPair={diffPair}
          onTogglePin={togglePin}
        />
        <main className="main">
          {view === 'dag' && run && (
            <DagView run={run} selectedSpan={span} onSelectSpan={setSpan} />
          )}
          {view === 'dag' && !run && (
            <div className="empty">Instrument an agent with <code>@lens.trace</code> and runs appear here.</div>
          )}
          {view === 'diff' && diffPair.length === 2 && (
            <RunDiff runA={diffPair[0]} runB={diffPair[1]} />
          )}
        </main>
        {view === 'dag' && span && <SpanDrawer span={span} onClose={() => setSpan(null)} />}
      </div>
    </div>
  )
}

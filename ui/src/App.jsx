import React, { useCallback, useEffect, useState } from 'react'
import { DEMO, getRun, listRuns } from './api'
import DagView from './components/DagView'
import RunDiff from './components/RunDiff'
import RunList from './components/RunList'
import TimelineView from './components/TimelineView'
import AlertsPanel from './components/AlertsPanel'
import ScoreTrend from './components/ScoreTrend'
import useLiveRuns from './useLiveRuns'
import SpanDrawer from './components/SpanDrawer'

export default function App() {
  const [runs, setRuns] = useState([])
  const [filters, setFilters] = useState({ status: '', name: '' })
  const [selectedId, setSelectedId] = useState(null)
  const [run, setRun] = useState(null)
  const [span, setSpan] = useState(null)
  const [diffPair, setDiffPair] = useState([]) // [runIdA, runIdB]
  const [view, setView] = useState('dag')     // 'dag' | 'diff' | 'alerts'
  const [runMode, setRunMode] = useState('dag') // 'dag' | 'timeline'
  const [error, setError] = useState(null)
  const { liveRuns, liveById, connected } = useLiveRuns()

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
    // With SSE connected, finished runs still need a periodic pull (the
    // stream carries spans, not the persisted rollups) — just far less often.
    const id = setInterval(refresh, connected ? 20000 : 5000)
    return () => clearInterval(id)
  }, [refresh, connected])

  useEffect(() => {
    if (!selectedId) return
    setSpan(null)
    getRun(selectedId).then(setRun).catch(e => setError(String(e.message || e)))
  }, [selectedId])

  // a selected run that's still executing is driven by the stream, so the
  // DAG grows in place instead of waiting for the next fetch
  const liveSelected = liveById[selectedId]
  const activeRun = liveSelected ? { ...liveSelected, live: true } : run

  // live runs sit above finished ones and replace their stored twin
  const liveIds = new Set(liveRuns.map(r => r.run_id))
  const mergedRuns = [
    ...liveRuns.map(r => ({
      ...r, status: 'running', span_count: r.spans?.length || 0,
      total_tokens: 0, total_cost_usd: 0, scores: [], live: true,
    })),
    ...runs.filter(r => !liveIds.has(r.run_id)),
  ]

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
        {connected && (
          <span className="live-pill" title="Streaming spans over SSE">
            <i /> live
          </span>
        )}
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
          <button className={view === 'quality' ? 'tab active' : 'tab'} onClick={() => setView('quality')}>Quality</button>
          <button className={view === 'alerts' ? 'tab active' : 'tab'} onClick={() => setView('alerts')}>Alerts</button>
        </nav>
        {DEMO && <span className="demo-pill">demo data — set VITE_API_URL to connect a server</span>}
      </header>

      {error && <div className="error-bar">Can't reach the server: {error}. Retrying…</div>}

      <div className="layout">
        <RunList
          runs={mergedRuns}
          selectedId={selectedId}
          onSelect={id => { setSelectedId(id); setView('dag') }}
          filters={filters}
          onFilters={setFilters}
          diffPair={diffPair}
          onTogglePin={togglePin}
        />
        <main className="main">
          {view === 'dag' && activeRun && (
            <>
              <div className="mode-switch">
                <button className={runMode === 'dag' ? 'seg active' : 'seg'} onClick={() => setRunMode('dag')}>Graph</button>
                <button className={runMode === 'timeline' ? 'seg active' : 'seg'} onClick={() => setRunMode('timeline')}>Timeline</button>
              </div>
              {runMode === 'dag'
                ? <DagView run={activeRun} selectedSpan={span} onSelectSpan={setSpan} />
                : <TimelineView run={activeRun} selectedSpan={span} onSelectSpan={setSpan} />}
            </>
          )}
          {view === 'dag' && !activeRun && (
            <div className="empty">Instrument an agent with <code>@lens.trace</code> and runs appear here.</div>
          )}
          {view === 'diff' && diffPair.length === 2 && (
            <RunDiff runA={diffPair[0]} runB={diffPair[1]} />
          )}
          {view === 'quality' && (
            <div className="quality">
              <h3>Eval scores over time</h3>
              <ScoreTrend agentName={activeRun?.name} />
            </div>
          )}
          {view === 'alerts' && <AlertsPanel />}
        </main>
        {view === 'dag' && span && <SpanDrawer span={span} onClose={() => setSpan(null)} />}
      </div>
    </div>
  )
}

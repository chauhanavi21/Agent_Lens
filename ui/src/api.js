// API client. If VITE_API_URL is empty, the UI runs in demo mode with
// bundled sample runs so the DAG and diff views work without a server.
import { demoRuns } from './demoData'

const BASE = import.meta.env.VITE_API_URL || ''
export const DEMO = !BASE

async function get(path) {
  const res = await fetch(`${BASE}${path}`)
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

export async function listRuns({ status, name } = {}) {
  if (DEMO) {
    return demoRuns
      .filter(r => (!status || r.status === status) && (!name || r.name.includes(name)))
      .map(r => ({ ...r, span_count: r.spans.length }))
  }
  const q = new URLSearchParams()
  if (status) q.set('status', status)
  if (name) q.set('name', name)
  return get(`/api/runs?${q}`)
}

export async function getRun(runId) {
  if (DEMO) return demoRuns.find(r => r.run_id === runId)
  return get(`/api/runs/${runId}`)
}

export async function diffRuns(runA, runB) {
  if (DEMO) {
    const res = await import('./demoDiff')
    return res.demoDiff(await getRun(runA), await getRun(runB))
  }
  const res = await fetch(`${BASE}/api/runs/diff`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ run_a: runA, run_b: runB }),
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.json()
}

// --- alerts -------------------------------------------------------------- //

async function send(path, method, body) {
  const res = await fetch(`${BASE}${path}`, {
    method,
    headers: body ? { 'Content-Type': 'application/json' } : {},
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${await res.text()}`)
  return res.status === 204 ? null : res.json()
}

export async function listRules() {
  if (DEMO) return demoState.rules
  return get('/api/alerts/rules')
}

export async function createRule(rule) {
  if (DEMO) {
    const r = { ...rule, id: Math.random().toString(16).slice(2, 10), created_at: Date.now() / 1000, enabled: true }
    demoState.rules = [r, ...demoState.rules]
    return r
  }
  return send('/api/alerts/rules', 'POST', rule)
}

export async function deleteRule(id) {
  if (DEMO) {
    demoState.rules = demoState.rules.filter(r => r.id !== id)
    return null
  }
  return send(`/api/alerts/rules/${id}`, 'DELETE')
}

export async function testRule(id) {
  if (DEMO) return { delivered: false, error: 'demo mode does not send real webhooks' }
  return send(`/api/alerts/rules/${id}/test`, 'POST')
}

export async function listEvents() {
  if (DEMO) return demoState.events
  return get('/api/alerts/events')
}

// in-memory store so the alerts UI is explorable without a server
const demoState = {
  rules: [
    { id: 'demo1', name: 'Any failed run', field: 'status', op: 'eq', value: 'error', run_name: '', webhook_url: 'https://hooks.slack.com/services/DEMO', enabled: true, created_at: 0 },
    { id: 'demo2', name: 'Runs over $0.05', field: 'total_cost_usd', op: 'gt', value: '0.05', run_name: '', webhook_url: 'https://hooks.slack.com/services/DEMO', enabled: true, created_at: 0 },
  ],
  events: [
    { id: 'e1', rule_id: 'demo1', rule_name: 'Any failed run', run_id: 'demo-run-b', run_name: 'research_agent', reason: 'status is error (eq error)', delivered: true, fired_at: 0 },
  ],
}

// --- evals --------------------------------------------------------------- //

export async function scoreTrends(agentName) {
  if (DEMO) {
    const series = {}
    for (const r of [...demoRuns].sort((a, b) => a.started_at - b.started_at)) {
      for (const s of r.scores || []) {
        (series[s.name] ||= []).push({ run_id: r.run_id, started_at: r.started_at, value: s.value, passed: s.passed })
      }
    }
    const metrics = Object.keys(series).sort()
    return { metrics, series, latest: Object.fromEntries(metrics.map(m => [m, series[m].at(-1).value])) }
  }
  const q = agentName ? `?name=${encodeURIComponent(agentName)}` : ''
  return get(`/api/runs/scores${q}`)
}

// --- live streaming ------------------------------------------------------ //

export function streamUrl(runId) {
  const q = runId ? `?run_id=${encodeURIComponent(runId)}` : ''
  return `${BASE}/api/stream${q}`
}

export async function listLiveRuns() {
  if (DEMO) return { runs: [], subscribers: 0 }
  return get('/api/live/runs')
}

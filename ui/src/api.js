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

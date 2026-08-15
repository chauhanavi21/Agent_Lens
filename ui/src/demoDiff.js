// Client-side mirror of the server's diff logic, used only in demo mode.
function paths(spans) {
  const byId = Object.fromEntries(spans.map(s => [s.span_id, s]))
  const pathOf = s => {
    const parts = []
    let cur = s
    while (cur) { parts.push(cur.name); cur = cur.parent_id ? byId[cur.parent_id] : null }
    return parts.reverse().join('.')
  }
  const seen = {}, keyed = {}
  for (const s of [...spans].sort((a, b) => a.started_at - b.started_at)) {
    const base = pathOf(s)
    const i = seen[base] || 0
    seen[base] = i + 1
    keyed[`${base}#${i}`] = s
  }
  return keyed
}

export function demoDiff(a, b) {
  const pa = paths(a.spans), pb = paths(b.spans)
  const ka = new Set(Object.keys(pa)), kb = new Set(Object.keys(pb))
  const removed = [...ka].filter(k => !kb.has(k)).sort()
  const added = [...kb].filter(k => !ka.has(k)).sort()
  const changed = []
  for (const k of [...ka].filter(k => kb.has(k)).sort()) {
    const sa = pa[k], sb = pb[k], ch = {}
    if (sa.status !== sb.status) ch.status = { a: sa.status, b: sb.status }
    const da = sa.duration_ms || 0, db = sb.duration_ms || 0
    if (da && db && Math.abs((db - da) / da) >= 0.2) ch.duration_ms = { a: da, b: db, pct: +(((db - da) / da) * 100).toFixed(1) }
    const ta = sa.llm?.total_tokens || 0, tb = sb.llm?.total_tokens || 0
    if (ta !== tb) ch.tokens = { a: ta, b: tb }
    if (Object.keys(ch).length) changed.push({ path: k, changes: ch })
  }
  const sa = Object.fromEntries((a.scores || []).map(s => [s.name, s]))
  const sb = Object.fromEntries((b.scores || []).map(s => [s.name, s]))
  const scores = [...new Set([...Object.keys(sa), ...Object.keys(sb)])].sort().map(name => ({
    name,
    a: sa[name]?.value ?? null,
    b: sb[name]?.value ?? null,
    delta: sa[name] && sb[name] ? +(sb[name].value - sa[name].value).toFixed(4) : null,
    passed_a: sa[name]?.passed ?? null,
    passed_b: sb[name]?.passed ?? null,
  })).filter(s => s.delta === null || s.delta !== 0)

  const flips = changed.filter(c => c.changes.status)
  const parts = []
  const regressions = scores.filter(s => s.delta != null && s.delta < 0)
  if (regressions.length) {
    const worst = regressions.reduce((m, s) => (s.delta < m.delta ? s : m))
    parts.push(`Quality dropped: ${worst.name} ${worst.a} → ${worst.b}.`)
  }
  if (a.status !== b.status && flips.length) {
    const deepest = flips.reduce((m, c) => (c.path.split('.').length > m.path.split('.').length ? c : m))
    parts.push(`Status diverged first at '${deepest.path}'.`)
  } else if (changed.length) parts.push(`${changed.length} span(s) changed behavior between runs.`)
  const verdict = parts.length ? parts.join(' ') : 'Runs are structurally and behaviorally equivalent.'
  const head = r => ({ run_id: r.run_id, name: r.name, status: r.status, duration_ms: r.duration_ms, total_tokens: r.total_tokens, total_cost_usd: r.total_cost_usd })
  return {
    run_a: head(a), run_b: head(b),
    added: added.map(k => ({ path: k, status: pb[k].status })),
    removed: removed.map(k => ({ path: k, status: pa[k].status })),
    changed, scores,
    summary: { added: added.length, removed: removed.length, changed: changed.length, verdict },
  }
}

import * as d3 from 'd3'
import React, { useEffect, useRef } from 'react'
import ScoreBar from './ScoreBar'

const KIND_COLOR = {
  agent: 'var(--kind-agent)',
  llm: 'var(--kind-llm)',
  tool: 'var(--kind-tool)',
  retrieval: 'var(--kind-retrieval)',
  mcp: 'var(--kind-mcp)',
  chain: 'var(--kind-chain)',
  custom: 'var(--kind-custom)',
}

export default function DagView({ run, selectedSpan, onSelectSpan }) {
  const ref = useRef(null)

  useEffect(() => {
    const el = ref.current
    if (!el || !run) return
    const spans = run.spans
    const width = el.clientWidth || 900

    // Build hierarchy; orphan/second roots attach to the first root.
    const ids = new Set(spans.map(s => s.span_id))
    const rootId = spans.find(s => !s.parent_id || !ids.has(s.parent_id))?.span_id
    const strat = d3.stratify()
      .id(d => d.span_id)
      .parentId(d => (d.span_id === rootId ? null : (ids.has(d.parent_id) ? d.parent_id : rootId)))
    let root
    try { root = strat(spans) } catch { return }

    const nodeW = 168, nodeH = 62
    const tree = d3.tree().nodeSize([nodeW + 26, nodeH + 56])
    tree(root)
    const xs = root.descendants().map(d => d.x)
    const minX = Math.min(...xs), maxX = Math.max(...xs)
    const height = (root.height + 1) * (nodeH + 56) + 40

    const svg = d3.select(el).selectAll('svg').data([null]).join('svg')
      .attr('width', width).attr('height', height)
      .attr('viewBox', [minX - nodeW, -30, Math.max(width, maxX - minX + nodeW * 2), height])

    svg.selectAll('*').remove()
    const g = svg.append('g')

    // retry edges (dashed, span -> its retry) drawn beneath tree links
    const pos = Object.fromEntries(root.descendants().map(d => [d.id, d]))
    g.append('g').selectAll('path.retry')
      .data(spans.filter(s => s.retry_of && pos[s.retry_of] && pos[s.span_id]))
      .join('path')
      .attr('class', 'edge-retry')
      .attr('d', s => {
        const a = pos[s.retry_of], b = pos[s.span_id]
        return `M${a.x + nodeW / 2},${a.y + nodeH / 2} C${(a.x + b.x) / 2 + nodeW},${a.y} ${(a.x + b.x) / 2 + nodeW},${b.y} ${b.x + nodeW / 2},${b.y + nodeH / 2}`
      })

    g.append('g').selectAll('path.link')
      .data(root.links())
      .join('path')
      .attr('class', 'edge')
      .attr('d', d3.linkVertical().x(d => d.x).y(d => d.y + nodeH / 2))

    const node = g.append('g').selectAll('g.node')
      .data(root.descendants())
      .join('g')
      .attr('class', 'node')
      .attr('transform', d => `translate(${d.x - nodeW / 2},${d.y})`)
      .style('cursor', 'pointer')
      .on('click', (_, d) => onSelectSpan(d.data))

    node.append('rect')
      .attr('width', nodeW).attr('height', nodeH).attr('rx', 8)
      .attr('class', d => `node-box status-${d.data.status}` +
        (d.data.ended_at ? '' : ' node-running') +
        (selectedSpan && selectedSpan.span_id === d.data.span_id ? ' node-selected' : ''))
      .attr('stroke', d => KIND_COLOR[d.data.kind] || KIND_COLOR.custom)

    node.append('rect')
      .attr('x', 0).attr('y', 0).attr('width', 4).attr('height', nodeH).attr('rx', 2)
      .attr('fill', d => KIND_COLOR[d.data.kind] || KIND_COLOR.custom)

    node.append('text')
      .attr('x', 12).attr('y', 20).attr('class', 'node-name')
      .text(d => d.data.name.length > 18 ? d.data.name.slice(0, 17) + '…' : d.data.name)

    node.append('text')
      .attr('x', 12).attr('y', 38).attr('class', 'node-sub')
      .text(d => {
        const bits = [d.data.kind]
        if (d.data.duration_ms != null) bits.push(`${d.data.duration_ms >= 1000 ? (d.data.duration_ms / 1000).toFixed(1) + 's' : Math.round(d.data.duration_ms) + 'ms'}`)
        if (d.data.llm?.total_tokens) bits.push(`${d.data.llm.total_tokens} tok`)
        return bits.join(' · ')
      })

    node.filter(d => d.data.status === 'error')
      .append('text').attr('x', nodeW - 14).attr('y', 20).attr('class', 'node-err').text('✕')
    // a span recorded by another process — mark where the boundary is
    node.filter(d => d.data.service)
      .append('text').attr('x', 12).attr('y', nodeH - 4).attr('class', 'node-svc')
      .text(d => `⇄ ${d.data.service}`)

    node.filter(d => d.data.retry_of)
      .append('text').attr('x', nodeW - 14).attr('y', 38).attr('class', 'node-retry').text('↻')
  }, [run, selectedSpan, onSelectSpan])

  return (
    <div className="dag-wrap">
      <div className="dag-header">
        <h2>{run.name}{run.live && <span className="live-tag">live</span>}</h2>
        <div className="dag-stats">
          <span>{run.spans.length} spans</span>
          <span>{(run.total_tokens || 0).toLocaleString()} tokens</span>
          <span>${(run.total_cost_usd || 0).toFixed(4)}</span>
          <span>{run.duration_ms >= 1000 ? (run.duration_ms / 1000).toFixed(1) + 's' : Math.round(run.duration_ms || 0) + 'ms'}</span>
        </div>
      </div>
      <ScoreBar scores={run.scores} />
      {run.error && <div className="run-error">{run.error}</div>}
      <div className="dag-canvas" ref={ref} />
      <div className="legend">
        {Object.entries(KIND_COLOR).map(([k, c]) => (
          <span key={k} className="legend-item"><i style={{ background: c }} />{k}</span>
        ))}
        <span className="legend-item"><i className="legend-retry" />retry</span>
      </div>
    </div>
  )
}

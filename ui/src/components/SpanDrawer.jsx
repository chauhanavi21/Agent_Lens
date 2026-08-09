import React from 'react'
import StatusBadge from './StatusBadge'

function Row({ label, children }) {
  if (children == null || children === '') return null
  return (
    <div className="drawer-row">
      <div className="drawer-label">{label}</div>
      <div className="drawer-value">{children}</div>
    </div>
  )
}

export default function SpanDrawer({ span, onClose }) {
  const llm = span.llm
  return (
    <aside className="drawer">
      <div className="drawer-head">
        <div>
          <h3>{span.name}</h3>
          <div className="drawer-kind">{span.kind} · <StatusBadge status={span.status} /></div>
        </div>
        <button className="drawer-close" onClick={onClose} aria-label="Close span details">✕</button>
      </div>

      <Row label="Duration">{span.duration_ms != null ? `${span.duration_ms} ms` : 'running'}</Row>
      <Row label="Span ID"><code>{span.span_id}</code></Row>
      {span.retry_of && <Row label="Retry of"><code>{span.retry_of}</code></Row>}
      <Row label="Inputs"><pre>{span.inputs}</pre></Row>
      <Row label="Outputs"><pre>{span.outputs}</pre></Row>
      {span.error && <Row label="Error"><pre className="pre-error">{span.error}</pre></Row>}

      {llm && (
        <div className="drawer-llm">
          <h4>LLM call</h4>
          <Row label="Model">{llm.model || '—'}</Row>
          <Row label="Tokens">{llm.input_tokens} in / {llm.output_tokens} out ({llm.total_tokens} total)</Row>
          <Row label="Cost">${(llm.cost_usd || 0).toFixed(6)}</Row>
          <Row label="Prompt"><pre>{llm.prompt_preview}</pre></Row>
          <Row label="Response"><pre>{llm.response_preview}</pre></Row>
        </div>
      )}
    </aside>
  )
}

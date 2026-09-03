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
      {span.service && <Row label="Recorded by">{span.service}</Row>}
      {span.remote_parent_id && (
        <Row label="Called from"><code>{span.remote_parent_id}</code> (another process)</Row>
      )}
      {Object.keys(span.attributes || {}).length > 0 && (
        <Row label="Attributes">
          <pre>{Object.entries(span.attributes).map(([k, v]) => `${k} = ${v}`).join('\n')}</pre>
        </Row>
      )}
      <Row label="Inputs"><pre>{span.inputs}</pre></Row>
      <Row label="Outputs"><pre>{span.outputs}</pre></Row>
      {span.error && <Row label="Error"><pre className="pre-error">{span.error}</pre></Row>}

      {llm && (
        <div className="drawer-llm">
          <h4>LLM call</h4>
          <Row label="Model">{llm.model || '—'}</Row>
          <Row label="Tokens">{llm.input_tokens} in / {llm.output_tokens} out ({llm.total_tokens} total)</Row>
          <Row label="Cost">
            {llm.cost_source === 'unpriced' && llm.total_tokens ? (
              <span className="cost-unpriced">
                no price configured for {llm.model || 'this model'}
              </span>
            ) : (
              <>
                ${(llm.cost_usd || 0).toFixed(6)}
                {llm.cost_source === 'reported' && <span className="cost-note"> reported by provider</span>}
              </>
            )}
          </Row>
          <Row label="Prompt"><pre>{llm.prompt_preview}</pre></Row>
          <Row label="Response"><pre>{llm.response_preview}</pre></Row>
        </div>
      )}
    </aside>
  )
}

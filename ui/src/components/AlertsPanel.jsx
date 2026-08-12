import React, { useEffect, useState } from 'react'
import { createRule, deleteRule, listEvents, listRules, testRule } from '../api'

const FIELDS = ['status', 'total_cost_usd', 'total_tokens', 'duration_ms', 'span_count', 'error_span_count', 'retry_count', 'name']
const OPS = ['gt', 'gte', 'lt', 'lte', 'eq', 'neq', 'contains']

const blank = { name: '', field: 'total_cost_usd', op: 'gt', value: '0.50', webhook_url: '', run_name: '' }

export default function AlertsPanel() {
  const [rules, setRules] = useState([])
  const [events, setEvents] = useState([])
  const [draft, setDraft] = useState(blank)
  const [notice, setNotice] = useState(null)

  const load = () => {
    listRules().then(setRules).catch(e => setNotice(String(e.message || e)))
    listEvents().then(setEvents).catch(() => {})
  }
  useEffect(load, [])

  const save = async () => {
    if (!draft.name.trim()) return setNotice('Give the rule a name so you can recognize it later.')
    if (!draft.webhook_url.trim()) return setNotice('Add a webhook URL to send alerts to.')
    try {
      await createRule(draft)
      setDraft(blank)
      setNotice(null)
      load()
    } catch (e) {
      setNotice(String(e.message || e))
    }
  }

  const fire = async id => {
    const res = await testRule(id)
    setNotice(res.delivered ? 'Test alert delivered.' : `Delivery failed: ${res.error}`)
    load()
  }

  return (
    <div className="alerts">
      <section className="alert-form">
        <h3>New alert rule</h3>
        <div className="form-grid">
          <label>Name<input value={draft.name} placeholder="Runs over budget" onChange={e => setDraft({ ...draft, name: e.target.value })} /></label>
          <label>Only for agent<input value={draft.run_name} placeholder="any agent" onChange={e => setDraft({ ...draft, run_name: e.target.value })} /></label>
          <label>When<select value={draft.field} onChange={e => setDraft({ ...draft, field: e.target.value })}>{FIELDS.map(f => <option key={f}>{f}</option>)}</select></label>
          <label>Is<select value={draft.op} onChange={e => setDraft({ ...draft, op: e.target.value })}>{OPS.map(o => <option key={o}>{o}</option>)}</select></label>
          <label>Value<input value={draft.value} onChange={e => setDraft({ ...draft, value: e.target.value })} /></label>
          <label className="wide">Webhook URL<input value={draft.webhook_url} placeholder="https://hooks.slack.com/services/…" onChange={e => setDraft({ ...draft, webhook_url: e.target.value })} /></label>
        </div>
        <button className="btn" onClick={save}>Create rule</button>
        {notice && <div className="notice">{notice}</div>}
      </section>

      <section className="alert-list">
        <h3>Rules ({rules.length})</h3>
        {!rules.length && <div className="empty-inline">No rules yet. Create one above to get notified when a run fails or runs over budget.</div>}
        {rules.map(r => (
          <div key={r.id} className="alert-rule">
            <div>
              <strong>{r.name}</strong>
              <code className="rule-expr">{r.run_name ? `${r.run_name}: ` : ''}{r.field} {r.op} {r.value}</code>
              <div className="rule-hook">{r.webhook_url}</div>
            </div>
            <div className="rule-actions">
              <button className="btn ghost" onClick={() => fire(r.id)}>Send test</button>
              <button className="btn ghost danger" onClick={() => deleteRule(r.id).then(load)}>Delete</button>
            </div>
          </div>
        ))}
      </section>

      <section className="alert-list">
        <h3>Recent alerts ({events.length})</h3>
        {!events.length && <div className="empty-inline">Nothing has tripped a rule yet.</div>}
        {events.map(e => (
          <div key={e.id} className="alert-event">
            <span className={e.delivered ? 'dot ok' : 'dot err'} />
            <strong>{e.rule_name}</strong>
            <span className="ev-run">{e.run_name}</span>
            <span className="ev-reason">{e.reason}</span>
            {!e.delivered && <span className="ev-fail">delivery failed</span>}
          </div>
        ))}
      </section>
    </div>
  )
}

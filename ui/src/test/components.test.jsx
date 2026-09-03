import { render, screen, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { describe, expect, test, vi } from 'vitest'

import DagView from '../components/DagView'
import RunList from '../components/RunList'
import ScoreBar from '../components/ScoreBar'
import SpanDrawer from '../components/SpanDrawer'
import StatusBadge from '../components/StatusBadge'
import TimelineView from '../components/TimelineView'

const span = (o) => ({
  parent_id: null,
  status: 'success',
  inputs: '',
  outputs: '',
  error: null,
  retry_of: null,
  remote_parent_id: null,
  service: null,
  llm: null,
  attributes: {},
  duration_ms: 100,
  ended_at: o.started_at + 0.1,
  ...o,
})

const RUN = {
  run_id: 'r1',
  name: 'research_agent',
  status: 'error',
  tags: ['prod'],
  started_at: 1000,
  duration_ms: 4200,
  total_tokens: 2840,
  total_cost_usd: 0.0161,
  error: 'TimeoutError: model timeout',
  scores: [
    { name: 'faithfulness', value: 0.62, threshold: 0.85, passed: false, source: 'ragas', span_id: null },
    { name: 'relevancy', value: 0.91, threshold: 0.8, passed: true, source: 'ragas', span_id: null },
  ],
  spans: [
    span({ span_id: 'a1', name: 'research_agent', kind: 'agent', started_at: 1000, duration_ms: 4200 }),
    span({ span_id: 'a2', parent_id: 'a1', name: 'web_search', kind: 'tool', started_at: 1000.1, duration_ms: 600 }),
    span({ span_id: 'a3', parent_id: 'a1', name: 'summarize', kind: 'llm', started_at: 1001, duration_ms: 2000, status: 'error', error: 'TimeoutError' }),
    span({ span_id: 'a4', parent_id: 'a1', name: 'summarize', kind: 'llm', started_at: 1003, duration_ms: 1000, retry_of: 'a3' }),
  ],
}

describe('StatusBadge', () => {
  test('renders the status with a matching class', () => {
    const { container } = render(<StatusBadge status="error" />)
    expect(screen.getByText('error')).toBeInTheDocument()
    expect(container.querySelector('.badge-error')).toBeTruthy()
  })
})

describe('ScoreBar', () => {
  test('marks scores as pass or fail against their thresholds', () => {
    const { container } = render(<ScoreBar scores={RUN.scores} />)
    expect(screen.getByText('faithfulness')).toBeInTheDocument()
    expect(screen.getByText('0.62')).toBeInTheDocument()
    expect(container.querySelectorAll('.score-fail')).toHaveLength(1)
    expect(container.querySelectorAll('.score-pass')).toHaveLength(1)
  })

  test('renders nothing when there are no scores', () => {
    const { container } = render(<ScoreBar scores={[]} />)
    expect(container).toBeEmptyDOMElement()
    expect(render(<ScoreBar />).container).toBeEmptyDOMElement()
  })

  test('compact mode hides the threshold and source', () => {
    const { container } = render(<ScoreBar scores={RUN.scores} compact />)
    expect(container.querySelector('.scores.compact')).toBeTruthy()
    expect(container.querySelector('.score-thr')).toBeNull()
  })
})

describe('RunList', () => {
  const runs = [
    { run_id: 'r1', name: 'research_agent', status: 'success', tags: ['prod'], started_at: Date.now() / 1000 - 120, span_count: 5, total_tokens: 2840, total_cost_usd: 0.0161, scores: [] },
    { run_id: 'r2', name: 'support_agent', status: 'error', tags: [], started_at: Date.now() / 1000 - 7200, span_count: 3, total_tokens: 500, total_cost_usd: 0.002, scores: [] },
  ]

  const setup = (overrides = {}) => {
    const props = {
      runs,
      selectedId: 'r1',
      onSelect: vi.fn(),
      filters: { name: '', status: '' },
      onFilters: vi.fn(),
      diffPair: [],
      onTogglePin: vi.fn(),
      ...overrides,
    }
    return { ...render(<RunList {...props} />), props }
  }

  test('lists runs with their rollups', () => {
    setup()
    expect(screen.getByText('research_agent')).toBeInTheDocument()
    expect(screen.getByText('2,840 tok')).toBeInTheDocument()
    expect(screen.getByText('$0.0161')).toBeInTheDocument()
  })

  test('formats relative times', () => {
    setup()
    expect(screen.getByText('2m ago')).toBeInTheDocument()
    expect(screen.getByText('2h ago')).toBeInTheDocument()
  })

  test('selecting a run calls back with its id', async () => {
    const { props } = setup()
    await userEvent.click(screen.getByText('support_agent'))
    expect(props.onSelect).toHaveBeenCalledWith('r2')
  })

  test('pinning does not also select the run', async () => {
    // the pin sits inside the clickable row, so its handler must stop
    // propagation or pinning would navigate away from what you're comparing
    const { props } = setup()
    const row = screen.getByText('support_agent').closest('.run-item')
    await userEvent.click(within(row).getByTitle('Pin for diff'))
    expect(props.onTogglePin).toHaveBeenCalledWith('r2')
    expect(props.onSelect).not.toHaveBeenCalled()
  })

  test('a live run shows as running with its own styling', () => {
    const { container } = setup({
      runs: [{ ...runs[0], live: true, status: 'running' }],
    })
    const row = container.querySelector('.run-item.live')
    expect(row).toBeTruthy()
    // scoped to the row: 'running' also appears in the status filter dropdown
    expect(within(row).getByText('running')).toBeInTheDocument()
    expect(within(row).getByText('now')).toBeInTheDocument()
  })

  test('an empty list explains itself', () => {
    setup({ runs: [] })
    expect(screen.getByText(/No runs match/)).toBeInTheDocument()
  })
})

describe('SpanDrawer', () => {
  test('shows span detail including the error', () => {
    render(<SpanDrawer span={RUN.spans[2]} onClose={vi.fn()} />)
    expect(screen.getByRole('heading', { name: 'summarize' })).toBeInTheDocument()
    expect(screen.getByText('2000 ms')).toBeInTheDocument()
    expect(screen.getByText('TimeoutError')).toBeInTheDocument()
  })

  test('shows retry lineage', () => {
    render(<SpanDrawer span={RUN.spans[3]} onClose={vi.fn()} />)
    expect(screen.getByText('Retry of')).toBeInTheDocument()
    expect(screen.getByText('a3')).toBeInTheDocument()
  })

  test('shows LLM metadata when present', () => {
    const llmSpan = {
      ...RUN.spans[3],
      llm: {
        model: 'gpt-4o', provider: 'openai', input_tokens: 1000,
        output_tokens: 500, total_tokens: 1500, cost_usd: 0.0075,
        prompt_preview: 'summarize this', response_preview: 'a summary',
      },
    }
    render(<SpanDrawer span={llmSpan} onClose={vi.fn()} />)
    expect(screen.getByText('gpt-4o')).toBeInTheDocument()
    expect(screen.getByText(/1000 in \/ 500 out/)).toBeInTheDocument()
    expect(screen.getByText('$0.007500')).toBeInTheDocument()
  })

  test('shows cross-process attribution for a stitched span', () => {
    const remote = { ...RUN.spans[1], service: 'github', remote_parent_id: 'a2', attributes: { 'mcp.server.name': 'github' } }
    render(<SpanDrawer span={remote} onClose={vi.fn()} />)
    expect(screen.getByText('Recorded by')).toBeInTheDocument()
    expect(screen.getByText('github')).toBeInTheDocument()
    expect(screen.getByText(/another process/)).toBeInTheDocument()
  })

  test('close button fires', async () => {
    const onClose = vi.fn()
    render(<SpanDrawer span={RUN.spans[0]} onClose={onClose} />)
    await userEvent.click(screen.getByLabelText('Close span details'))
    expect(onClose).toHaveBeenCalled()
  })
})

describe('cost provenance', () => {
  const withLlm = (llm) => ({
    ...RUN,
    total_cost_usd: llm.cost_usd,
    spans: [RUN.spans[0], { ...RUN.spans[2], llm }],
  })

  test('an unpriced model is flagged rather than shown as free', () => {
    const run = withLlm({
      model: 'mystery-v9', provider: '', input_tokens: 8000, output_tokens: 2000,
      total_tokens: 10000, cost_usd: 0, cost_source: 'unpriced',
      prompt_preview: '', response_preview: '',
    })
    const { container } = render(<DagView run={run} selectedSpan={null} onSelectSpan={vi.fn()} />)
    // the total must not read as complete when it excludes real spend
    expect(container.querySelector('.cost-gap')).toBeTruthy()
  })

  test('a fully priced run shows no gap marker', () => {
    const run = withLlm({
      model: 'gpt-4o', provider: 'openai', input_tokens: 1000, output_tokens: 500,
      total_tokens: 1500, cost_usd: 0.0075, cost_source: 'table',
      prompt_preview: '', response_preview: '',
    })
    const { container } = render(<DagView run={run} selectedSpan={null} onSelectSpan={vi.fn()} />)
    expect(container.querySelector('.cost-gap')).toBeNull()
  })

  test('the drawer explains an unpriced cost instead of printing $0.000000', () => {
    render(<SpanDrawer span={{ ...RUN.spans[2], llm: {
      model: 'mystery-v9', provider: '', input_tokens: 8000, output_tokens: 2000,
      total_tokens: 10000, cost_usd: 0, cost_source: 'unpriced',
      prompt_preview: '', response_preview: '',
    } }} onClose={vi.fn()} />)
    expect(screen.getByText(/no price configured for mystery-v9/)).toBeInTheDocument()
    expect(screen.queryByText('$0.000000')).toBeNull()
  })

  test('a provider-reported cost is labelled as such', () => {
    render(<SpanDrawer span={{ ...RUN.spans[2], llm: {
      model: 'gateway', provider: '', input_tokens: 10, output_tokens: 5,
      total_tokens: 15, cost_usd: 0.0031, cost_source: 'reported',
      prompt_preview: '', response_preview: '',
    } }} onClose={vi.fn()} />)
    expect(screen.getByText(/reported by provider/)).toBeInTheDocument()
  })
})

describe('TimelineView', () => {
  test('renders one row per span, ordered by start time', () => {
    const { container } = render(<TimelineView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    const rows = container.querySelectorAll('.tl-row')
    expect(rows).toHaveLength(4)
    expect(rows[0].textContent).toMatch(/research_agent/)
    expect(rows[1].textContent).toMatch(/web_search/)
  })

  test('outlines the slowest leaf as the critical path', () => {
    const { container } = render(<TimelineView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    const critical = container.querySelectorAll('.tl-bar.critical')
    expect(critical).toHaveLength(1)
  })

  test('flags retries and errors inline', () => {
    const { container } = render(<TimelineView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    expect(container.querySelectorAll('.tl-flag.retry')).toHaveLength(1)
    expect(container.querySelectorAll('.tl-flag.err')).toHaveLength(1)
  })

  test('clicking a bar selects that span', async () => {
    const onSelectSpan = vi.fn()
    const { container } = render(<TimelineView run={RUN} selectedSpan={null} onSelectSpan={onSelectSpan} />)
    await userEvent.click(container.querySelectorAll('.tl-row')[1])
    expect(onSelectSpan).toHaveBeenCalledWith(expect.objectContaining({ name: 'web_search' }))
  })

  test('survives a run whose spans have not finished', () => {
    const running = { ...RUN, spans: [{ ...RUN.spans[0], ended_at: null, duration_ms: null }] }
    expect(() => render(<TimelineView run={running} selectedSpan={null} onSelectSpan={vi.fn()} />)).not.toThrow()
  })
})

describe('DagView', () => {
  test('draws a node per span with D3', () => {
    const { container } = render(<DagView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    expect(container.querySelectorAll('svg g.node')).toHaveLength(4)
    expect(container.querySelector('svg')).toBeTruthy()
  })

  test('links each child to its parent, and draws retry edges separately', () => {
    const { container } = render(<DagView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    // three children under one root
    expect(container.querySelectorAll('path.edge')).toHaveLength(3)
    // the retry attempt gets a dashed edge back to the attempt it replaces
    expect(container.querySelectorAll('path.edge-retry')).toHaveLength(1)
  })

  test('shows run rollups and scores in the header', () => {
    render(<DagView run={RUN} selectedSpan={null} onSelectSpan={vi.fn()} />)
    expect(screen.getByText('4 spans')).toBeInTheDocument()
    expect(screen.getByText('2,840 tokens')).toBeInTheDocument()
    expect(screen.getByText('faithfulness')).toBeInTheDocument()
    expect(screen.getByText(/TimeoutError/)).toBeInTheDocument()
  })

  test('marks a live run and its unfinished spans', () => {
    const live = {
      ...RUN,
      live: true,
      spans: [RUN.spans[0], { ...RUN.spans[1], ended_at: null, duration_ms: null }],
    }
    const { container } = render(<DagView run={live} selectedSpan={null} onSelectSpan={vi.fn()} />)
    expect(screen.getByText('live')).toBeInTheDocument()
    expect(container.querySelectorAll('.node-running').length).toBeGreaterThan(0)
  })

  test('does not crash when a span points at a missing parent', () => {
    // stitched traces can arrive with a parent that lives in another run
    const orphaned = {
      ...RUN,
      spans: [RUN.spans[0], { ...RUN.spans[1], parent_id: 'not-in-this-run' }],
    }
    expect(() => render(<DagView run={orphaned} selectedSpan={null} onSelectSpan={vi.fn()} />)).not.toThrow()
  })
})

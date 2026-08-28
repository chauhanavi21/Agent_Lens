import { render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { beforeEach, describe, expect, test, vi } from 'vitest'

/**
 * App-level behaviour: the wiring between the run list, the views, and the
 * diff pairing. Runs against demo mode, which is also what a visitor sees
 * with no server — so these tests double as a check that the offline
 * experience works.
 */

let App

beforeEach(async () => {
  vi.resetModules()
  vi.stubEnv('VITE_API_URL', '')  // demo mode: bundled data, no network
  globalThis.fetch = vi.fn(() => Promise.reject(new Error('tests must not hit the network')))
  ;({ default: App } = await import('../App'))
})

describe('App in demo mode', () => {
  test('lists the bundled demo runs and shows a demo banner', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('research_agent').length).toBeGreaterThan(0))
    expect(screen.getByText(/demo data/)).toBeInTheDocument()
  })

  test('opens the first run in the graph view by default', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelectorAll('svg g.node').length).toBeGreaterThan(0))
    expect(screen.getByRole('button', { name: 'Graph' })).toHaveClass('active')
  })

  test('switches between graph and timeline', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelector('svg')).toBeTruthy())

    await userEvent.click(screen.getByRole('button', { name: 'Timeline' }))
    await waitFor(() => expect(container.querySelectorAll('.tl-row').length).toBeGreaterThan(0))
    expect(container.querySelector('svg g.node')).toBeNull()
  })

  test('clicking a node opens the span drawer', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelectorAll('svg g.node').length).toBeGreaterThan(0))

    await userEvent.click(container.querySelectorAll('svg g.node')[1])
    await waitFor(() => expect(container.querySelector('.drawer')).toBeTruthy())

    await userEvent.click(screen.getByLabelText('Close span details'))
    await waitFor(() => expect(container.querySelector('.drawer')).toBeNull())
  })

  test('the diff tab stays disabled until two runs are pinned', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelectorAll('.run-item').length).toBeGreaterThan(1))

    const diffTab = screen.getByRole('button', { name: /Diff/ })
    expect(diffTab).toBeDisabled()

    const rows = container.querySelectorAll('.run-item')
    await userEvent.click(within(rows[0]).getByTitle('Pin for diff'))
    expect(screen.getByRole('button', { name: /Diff \(1\/2\)/ })).toBeDisabled()

    await userEvent.click(within(rows[1]).getByTitle('Pin for diff'))
    await waitFor(() => expect(screen.getByRole('button', { name: /Diff \(2\/2\)/ })).toBeEnabled())
  })

  test('diffing two pinned runs shows the comparison', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelectorAll('.run-item').length).toBeGreaterThan(1))

    const rows = container.querySelectorAll('.run-item')
    await userEvent.click(within(rows[0]).getByTitle('Pin for diff'))
    await userEvent.click(within(rows[1]).getByTitle('Pin for diff'))
    await userEvent.click(screen.getByRole('button', { name: /Diff/ }))

    await waitFor(() => expect(container.querySelector('.diff-verdict')).toBeTruthy())
    expect(screen.getByText('Run A')).toBeInTheDocument()
    expect(screen.getByText('Run B')).toBeInTheDocument()
  })

  test('the quality tab renders score trends', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Quality' }))
    await waitFor(() => expect(screen.getByText(/Eval scores over time/)).toBeInTheDocument())
    await waitFor(() => expect(screen.getAllByText('faithfulness').length).toBeGreaterThan(0))
  })

  test('the alerts tab renders the rule builder', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Alerts' }))
    await waitFor(() => expect(screen.getByText('New alert rule')).toBeInTheDocument())
    expect(screen.getByPlaceholderText(/Runs over budget/)).toBeInTheDocument()
  })

  test('creating a rule without a name is refused with an explanation', async () => {
    render(<App />)
    await userEvent.click(screen.getByRole('button', { name: 'Alerts' }))
    await waitFor(() => expect(screen.getByText('New alert rule')).toBeInTheDocument())

    await userEvent.click(screen.getByRole('button', { name: 'Create rule' }))
    await waitFor(() => expect(screen.getByText(/Give the rule a name/)).toBeInTheDocument())
  })

  test('filtering by name narrows the run list', async () => {
    const { container } = render(<App />)
    await waitFor(() => expect(container.querySelectorAll('.run-item').length).toBe(2))

    await userEvent.type(screen.getByPlaceholderText('Filter by name'), 'nothing-matches-this')
    await waitFor(() => expect(screen.getByText(/No runs match/)).toBeInTheDocument())
  })
})

import '@testing-library/jest-dom/vitest'
import { cleanup } from '@testing-library/react'
import { afterEach, vi } from 'vitest'

afterEach(() => {
  cleanup()
  vi.restoreAllMocks()
})

// jsdom has no EventSource. Tests that need one install a fake; anything
// that constructs one without doing so should fail rather than silently
// skip the streaming path.
if (typeof globalThis.EventSource === 'undefined') {
  globalThis.EventSource = class {
    constructor() {
      throw new Error('EventSource was constructed without a test double')
    }
  }
}

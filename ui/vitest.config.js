import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.js'],
    // The UI talks to a server; tests must never actually reach one.
    // Anything that tries will fail loudly rather than hang.
    testTimeout: 10000,
  },
})

import path from 'node:path'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vitest/config'

// Smoke tests (audit QA-02): render real components in jsdom. `npm test -w platform-dashboard`.
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: { '@': path.resolve(import.meta.dirname, './src') },
    dedupe: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
  },
  test: { environment: 'jsdom', include: ['src/**/*.test.tsx', 'src/**/*.test.ts'] },
})

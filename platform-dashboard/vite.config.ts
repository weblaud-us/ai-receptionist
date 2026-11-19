import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Platform admin app. Runs next to the client dashboard (5173), so it gets its own port.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(import.meta.dirname, './src'),
    },
    // @receptionist/shared is linked source: make sure it and the app share one copy of each.
    dedupe: ['react', 'react-dom', 'react-router-dom', '@tanstack/react-query'],
  },
  server: {
    port: 5174,
    strictPort: true,
    // docker compose sets this: bind mounts from a Windows/macOS host don't send file-change events.
    watch: process.env.DEV_WATCH_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
  },
  preview: { port: 4174, strictPort: true },
})

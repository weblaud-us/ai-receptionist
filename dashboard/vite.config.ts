import path from 'node:path'
import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Client dashboard (businesses). The platform admin app is ../platform-dashboard (port 5174).
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
    port: 5173,
    strictPort: true,
    // docker compose sets this: bind mounts from a Windows/macOS host don't send file-change events.
    watch: process.env.DEV_WATCH_POLLING === 'true' ? { usePolling: true, interval: 300 } : undefined,
  },
})

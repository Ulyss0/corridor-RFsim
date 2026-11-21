import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: the page talks to /ws and /api on its own origin; Vite forwards them to corridor-server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/api': 'http://127.0.0.1:8000',
    },
  },
})

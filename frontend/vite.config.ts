import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// Dev: the page talks to /ws on its own origin; Vite forwards it to corridor-server.
export default defineConfig({
  plugins: [react()],
  server: {
    proxy: {
      '/ws': { target: 'ws://127.0.0.1:8000', ws: true },
      '/api': { target: 'http://127.0.0.1:8000' },
    },
  },
})

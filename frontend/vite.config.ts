import tailwindcss from '@tailwindcss/vite'
import react from '@vitejs/plugin-react'
import { defineConfig } from 'vite'

// The backend. 8010 rather than 8000 because another project on this machine
// already holds that port.
const BACKEND = 'http://127.0.0.1:8010'

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    // Proxying instead of calling the backend across origins. It keeps CORS out
    // of the picture entirely, and CORS plus SSE is a particularly unpleasant
    // combination to debug -- the connection opens, then silently delivers
    // nothing. It also means the browser only ever talks to one origin, so the
    // production setup behind a single reverse proxy behaves the same way.
    proxy: {
      '/api': { target: BACKEND, changeOrigin: true },
      '/in': { target: BACKEND, changeOrigin: true },
    },
  },
})

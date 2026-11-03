import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  server: {
    // The repo lives on the Windows drive (/mnt/c); WSL gets no file-change events from it,
    // so without polling the dev server keeps serving stale modules.
    watch: { usePolling: true, interval: 300 },
    proxy: {
      '/ws': {
        target: 'ws://localhost:8000',
        ws: true,
      },
    },
  },
})

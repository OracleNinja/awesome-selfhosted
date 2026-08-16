import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

/**
 * Dev server proxies /api to the JARVIS server so the browser only ever talks
 * to one origin — no CORS, and no API token in a cross-origin request.
 */
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: process.env.JARVIS_SERVER_URL || 'http://127.0.0.1:8787',
        changeOrigin: true,
        // SSE needs streaming, not buffering.
        ws: false,
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
    chunkSizeWarningLimit: 900,
  },
});

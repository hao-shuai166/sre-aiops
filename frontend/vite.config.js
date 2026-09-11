import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [vue()],
  server: {
    port: 5173,
    // Dev-time proxy: the frontend always calls /diagnose (same as prod);
    // in dev mode there is no backend origin, so forward it to FastAPI.
    // (/api/* is also forwarded for any future backend sub-routes.)
    proxy: {
      '/diagnose': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/health': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
      },
      '/api': {
        target: 'http://127.0.0.1:8000',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    // Single-file friendly: emit plain ES modules (no legacy chunks needed
    // for an internal ops console).
    target: 'es2020',
  },
})

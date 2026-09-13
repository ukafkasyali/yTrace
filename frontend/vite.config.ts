import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  server: {
    // Route the narrower scout prefix first; inference keeps its existing port.
    proxy: {
      '/api/sourcing-requirement-previews': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/api/sourcing-runs': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/api/approved-sources': { target: 'http://127.0.0.1:8001', changeOrigin: true },
      '/api/ingestions': { target: 'http://127.0.0.1:8002', changeOrigin: true },
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});

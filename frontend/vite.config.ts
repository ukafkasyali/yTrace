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
      '/api-v6': { target: 'http://127.0.0.1:8003', changeOrigin: true, rewrite: path => path.replace(/^\/api-v6/, '/api') },
      '/api': { target: 'http://127.0.0.1:8000', changeOrigin: true },
    },
  },
});

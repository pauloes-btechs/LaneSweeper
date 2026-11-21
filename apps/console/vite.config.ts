import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

const api = process.env.LK_API_ORIGIN ?? 'http://127.0.0.1:8787';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // the console imports the original LK-01 parametric model from apps/site (kept as the single source of truth)
    fs: { allow: ['..', '../..'] },
    proxy: { '/v1': api, '/health': api },
  },
  build: { chunkSizeWarningLimit: 1600 },
  test: { globals: true, environment: 'jsdom' },
});

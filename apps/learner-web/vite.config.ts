import react from '@vitejs/plugin-react';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react()],
  server: {
    port: 4173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8765',
        xfwd: true,
      },
    },
    fs: {
      allow: ['../..'],
    },
  },
  test: {
    environment: 'jsdom',
    setupFiles: './src/test-setup.ts',
  },
});

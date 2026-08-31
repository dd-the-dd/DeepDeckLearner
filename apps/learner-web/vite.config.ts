import react from '@vitejs/plugin-react';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
import { defineConfig } from 'vitest/config';

export default defineConfig({
  plugins: [react(), vue()],
  resolve: {
    alias: [
      {
        find: /^@deepdeck\/pixi$/,
        replacement: fileURLToPath(
          new URL('../../external/deepdeck-pixi/src/index.mjs', import.meta.url),
        ),
      },
    ],
  },
  server: {
    port: 4173,
    proxy: {
      '/api': 'http://127.0.0.1:8765',
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

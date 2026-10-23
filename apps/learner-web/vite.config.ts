import react from '@vitejs/plugin-react';
import vue from '@vitejs/plugin-vue';
import { fileURLToPath, URL } from 'node:url';
import { createHash } from 'node:crypto';
import { existsSync, readFileSync, readdirSync } from 'node:fs';
import { relative, resolve } from 'node:path';
import type { Plugin } from 'vite';
import { defineConfig } from 'vitest/config';

// Packaging can then reject an older cockpit or stale assets, and include only
// this build's files while open tabs keep their previous hashed chunks on disk.
function playerBuildManifest(): Plugin {
  const root = fileURLToPath(new URL('.', import.meta.url));
  function files(directory: string): string[] {
    return readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
      const path = resolve(directory, entry.name);
      return entry.isDirectory() ? files(path) : [path];
    });
  }
  return {
    name: 'deepdeck-player-manifest',
    enforce: 'post',
    generateBundle(_options, bundle) {
      if (!/\bfrom\s+['"]\.\/PlayerApp['"]/.test(readFileSync(resolve(root, 'src/main.tsx'), 'utf8'))) {
        this.error('The frontend entry point must open PlayerApp before packaging.');
      }
      const sources = [
        ...files(resolve(root, 'src')).filter(path =>
          /\.(tsx?|css)$/.test(path) && !path.includes('.test.') && !path.endsWith('test-setup.ts')),
        ...['index.html', 'package.json', 'package-lock.json', 'vite.config.ts'].map(path => resolve(root, path)),
      ];
      const sourceSha256 = Object.fromEntries(sources.map(path => [
        relative(root, path).replaceAll('\\', '/'), createHash('sha256').update(readFileSync(path)).digest('hex'),
      ]));
      const publicRoot = resolve(root, 'public');
      const publicFiles = existsSync(publicRoot) ? files(publicRoot).map(path => relative(publicRoot, path).replaceAll('\\', '/')) : [];
      const manifest = { schemaVersion: 'deepdeck-web/v1', interface: 'player', sourceSha256,
        files: [...new Set([...Object.keys(bundle), ...publicFiles])].sort() };
      this.emitFile({ type: 'asset', fileName: 'deepdeck-web.json', source: JSON.stringify(manifest, null, 2) + '\n' });
    },
  };
}

export default defineConfig({
  plugins: [react(), vue(), playerBuildManifest()],
  build: {
    // The local workbench can be rebuilt while an Engine game is open. Keep
    // content-hashed chunks from the previous build so an existing tab can
    // still finish loading its lazy Pixi modules.
    emptyOutDir: false,
  },
  resolve: {
    alias: [
      {
        find: /^@deepdeck\/pixi$/,
        replacement: fileURLToPath(
          new URL('../../external/deepdeck-pixi/src/index.mjs', import.meta.url),
        ),
      },
      {
        find: /^pixi\.js$/,
        replacement: fileURLToPath(
          new URL('./node_modules/pixi.js/lib/index.mjs', import.meta.url),
        ),
      },
      {
        find: /^vue$/,
        replacement: fileURLToPath(
          new URL('./node_modules/vue/dist/vue.runtime.esm-bundler.js', import.meta.url),
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
    // Pixi and the mixed React/Vue suite are sizeable in jsdom. Running one
    // file at a time keeps the downloadable desktop experience stable on
    // machines with modest memory instead of spawning a worker per test file.
    fileParallelism: false,
    maxWorkers: 1,
  },
});

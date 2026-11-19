import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
import { fileURLToPath } from 'node:url'

export default defineConfig({
  plugins: [vue()],
  build: {
    outDir: fileURLToPath(new URL('../src/sleepy/static', import.meta.url)),
    emptyOutDir: true,
    license: { fileName: 'THIRD_PARTY_LICENSES.txt' },
  },
  server: { proxy: { '/api': 'http://127.0.0.1:8080' } },
})

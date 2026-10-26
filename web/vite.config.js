import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'

// 构建产物由 FastAPI 托管（见 app/modules/system/router.py）
export default defineConfig({
  plugins: [vue()],
  base: '/',
  build: { outDir: 'dist', emptyOutDir: true, chunkSizeWarningLimit: 900 },
  server: {
    port: 5173,
    proxy: {
      '/api': 'http://127.0.0.1:8787',
      '/health': 'http://127.0.0.1:8787',
      '/v1': 'http://127.0.0.1:8787',
      '/auth': 'http://127.0.0.1:8787',
    },
  },
})

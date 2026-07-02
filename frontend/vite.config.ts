import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 构建 SPA，产物输出到 dist/（与后端 /app/admin/dist 结构一致）
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    // 开发时把 API 请求代理到本地后端容器（默认 8000）
    proxy: {
      '/api': 'http://localhost:8000',
      '/ws': { target: 'ws://localhost:8000', ws: true },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})

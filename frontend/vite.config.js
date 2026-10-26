import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发态：前端 5173，后端 8001。
// 统一约定所有请求走 /api 前缀，由 Vite 代理转发到后端 —— 这样开发环境和
// 生产环境（nginx 反代）的请求路径完全一致，代码里不需要 if (dev) 分支。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8001',
        changeOrigin: true,
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
  build: {
    outDir: 'dist',
    sourcemap: false,
  },
})

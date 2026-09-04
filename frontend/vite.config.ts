import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import { defineConfig } from 'vite'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: {
    proxy: {
      // 开发期前端 5173 → 后端 8000（生产由 FastAPI 托管 frontend/dist）
      '/api': 'http://127.0.0.1:8000',
    },
  },
})

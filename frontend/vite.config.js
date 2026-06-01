import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { resolve } from 'path'

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (
            id.includes('/node_modules/react') ||
            id.includes('/node_modules/react-dom') ||
            id.includes('/node_modules/scheduler')
          ) {
            return 'react-vendor'
          }
          return undefined
        },
      },
    },
  },
  resolve: {
    alias: {
      '@': resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: 5173,
    strictPort: true,
    proxy: {
      '/api/v1': {
        target: 'http://backend:8000',
        changeOrigin: true,
        secure: false,
      },
    },
  },
})

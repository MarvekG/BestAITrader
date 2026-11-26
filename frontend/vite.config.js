import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { createReadStream, existsSync, readFileSync } from 'fs'
import { resolve } from 'path'

const backendLocalesDir = resolve(__dirname, '../backend/app/locales')
const publicLocalesDir = resolve(__dirname, 'public/locales')
const localeFiles = ['zh.json', 'en.json']

// 开发时直接读取后端语言文件；本地构建时输出到 dist/locales。
// Docker 构建由 backend-locales 上下文注入 public/locales，再由 Vite 复制到产物。
function backendLocalesPlugin() {
  return {
    name: 'backend-locales',
    configureServer(server) {
      server.middlewares.use('/locales', (req, res, next) => {
        if (req.method !== 'GET' && req.method !== 'HEAD') {
          next()
          return
        }

        const fileName = (req.url || '').replace(/^\//, '').split('?')[0]
        const filePath = resolve(backendLocalesDir, fileName)
        if (!localeFiles.includes(fileName) || !existsSync(filePath)) {
          next()
          return
        }

        res.setHeader('Content-Type', 'application/json; charset=utf-8')
        res.setHeader('Cache-Control', 'no-store')
        if (req.method === 'HEAD') {
          res.end()
          return
        }

        createReadStream(filePath).on('error', next).pipe(res)
      })
    },
    generateBundle() {
      if (!existsSync(backendLocalesDir)) {
        if (!existsSync(publicLocalesDir)) {
          this.error('[backend-locales] No locale files found for frontend build.')
        }
        return
      }

      for (const fileName of localeFiles) {
        const filePath = resolve(backendLocalesDir, fileName)
        if (!existsSync(filePath)) {
          this.error(`[backend-locales] Missing locale file: ${filePath}`)
          return
        }

        this.emitFile({
          type: 'asset',
          fileName: `locales/${fileName}`,
          source: readFileSync(filePath),
        })
      }
    },
  }
}

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react(), backendLocalesPlugin()],
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

import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { readFileSync } from 'fs'
import { resolve, dirname } from 'path'
import { fileURLToPath } from 'url'

const __dirname = dirname(fileURLToPath(import.meta.url))
const configYaml = readFileSync(resolve(__dirname, '../ha-ai-companion/config.yaml'), 'utf-8')
const version = configYaml.match(/^version:\s*"([^"]+)"/m)?.[1] ?? 'dev'

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../ha-ai-companion/static/dist',
    emptyOutDir: true,
    // Don't emit the Vite index.html — FastAPI serves templates/index.html via Jinja2
    rollupOptions: {
      input: 'src/main.jsx',
      output: {
        entryFileNames: `bundle.${version}.js`,
        chunkFileNames: 'bundle-[hash].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith('.css')) return `bundle.${version}.css`
          return assetInfo.name ?? 'asset'
        }
      }
    }
  }
})

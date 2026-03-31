import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  base: './',
  build: {
    outDir: '../custom_components/ha_ai_companion/static/dist',
    emptyOutDir: true,
    // Don't emit the Vite index.html — FastAPI serves templates/index.html via Jinja2
    rollupOptions: {
      input: 'src/main.jsx',
      output: {
        entryFileNames: 'bundle.js',
        chunkFileNames: 'bundle-[hash].js',
        assetFileNames: (assetInfo) => {
          if (assetInfo.name?.endsWith('.css')) return 'bundle.css'
          return assetInfo.name ?? 'asset'
        }
      }
    }
  }
})

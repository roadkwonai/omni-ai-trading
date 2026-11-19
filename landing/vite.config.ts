import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// base './': works under the Pages sub path and in local preview; 'site' keeps clear of the reports' assets/ folder.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: { assetsDir: 'site', rollupOptions: { input: { main: 'index.html', notFound: '404.html' } } },
})

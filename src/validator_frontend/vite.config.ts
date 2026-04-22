import { cloudflare } from '@cloudflare/vite-plugin'
import build from '@hono/vite-build/cloudflare-workers'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(() => {
  return {
   plugins: [build({ outputDir: 'dist', entry: './src/frontend/index.tsx' }), tailwindcss()]
  }
})

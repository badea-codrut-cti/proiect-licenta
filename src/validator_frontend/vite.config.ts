import { cloudflare } from '@cloudflare/vite-plugin'
import build from '@hono/vite-build/cloudflare-workers'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ command, isSsrBuild }) => {
  if (command === 'serve') {
    return { plugins: [tailwindcss(), cloudflare()] }
  }
if (!isSsrBuild) {
    return {
      build: {
        rollupOptions: {
          input: ['./src/style.css'],
          output: {
            assetFileNames: 'assets/[name].[ext]'
          }
        },
      },
      plugins: [tailwindcss()]
    }
  }
  return {
    plugins: [build({ outputDir: 'dist', entry: './src/frontend/index.tsx' }), tailwindcss()]
  }
})

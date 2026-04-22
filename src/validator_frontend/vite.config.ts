import { cloudflare } from '@cloudflare/vite-plugin'
import { defineConfig } from 'vite'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig(({ mode }) => {
  if (mode === 'client') {
    return {
      plugins: [tailwindcss()],
      esbuild: {
        jsxImportSource: 'hono/jsx/dom',
      },
      build: {
        rollupOptions: {
          input: './src/client.tsx',
          output: {
            entryFileNames: 'static/client.js',
          },
        },
        copyPublicDir: false,
      },
    }
  }

  return {
    plugins: [cloudflare(), tailwindcss()],
  }
})

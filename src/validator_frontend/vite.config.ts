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
        outDir: 'dist/client',
        rollupOptions: {
          input: './src/client.tsx',
          output: {
            entryFileNames: 'static/client.js',
            assetFileNames: 'static/style.[ext]',
          },
        },
        copyPublicDir: false,
      },
    }
  }

  return {
    plugins: [cloudflare(), tailwindcss()],
    build: {
      emptyOutDir: false,
    },
  }
})

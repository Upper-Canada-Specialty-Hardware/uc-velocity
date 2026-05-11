import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'
import path from 'path'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  build: {
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (!id.includes('node_modules')) return
          // Note: @react-pdf and xlsx are intentionally NOT split into named chunks here.
          // Both are only ever reached via dynamic import() in their callers (print/export
          // handlers + ReportsPage), so Rollup already creates lazy chunks for them on its
          // own. Manually bucketing @react-pdf produced a circular chunk dependency with
          // vendor-react via Rollup's auto-generated CJS interop helper, which forced
          // vendor-pdf into the entry's modulepreload list — undoing the lazy split.
          if (id.includes('@clerk/')) return 'vendor-clerk'
          if (id.includes('@radix-ui/')) return 'vendor-radix'
          if (id.includes('/lucide-react/')) return 'vendor-icons'
          if (
            id.includes('/react/') ||
            id.includes('/react-dom/') ||
            id.includes('/react-is/') ||
            id.includes('/scheduler/') ||
            id.includes('/object-assign/')
          ) {
            return 'vendor-react'
          }
        },
      },
    },
  },
})

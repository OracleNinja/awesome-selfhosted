import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

export default defineConfig({
  plugins: [react()],
  // Electron loads the production build from the filesystem, so asset URLs
  // must be relative rather than rooted at /.
  base: './',
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    rollupOptions: {
      output: {
        // Split the three big vendor groups so their cost is visible and so a
        // change to app code does not invalidate all of it.
        manualChunks: {
          react: ['react', 'react-dom'],
          markdown: ['react-markdown', 'remark-gfm'],
          highlight: ['lowlight'],
        },
      },
    },
  },
  server: {
    strictPort: false,
  },
});

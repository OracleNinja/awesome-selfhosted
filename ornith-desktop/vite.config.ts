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
  },
  server: {
    strictPort: false,
  },
});

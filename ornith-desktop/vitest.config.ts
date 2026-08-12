import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    // Playwright drives Electron itself and must not be collected by vitest.
    include: ['tests/unit/**/*.test.ts', 'tests/integration/**/*.test.ts'],
    environment: 'node',
    coverage: {
      provider: 'v8',
      include: ['electron/ollama/**', 'electron/store/**', 'shared/**'],
      reporter: ['text-summary', 'json-summary'],
    },
  },
});

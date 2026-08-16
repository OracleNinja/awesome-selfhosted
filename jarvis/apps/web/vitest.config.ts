import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    name: 'control-room',
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    testTimeout: 15_000,
  },
});

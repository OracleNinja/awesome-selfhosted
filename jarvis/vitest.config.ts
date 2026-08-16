import { defineConfig } from 'vitest/config';

export default defineConfig({
  test: {
    include: ['tests/**/*.test.ts'],
    environment: 'node',
    // Each suite opens its own in-memory database; running files in parallel is
    // safe, but the HTTP suites bind sockets, so keep concurrency modest.
    pool: 'forks',
    testTimeout: 30_000,
    hookTimeout: 30_000,
  },
});

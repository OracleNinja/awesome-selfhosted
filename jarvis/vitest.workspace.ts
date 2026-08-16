/**
 * Two test projects: the runtime (node) and the Control Room client (also node —
 * the runtime adapter is deliberately testable without a browser, because
 * `fetch` and `EventSource` are injected).
 *
 * Browser-only behaviour is covered by Playwright against the real built app.
 */
export default ['./vitest.config.ts', './apps/web/vitest.config.ts'];

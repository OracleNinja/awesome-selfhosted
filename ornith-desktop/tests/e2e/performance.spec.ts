import { test, expect, _electron as electron, type ElectronApplication, type Page } from '@playwright/test';
import { mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { startStubOllama, type StubChunk, type StubHandle } from '../integration/stubOllama';

const appRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');

/**
 * Phase 5 gate (SPEC §6.3): a long response must not starve the UI thread.
 *
 * Note on the environment: this runs under Xvfb with software rasterisation, so
 * the absolute frame rate is a floor, not a representative number for real
 * hardware with GPU compositing.
 */
test('a long streamed response keeps the UI responsive', async () => {
  const userData = mkdtempSync(path.join(tmpdir(), 'ornith-perf-'));

  // ~4000 tokens of prose plus code, delivered fast.
  const script: StubChunk[] = Array.from({ length: 4000 }, (_, i) =>
    i % 400 === 399
      ? { content: '\n\n```python\ndef f(x):\n    return x * 2\n```\n\n' }
      : { content: `token${i} ` },
  );
  script.push({ done: true, eval_count: 4000, eval_duration: 4_000_000_000 });

  const stub: StubHandle = await startStubOllama({ script, chunkDelayMs: 0 });

  const app: ElectronApplication = await electron.launch({
    args: [appRoot, '--no-sandbox', '--disable-gpu'],
    env: {
      ...process.env,
      ORNITH_USER_DATA: userData,
      ORNITH_OLLAMA_URL: stub.url,
      NODE_ENV: 'test',
    },
  });

  const page: Page = await app.firstWindow();
  await page.waitForSelector('.app', { timeout: 30_000 });

  try {
    // Sample frame intervals for the duration of the stream.
    await page.evaluate(() => {
      const w = window as unknown as { __frames: number[]; __last: number };
      w.__frames = [];
      w.__last = performance.now();
      const tick = () => {
        const now = performance.now();
        w.__frames.push(now - w.__last);
        w.__last = now;
        requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
    });

    await page.getByTestId('composer-input').fill('Generate a long answer');
    await page.getByTestId('send-button').click();

    // Wait for completion: the Stop button disappears when the stream ends.
    await expect(page.getByTestId('send-button')).toBeVisible({ timeout: 90_000 });
    // Indices where i % 400 === 399 are code blocks, so 3998 is the last token.
    await expect(page.locator('.message-meta')).toBeVisible({ timeout: 30_000 });
    const rendered = await page.locator('.message-assistant .markdown').innerText();
    expect(rendered).toContain('token3998');
    expect(rendered).toContain('def f(x):');

    const metrics = await page.evaluate(() => {
      const frames = (window as unknown as { __frames: number[] }).__frames.slice(1);
      const sorted = [...frames].sort((a, b) => a - b);
      const p95 = sorted[Math.floor(sorted.length * 0.95)] ?? 0;
      const longFrames = frames.filter((f) => f > 50).length;
      return {
        count: frames.length,
        median: sorted[Math.floor(sorted.length / 2)] ?? 0,
        p95,
        longFrames,
      };
    });

    console.log(
      `[perf] frames=${metrics.count} median=${metrics.median.toFixed(1)}ms ` +
        `p95=${metrics.p95.toFixed(1)}ms frames>50ms=${metrics.longFrames}`,
    );

    // The composer must still accept input while all that text is on screen.
    const typingStart = Date.now();
    await page.getByTestId('composer-input').fill('still responsive');
    const typingMs = Date.now() - typingStart;

    expect(typingMs).toBeLessThan(2000);
    await expect(page.getByTestId('composer-input')).toHaveValue('still responsive');

    // Under software rendering the bar is "no pathological stalls", not 60fps.
    expect(metrics.p95).toBeLessThan(250);
  } finally {
    await app.close().catch(() => {});
    await stub.close();
    rmSync(userData, { recursive: true, force: true });
  }
});

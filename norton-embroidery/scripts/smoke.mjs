/**
 * Browser smoke test: drives the real application through the whole workflow
 * with a real artwork file and checks that a real PES file comes out.
 *
 *   node scripts/smoke.mjs [baseUrl] [outDir]
 *
 * Requires a running server (npm run preview) and Chromium.
 */

import { chromium } from 'playwright';
import { PNG } from 'pngjs';
import { mkdirSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const BASE_URL = process.argv[2] ?? 'http://127.0.0.1:4173/';
const OUT_DIR = process.argv[3] ?? '/tmp/norton-smoke';
const CHROME = process.env.CHROME_PATH ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

mkdirSync(OUT_DIR, { recursive: true });

/** Build a small three-colour logo as a real PNG. */
function makeArtwork() {
  const size = 200;
  const png = new PNG({ width: size, height: size });
  const set = (x, y, r, g, b) => {
    const i = (y * size + x) * 4;
    png.data[i] = r;
    png.data[i + 1] = g;
    png.data[i + 2] = b;
    png.data[i + 3] = 255;
  };
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) set(x, y, 255, 255, 255);
  }
  // Blue disc.
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const d = (x - 100) ** 2 + (y - 100) ** 2;
      if (d <= 80 * 80) set(x, y, 20, 60, 180);
    }
  }
  // Red bar across the middle.
  for (let y = 88; y < 112; y++) {
    for (let x = 35; x < 165; x++) set(x, y, 220, 30, 40);
  }
  // Yellow square top-left.
  for (let y = 45; y < 75; y++) {
    for (let x = 70; x < 100; x++) set(x, y, 250, 210, 30);
  }
  return PNG.sync.write(png);
}


/** Encode a small three-colour logo as a baseline JPEG using the browser. */
async function makeJpegInPage(page) {
  return Buffer.from(
    await page.evaluate(() => {
      const c = document.createElement('canvas');
      c.width = 200;
      c.height = 200;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#ffffff';
      ctx.fillRect(0, 0, 200, 200);
      ctx.fillStyle = '#1440b4';
      ctx.beginPath();
      ctx.arc(100, 100, 70, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = '#d81e28';
      ctx.fillRect(40, 90, 120, 20);
      return c.toDataURL('image/jpeg', 0.95).split(',')[1];
    }),
    'base64',
  );
}


/** Switch into advanced view if we are not already there (state resets on reload). */
async function ensureAdvanced(page) {
  const toggle = page.locator('.topbar button:has-text("Advanced view")');
  if (await toggle.count()) await toggle.click();
  await page.waitForSelector('.tabs button:has-text("Object"):visible');
}

const log = (...args) => console.log('•', ...args);
const fail = (msg) => {
  console.error('✗', msg);
  process.exitCode = 1;
  throw new Error(msg);
};

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 950 }, acceptDownloads: true });
const page = await context.newPage();

const consoleErrors = [];
page.on('console', (m) => {
  if (m.type() === 'error') consoleErrors.push(m.text());
});
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));

try {
  // --- 1. launch ---------------------------------------------------------
  await page.goto(BASE_URL, { waitUntil: 'networkidle' });
  await page.waitForSelector('.modal h2');
  log('application loaded');

  // --- 2. create a project ----------------------------------------------
  await page.fill('#np-name', 'Smoke Test Logo');
  await page.fill('#np-customer', 'Norton QA');
  await page.fill('#np-width', '70');
  await page.fill('#np-height', '70');
  await page.click('.modal .footer button.primary');
  await page.waitForSelector('.canvas-area canvas');
  log('project created');
  await page.screenshot({ path: join(OUT_DIR, '01-workspace.png') });

  // --- 3. upload real artwork -------------------------------------------
  const artworkPath = join(OUT_DIR, 'artwork.png');
  writeFileSync(artworkPath, makeArtwork());
  await page.setInputFiles('.panel.left input[type=file]', artworkPath);
  await page.waitForSelector('.suitability', { timeout: 30000 });
  const suitability = await page.textContent('.suitability');
  log('artwork analysed, suitability:', suitability.trim());
  await page.screenshot({ path: join(OUT_DIR, '02-analysed.png') });

  // --- 4. digitize -------------------------------------------------------
  // Upload alone should already have produced stitches.
  await page.waitForFunction(
    () => /[1-9][\d,]* stitches/.test(document.querySelector('.canvas-readout')?.textContent || ''),
    { timeout: 90000 },
  );
  log('auto-digitized on upload (no button press)');
  await page.waitForFunction(
    () => document.querySelectorAll('.object-list li').length > 0 &&
      !document.querySelector('.object-list li')?.textContent?.includes('No objects'),
    { timeout: 60000 },
  );
  const objectCount = await page.locator('.object-list li').count();
  log('digitized into', objectCount, 'objects');

  // --- 5. read the live statistics --------------------------------------
  const readStats = async () => {
    const text = await page.textContent('.canvas-readout');
    const stitches = Number((text.match(/([\d,]+) stitches/) ?? [])[1]?.replace(/,/g, '') ?? 0);
    const colors = Number((text.match(/(\d+) colours?/) ?? [])[1] ?? 0);
    const dims = text.match(/([\d.]+) × ([\d.]+) mm/);
    return { stitches, colors, width: Number(dims?.[1] ?? 0), height: Number(dims?.[2] ?? 0), raw: text };
  };
  const stats = await readStats();
  log('stats:', JSON.stringify(stats));
  if (stats.stitches < 500) fail(`expected a substantial stitch count, got ${stats.stitches}`);
  if (stats.colors < 2) fail(`expected multiple colours, got ${stats.colors}`);
  if (stats.width <= 0 || stats.width > 130) fail(`design width ${stats.width} mm is implausible`);
  await page.screenshot({ path: join(OUT_DIR, '03-digitized.png') });

  // --- 6. the preview must render stitches, not the artwork --------------
  const canvasStats = await page.evaluate(() => {
    const canvas = document.querySelector('.canvas-area canvas');
    const ctx = canvas.getContext('2d');
    const d = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const colors = new Map();
    let nonBackground = 0;
    for (let i = 0; i < d.length; i += 4) {
      const key = `${d[i] >> 4},${d[i + 1] >> 4},${d[i + 2] >> 4}`;
      colors.set(key, (colors.get(key) ?? 0) + 1);
      if (d[i + 3] > 0 && !(d[i] > 230 && d[i + 1] > 230 && d[i + 2] > 230)) nonBackground++;
    }
    return { distinctColors: colors.size, nonBackground, total: d.length / 4 };
  });
  log('canvas:', JSON.stringify(canvasStats));
  if (canvasStats.nonBackground < 1000) fail('the stitch preview appears to be blank');

  // --- 7. simulator ------------------------------------------------------
  await page.click('button:has-text("Restart")');
  await page.click('.timeline button.primary');
  await page.waitForTimeout(1200);
  const statusMid = await page.textContent('.timeline-controls');
  await page.click('.timeline button.primary'); // pause
  log('simulator ran:', statusMid.match(/stitch [\d,]+ \/ [\d,]+/)?.[0]);
  await page.screenshot({ path: join(OUT_DIR, '04-simulating.png') });
  await page.click('button:has-text("Restart")');

  // --- 8. edit an object -------------------------------------------------
  // Stitch-level controls live in advanced view; switch into it first.
  await ensureAdvanced(page);
  await page.waitForSelector('.object-list li');
  await page.click('.object-list li:first-child');
  await page.click('.tabs button:has-text("Object")');
  await page.waitForSelector('.panel.right #density');
  const beforeEdit = (await readStats()).stitches;
  await page.fill('.panel.right #density', '0.80');
  await page.dispatchEvent('.panel.right #density', 'change');
  await page.waitForTimeout(700);
  const afterEdit = (await readStats()).stitches;
  log('density edit changed stitch count', beforeEdit, '->', afterEdit);
  if (afterEdit === beforeEdit) fail('editing density did not change the stitches');

  // --- 9. undo -----------------------------------------------------------
  await page.click('button:has-text("Undo")');
  await page.waitForTimeout(500);
  const afterUndo = (await readStats()).stitches;
  if (afterUndo !== beforeEdit) fail(`undo did not restore the design (${afterUndo} vs ${beforeEdit})`);
  log('undo restored the design');

  // --- 10. validation ----------------------------------------------------
  await page.click('.tabs button:has-text("Validate")');
  await page.waitForSelector('.issue');
  const issues = await page.locator('.issue').allTextContents();
  log('validation reported', issues.length, 'issue(s)');
  for (const i of issues.slice(0, 4)) log('   ', i.replace(/\s+/g, ' ').slice(0, 120));
  if (issues.some((i) => /something went wrong/i.test(i))) fail('found a vague validation message');
  await page.screenshot({ path: join(OUT_DIR, '05-validation.png') });

  // --- 11. export --------------------------------------------------------
  const ackBox = page.locator('.section:has-text("Export PES") input[type=checkbox]');
  if (await ackBox.isEnabled()) await ackBox.check();

  const exportButton = page.locator('button:has-text("Export .pes")');
  if (await exportButton.isDisabled()) fail('export button is disabled on a valid design');

  const [download] = await Promise.all([page.waitForEvent('download', { timeout: 20000 }), exportButton.click()]);
  const pesPath = join(OUT_DIR, download.suggestedFilename());
  await download.saveAs(pesPath);
  const pes = readFileSync(pesPath);
  log('downloaded', download.suggestedFilename(), pes.length, 'bytes');

  if (pes.subarray(0, 8).toString('ascii') !== '#PES0001') {
    fail(`downloaded file is not a PES file (starts with "${pes.subarray(0, 8).toString('ascii')}")`);
  }
  await page.waitForSelector('.ok-banner');
  const checks = await page.locator('.check-list li').allTextContents();
  log('post-export verification:');
  for (const c of checks) log('   ', c);
  if (await page.locator('.check-list li.failed').count()) fail('a post-export verification check failed');
  await page.screenshot({ path: join(OUT_DIR, '06-exported.png') });

  // --- 12. save and reopen the project -----------------------------------
  await page.click('.topbar button:has-text("Save")');
  await page.waitForTimeout(400);
  await page.reload({ waitUntil: 'networkidle' });
  await page.waitForSelector('.modal h2');
  await page.click('.modal .footer button.primary'); // dismiss new-project dialog
  await page.click('.topbar button:has-text("Projects")');
  await page.waitForSelector('.project-table');
  const rows = await page.locator('.project-table tbody tr').count();
  if (rows < 1) fail('the saved project did not appear in the project list');
  await page.click('.project-table tbody tr:first-child button:has-text("Open")');
  await page.waitForTimeout(1500);
  const reopened = await readStats();
  log('reopened project stats:', JSON.stringify({ stitches: reopened.stitches, colors: reopened.colors }));
  if (reopened.stitches !== stats.stitches) {
    fail(`reopened project has ${reopened.stitches} stitches, expected ${stats.stitches}`);
  }
  log('project reopened with work intact');
  await page.screenshot({ path: join(OUT_DIR, '07-reopened.png') });

  // --- 13. import the PES we exported ------------------------------------
  await page.setInputFiles('input[accept=".pes,.pec"]', pesPath);
  await page.waitForTimeout(1200);
  const imported = await readStats();
  log('re-imported our own PES:', JSON.stringify({ stitches: imported.stitches, colors: imported.colors }));
  if (Math.abs(imported.stitches - stats.stitches) > 1) {
    fail(`re-import gave ${imported.stitches} stitches, expected ~${stats.stitches}`);
  }
  await page.screenshot({ path: join(OUT_DIR, '08-imported.png') });

  // --- 14. other artwork formats ----------------------------------------
  for (const [label, fileName, contents] of [
    ['SVG', 'logo.svg', Buffer.from(
      `<svg xmlns="http://www.w3.org/2000/svg" width="200" height="200" viewBox="0 0 200 200">` +
      `<rect width="200" height="200" fill="#ffffff"/>` +
      `<circle cx="100" cy="100" r="70" fill="#1440b4"/>` +
      `<rect x="40" y="90" width="120" height="20" fill="#d81e28"/>` +
      `</svg>`,
    )],
    ['JPEG', 'logo.jpg', await makeJpegInPage(page)],
  ]) {
    const p = join(OUT_DIR, fileName);
    writeFileSync(p, contents);

    await page.click('.topbar button:has-text("New")');
    await page.waitForSelector('.modal h2');
    await page.fill('#np-name', `${label} test`);
    await page.click('.modal .footer button.primary');
    await page.waitForSelector('.canvas-area canvas');

    await page.setInputFiles('.panel.left input[type=file]', p);
    await page.waitForSelector('.suitability', { timeout: 30000 });
    await page.click('button:has-text("Digitize artwork")');
    await page.waitForFunction(
      () => document.querySelectorAll('.object-list li').length > 0 &&
        !document.querySelector('.object-list li')?.textContent?.includes('No objects'),
      { timeout: 60000 },
    );
    const s = await readStats();
    log(`${label} upload digitized:`, JSON.stringify({ stitches: s.stitches, colors: s.colors }));
    if (s.stitches < 200) fail(`${label} artwork produced only ${s.stitches} stitches`);
  }
  await page.screenshot({ path: join(OUT_DIR, '10-formats.png') });

  // --- 16. oversized design must block export ----------------------------
  await ensureAdvanced(page);
  await page.click('.tabs button:has-text("Object")');
  for (let i = 0; i < 12; i++) await page.click('button:has-text("Scale +10%")');
  await page.waitForTimeout(800);
  await page.click('.tabs button:has-text("Validate")');
  await page.waitForSelector('.issue.ERROR', { timeout: 10000 });
  const hoopError = (await page.locator('.issue.ERROR').allTextContents()).join(' ');
  if (!/exceeds the .* hoop by [\d.]+ inches/.test(hoopError)) {
    fail(`oversize error did not quote the overage: ${hoopError.slice(0, 160)}`);
  }
  if (!(await page.locator('button:has-text("Export .pes")').isDisabled())) {
    fail('export is still enabled on a design that does not fit the hoop');
  }
  log('oversized design blocked:', hoopError.replace(/\s+/g, ' ').slice(0, 110));
  await page.screenshot({ path: join(OUT_DIR, '09-blocked.png') });

  // --- 15. rejects an unsupported file ----------------------------------
  const badPath = join(OUT_DIR, 'notes.txt');
  writeFileSync(badPath, 'this is not artwork');
  await page.setInputFiles('.panel.left input[type=file]', badPath);
  await page.waitForSelector('.error-banner', { timeout: 10000 });
  const bannerText = await page.textContent('.error-banner');
  if (!/not a supported artwork file|could not be decoded/i.test(bannerText)) {
    fail(`unsupported file gave an unhelpful message: ${bannerText}`);
  }
  log('unsupported file rejected with:', bannerText.replace(/\s+/g, ' ').slice(0, 90));

  if (consoleErrors.length) {
    console.error('✗ console errors:', consoleErrors.slice(0, 10));
    process.exitCode = 1;
  } else {
    log('no console errors');
  }

  console.log('\nSMOKE TEST PASSED — artifacts in', OUT_DIR);
} catch (err) {
  await page.screenshot({ path: join(OUT_DIR, 'failure.png') }).catch(() => {});
  console.error('\nSMOKE TEST FAILED:', err.message);
  if (consoleErrors.length) console.error('console errors:', consoleErrors.slice(0, 10));
  process.exitCode = 1;
} finally {
  await browser.close();
}

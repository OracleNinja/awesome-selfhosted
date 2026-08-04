/**
 * Build the physical-validation packet for every controlled test design.
 *
 *   npm run preview &                # serve the built app
 *   npm run fixtures:build
 *
 * For each design this drives the REAL application in a browser and writes:
 *
 *   fixtures/out/<id>/artwork.png    the artwork the customer would supply
 *   fixtures/out/<id>/preview.png    the stitch preview, captured from the app's
 *                                    own canvas — not a re-render
 *   fixtures/out/<id>/design.pes     the file the machine reads
 *   fixtures/out/<id>/worksheet.txt  the sheet to fill in at the machine
 *   fixtures/out/<id>/report.json    the measured metrics
 *   fixtures/out/REPORT.md           an index of all six
 *
 * Those are the four things to compare: artwork, preview, PES, and the physical
 * stitch-out that the worksheet records.
 */

import { chromium } from 'playwright';
import { execFileSync } from 'node:child_process';
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const root = join(here, '..');
const fixturesDir = join(root, 'fixtures');
const outRoot = join(fixturesDir, 'out');

const BASE_URL = process.argv[2] ?? 'http://127.0.0.1:4173/';
const CHROME = process.env.CHROME_PATH ?? '/opt/pw-browsers/chromium-1194/chrome-linux/chrome';

const catalog = JSON.parse(readFileSync(join(fixturesDir, 'catalog.json'), 'utf8'));
mkdirSync(outRoot, { recursive: true });

const log = (...a) => console.log('•', ...a);
const fail = (m) => {
  throw new Error(m);
};

/** Verify a PES file with pyembroidery, an independent implementation. */
function verifyWithPyembroidery(pesPath) {
  const script = `
import json, sys, pyembroidery
from pyembroidery.EmbConstant import STITCH, COMMAND_MASK
p = pyembroidery.read(sys.argv[1])
n = sum(1 for s in p.stitches if (s[2] & COMMAND_MASK) == STITCH)
b = p.bounds()
print(json.dumps({
  "stitches": n,
  "colors": len(p.threadlist),
  "widthMm": round((b[2]-b[0])/10.0, 1),
  "heightMm": round((b[3]-b[1])/10.0, 1),
  "colorList": ["#%06X" % (t.color & 0xFFFFFF) for t in p.threadlist],
}))
`;
  const scriptPath = join(outRoot, '_verify.py');
  writeFileSync(scriptPath, script);
  return JSON.parse(execFileSync('python3', [scriptPath, pesPath], { encoding: 'utf8' }));
}

const browser = await chromium.launch({ executablePath: CHROME, args: ['--no-sandbox'] });
const context = await browser.newContext({ viewport: { width: 1600, height: 950 }, acceptDownloads: true });
const page = await context.newPage();

const consoleErrors = [];
page.on('console', (m) => m.type() === 'error' && consoleErrors.push(m.text()));
page.on('pageerror', (e) => consoleErrors.push(`pageerror: ${e.message}`));

const results = [];

try {
  await page.goto(BASE_URL, { waitUntil: 'networkidle' });
  await page.waitForSelector('.modal h2');

  for (const entry of catalog.designs) {
    log(`--- ${entry.id} ---`);
    const outDir = join(outRoot, entry.id);
    mkdirSync(outDir, { recursive: true });

    // 1. artwork, copied verbatim so the packet is self-contained
    const artworkSrc = join(fixturesDir, entry.artwork);
    copyFileSync(artworkSrc, join(outDir, 'artwork.png'));

    // 2. new project at the fixture's declared physical size
    const alreadyOpen = await page.locator('.modal h2').count();
    if (!alreadyOpen) await page.click('.topbar button:has-text("New")');
    await page.waitForSelector('.modal h2');
    await page.fill('#np-name', entry.name);
    await page.fill('#np-customer', 'Fixture');
    await page.selectOption('#np-hoop', entry.hoopId);
    await page.fill('#np-width', String(entry.widthMm));
    await page.fill('#np-height', String(entry.heightMm));
    await page.click('.modal .footer button.primary');
    await page.waitForSelector('.canvas-area canvas');

    // 3. upload + analyse
    await page.fill('#colorCount', String(entry.colorCount)).catch(() => {});
    await page.setInputFiles('.panel.left input[type=file]', artworkSrc);
    await page.waitForSelector('.suitability', { timeout: 40000 });

    // 4. digitize happens automatically on upload
    await page.waitForFunction(
      () =>
        document.querySelectorAll('.object-list li').length > 0 &&
        !document.querySelector('.object-list li')?.textContent?.includes('No objects'),
      { timeout: 90000 },
    );

    // 5. capture the app's own stitch preview
    await page.click('button:has-text("Fit to hoop")');
    await page.waitForTimeout(400);
    const canvas = page.locator('.canvas-area canvas');
    await canvas.screenshot({ path: join(outDir, 'preview.png') });

    // 6. read the status panel
    await page.click('.tabs button:has-text("Status")');
    await page.waitForSelector('.tier');
    const status = await page.evaluate(() => {
      const text = (sel) => document.querySelector(sel)?.textContent?.trim() ?? '';
      const rows = {};
      document.querySelectorAll('.panel.right .stat-grid').forEach((grid) => {
        const dts = [...grid.querySelectorAll('dt')];
        const dds = [...grid.querySelectorAll('dd')];
        dts.forEach((dt, i) => {
          const key = dt.textContent.trim();
          if (key && !rows[key]) rows[key] = dds[i]?.textContent?.trim() ?? '';
        });
      });
      return { rows, readout: text('.canvas-readout') };
    });

    // 7. export (acknowledge warnings if any)
    await page.click('.tabs button:has-text("Validate")');
    const ack = page.locator('.section:has-text("Export PES") input[type=checkbox]');
    if (await ack.isEnabled()) await ack.check();
    const exportButton = page.locator('button:has-text("Export .pes")');
    if (await exportButton.isDisabled()) {
      const issues = await page.locator('.issue.ERROR').allTextContents();
      fail(`${entry.id}: export disabled — ${issues.join(' | ') || 'unknown reason'}`);
    }
    const [download] = await Promise.all([
      page.waitForEvent('download', { timeout: 30000 }),
      exportButton.click(),
    ]);
    const pesPath = join(outDir, 'design.pes');
    await download.saveAs(pesPath);

    const pes = readFileSync(pesPath);
    if (pes.subarray(0, 8).toString('ascii') !== '#PES0001') {
      fail(`${entry.id}: exported file is not a PES file`);
    }
    if (await page.locator('.check-list li.failed').count()) {
      fail(`${entry.id}: a post-export verification check failed`);
    }

    // 8. worksheet, straight from the app
    await page.click('.tabs button:has-text("Status")');
    const [wsDownload] = await Promise.all([
      page.waitForEvent('download', { timeout: 20000 }),
      page.click('button:has-text("Save stitch-out worksheet")'),
    ]);
    await wsDownload.saveAs(join(outDir, 'worksheet.txt'));

    const [rpDownload] = await Promise.all([
      page.waitForEvent('download', { timeout: 20000 }),
      page.click('button:has-text("Save readiness report")'),
    ]);
    await rpDownload.saveAs(join(outDir, 'readiness.txt'));

    // 9. independent verification of the bytes on disk
    const independent = verifyWithPyembroidery(pesPath);

    const record = {
      id: entry.id,
      name: entry.name,
      proves: entry.proves,
      requestedMm: { width: entry.widthMm, height: entry.heightMm },
      hoopId: entry.hoopId,
      status: status.rows,
      canvasReadout: status.readout,
      pesBytes: pes.length,
      independentlyVerified: independent,
      machineValidated: false,
      machineValidationNote:
        'NOT PERFORMED. Complete worksheet.txt on a physical machine and record the result here.',
    };
    writeFileSync(join(outDir, 'report.json'), `${JSON.stringify(record, null, 2)}\n`);
    results.push(record);

    log(
      `${entry.id}: ${independent.stitches} stitches, ${independent.colors} colour stop(s), ` +
        `${independent.widthMm}x${independent.heightMm} mm, ${(pes.length / 1024).toFixed(1)} kB`,
    );
  }

  // --- index -------------------------------------------------------------
  const md = [];
  md.push('# Physical validation packet');
  md.push('');
  md.push(`Generated ${new Date().toISOString()} from the built application.`);
  md.push('');
  md.push('Each design below has four artifacts to compare: the original artwork, the');
  md.push("stitch preview captured from the application's own canvas, the exported PES");
  md.push('file, and — once you have sewn it — the physical stitch-out recorded on the');
  md.push('worksheet.');
  md.push('');
  md.push('**No design here has been sewn on a physical machine.** The `machineValidated`');
  md.push('field in every `report.json` is `false` and stays false until an operator');
  md.push('completes a worksheet. See `docs/PHYSICAL-VALIDATION.md`.');
  md.push('');
  md.push('| Design | Stitches | Colour stops | Size (mm) | PES | Machine |');
  md.push('|---|---:|---:|---|---:|---|');
  for (const r of results) {
    md.push(
      `| [${r.name}](${r.id}/) | ${r.independentlyVerified.stitches} | ${r.independentlyVerified.colors} | ` +
        `${r.independentlyVerified.widthMm} × ${r.independentlyVerified.heightMm} | ` +
        `${(r.pesBytes / 1024).toFixed(1)} kB | NOT VERIFIED |`,
    );
  }
  md.push('');
  for (const r of results) {
    md.push(`## ${r.name}`);
    md.push('');
    md.push(`${r.proves}`);
    md.push('');
    md.push(`- artwork: \`${r.id}/artwork.png\``);
    md.push(`- preview: \`${r.id}/preview.png\``);
    md.push(`- PES: \`${r.id}/design.pes\` (verified by pyembroidery: ${r.independentlyVerified.stitches} stitches, colours ${r.independentlyVerified.colorList.join(' ')})`);
    md.push(`- worksheet: \`${r.id}/worksheet.txt\``);
    md.push('- physical stitch-out: **not performed**');
    md.push('');
  }
  writeFileSync(join(outRoot, 'REPORT.md'), `${md.join('\n')}\n`);

  if (consoleErrors.length) {
    console.error('✗ console errors:', consoleErrors.slice(0, 10));
    process.exitCode = 1;
  }
  console.log(`\nWrote ${results.length} validation packets to ${outRoot}`);
  console.log('Machine validation status for all designs: NOT PERFORMED.');
} catch (err) {
  await page.screenshot({ path: join(outRoot, 'failure.png') }).catch(() => {});
  console.error('\nFIXTURE BUILD FAILED:', err.message);
  if (consoleErrors.length) console.error('console errors:', consoleErrors.slice(0, 10));
  process.exitCode = 1;
} finally {
  await browser.close();
}

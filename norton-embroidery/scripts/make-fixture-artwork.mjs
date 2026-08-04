/**
 * Generate the six controlled test designs as real PNG files.
 *
 *   node scripts/make-fixture-artwork.mjs
 *
 * The PNGs are committed. This script exists so they can be regenerated
 * identically, not so they are rebuilt on every test run: the committed files
 * are the single source of truth, and both the regression suite and the browser
 * fixture runner read them from disk.
 *
 * Every design is drawn from flat colour with hard edges, because that is what
 * an embroidery digitizer is supposed to handle well. They get progressively
 * harder so a physical failure points at a specific part of the engine.
 */

import { PNG } from 'pngjs';
import { mkdirSync, writeFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const outDir = join(here, '..', 'fixtures', 'artwork');
mkdirSync(outDir, { recursive: true });

const WHITE = [255, 255, 255];
const BLACK = [20, 20, 20];
const RED = [214, 30, 40];
const BLUE = [20, 60, 180];
const GREEN = [30, 140, 70];
const YELLOW = [250, 200, 30];

function canvas(width, height, background = WHITE) {
  const png = new PNG({ width, height });
  const data = png.data;
  for (let i = 0; i < width * height; i++) {
    data[i * 4] = background[0];
    data[i * 4 + 1] = background[1];
    data[i * 4 + 2] = background[2];
    data[i * 4 + 3] = 255;
  }
  png.__w = width;
  png.__h = height;
  return png;
}

const put = (png, x, y, c) => {
  if (x < 0 || y < 0 || x >= png.__w || y >= png.__h) return;
  const i = (y * png.__w + x) * 4;
  png.data[i] = c[0];
  png.data[i + 1] = c[1];
  png.data[i + 2] = c[2];
  png.data[i + 3] = 255;
};

const rect = (png, x0, y0, w, h, c) => {
  for (let y = y0; y < y0 + h; y++) for (let x = x0; x < x0 + w; x++) put(png, x, y, c);
};

const disc = (png, cx, cy, r, c) => {
  for (let y = Math.floor(cy - r); y <= Math.ceil(cy + r); y++) {
    for (let x = Math.floor(cx - r); x <= Math.ceil(cx + r); x++) {
      if ((x - cx) ** 2 + (y - cy) ** 2 <= r * r) put(png, x, y, c);
    }
  }
};

const ring = (png, cx, cy, outer, inner, c) => {
  for (let y = Math.floor(cy - outer); y <= Math.ceil(cy + outer); y++) {
    for (let x = Math.floor(cx - outer); x <= Math.ceil(cx + outer); x++) {
      const d = (x - cx) ** 2 + (y - cy) ** 2;
      if (d <= outer * outer && d >= inner * inner) put(png, x, y, c);
    }
  }
};

/** A bar of the given thickness between two points. */
const bar = (png, x0, y0, x1, y1, thickness, c) => {
  const steps = Math.ceil(Math.hypot(x1 - x0, y1 - y0)) * 2;
  const half = thickness / 2;
  for (let s = 0; s <= steps; s++) {
    const t = s / steps;
    const cx = x0 + (x1 - x0) * t;
    const cy = y0 + (y1 - y0) * t;
    for (let dy = -half; dy <= half; dy += 0.5) {
      for (let dx = -half; dx <= half; dx += 0.5) {
        if (dx * dx + dy * dy <= half * half) put(png, Math.round(cx + dx), Math.round(cy + dy), c);
      }
    }
  }
};

const designs = [];

// 1 — the simplest thing that can be sewn: one flat shape, one colour.
{
  const png = canvas(240, 240);
  rect(png, 50, 50, 140, 140, BLUE);
  designs.push(['01-single-color-square', png]);
}

// 2 — two flat shapes, two colours: exactly one colour change.
{
  const png = canvas(280, 200);
  disc(png, 95, 100, 62, RED);
  rect(png, 160, 45, 90, 110, BLUE);
  designs.push(['02-two-color-logo', png]);
}

// 3 — four colours in separate areas: colour grouping and several changes.
{
  const png = canvas(300, 220);
  disc(png, 80, 80, 50, RED);
  disc(png, 210, 80, 50, BLUE);
  disc(png, 80, 165, 40, GREEN);
  disc(png, 210, 165, 40, YELLOW);
  rect(png, 130, 95, 40, 40, BLACK);
  designs.push(['03-multi-color-logo', png]);
}

// 4 — holes and cut-outs: the needle must travel around the openings rather
// than stitching across them. Every shape here is a closed region with at
// least one hole in it; nothing is cut into open arcs, because an open arc
// would not exercise hole handling at all.
{
  const png = canvas(260, 260);
  // A green annulus: one outer ring, one hole.
  ring(png, 130, 130, 112, 66, GREEN);
  // Three punched-out windows in the green band.
  disc(png, 130, 42, 13, WHITE);
  disc(png, 42, 168, 13, WHITE);
  disc(png, 218, 168, 13, WHITE);
  // A red disc in the middle with two holes of its own.
  disc(png, 130, 130, 50, RED);
  disc(png, 113, 122, 11, WHITE);
  disc(png, 149, 143, 11, WHITE);
  designs.push(['04-holes-and-cutouts', png]);
}

// 5 — narrow shapes that must become satin columns, not fills.
{
  const png = canvas(320, 200);
  bar(png, 30, 40, 290, 40, 9, BLACK);
  bar(png, 30, 80, 290, 80, 14, RED);
  bar(png, 30, 125, 290, 125, 20, BLUE);
  bar(png, 40, 165, 280, 165, 6, GREEN);
  designs.push(['05-narrow-satin', png]);
}

// 6 — the realistic worst case: a badge with an outline, a bar across it,
// small counters, and a colour that is used, left, and returned to.
{
  const png = canvas(320, 320);
  disc(png, 160, 160, 140, BLACK);
  disc(png, 160, 160, 124, YELLOW);
  disc(png, 160, 160, 96, BLUE);
  rect(png, 40, 142, 240, 36, RED);
  // Small black counters sitting on top of the red bar: black returns.
  rect(png, 78, 152, 16, 16, BLACK);
  rect(png, 152, 152, 16, 16, BLACK);
  rect(png, 226, 152, 16, 16, BLACK);
  // A thin green rule near the bottom.
  bar(png, 70, 250, 250, 250, 8, GREEN);
  designs.push(['06-detailed-logo', png]);
}

for (const [name, png] of designs) {
  const file = join(outDir, `${name}.png`);
  writeFileSync(file, PNG.sync.write(png));
  console.log(`wrote ${file} (${png.__w}x${png.__h})`);
}
console.log(`\n${designs.length} fixture artwork files written to ${outDir}`);

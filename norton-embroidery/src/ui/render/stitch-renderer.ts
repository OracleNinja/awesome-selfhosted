/**
 * Stitch renderer.
 *
 * This draws the generated stitch data — every needle penetration, in sew
 * order, in its thread colour. It never draws the source artwork, so what the
 * operator sees is what the machine will sew.
 */

import { StitchCommand, colorBlocks, type Stitch } from '../../domain/stitch';
import { threadHex, type Thread } from '../../domain/thread';
import type { Hoop } from '../../domain/machine';
import { unitsToMm } from '../../domain/units';

export interface Viewport {
  /** Screen pixels per design unit (0.1 mm). */
  scale: number;
  /** Design-unit coordinate drawn at the canvas origin. */
  offsetX: number;
  offsetY: number;
}

export interface RenderOptions {
  viewport: Viewport;
  /**
   * Thread for each colour block, in sew order — NOT the design palette. A
   * design that returns to a colour has more blocks than palette entries, so
   * indexing a palette by block ordinal draws the wrong thread.
   */
  blockThreads: readonly Thread[];
  hoop: Hoop | null;
  /** Draw only the first N stitches. -1 draws everything. */
  upTo: number;
  showJumps: boolean;
  showPenetrations: boolean;
  showHoop: boolean;
  showSafeArea: boolean;
  showGrid: boolean;
  /** Highlight the stitches belonging to these objects. */
  highlightRange: { start: number; end: number } | null;
  darkBackground: boolean;
}

export const defaultViewport = (): Viewport => ({ scale: 0.6, offsetX: 0, offsetY: 0 });

export function designToScreen(p: { x: number; y: number }, v: Viewport): { x: number; y: number } {
  return { x: (p.x - v.offsetX) * v.scale, y: (p.y - v.offsetY) * v.scale };
}

export function screenToDesign(p: { x: number; y: number }, v: Viewport): { x: number; y: number } {
  return { x: p.x / v.scale + v.offsetX, y: p.y / v.scale + v.offsetY };
}

/** Fit a bounding box into the canvas with a margin, returning a viewport. */
export function fitViewport(
  box: { minX: number; minY: number; maxX: number; maxY: number },
  canvasWidth: number,
  canvasHeight: number,
  marginPx = 32,
): Viewport {
  const w = Math.max(1, box.maxX - box.minX);
  const h = Math.max(1, box.maxY - box.minY);
  const scale = Math.min((canvasWidth - marginPx * 2) / w, (canvasHeight - marginPx * 2) / h);
  const safeScale = Number.isFinite(scale) && scale > 0 ? scale : 1;
  return {
    scale: safeScale,
    offsetX: (box.minX + box.maxX) / 2 - canvasWidth / 2 / safeScale,
    offsetY: (box.minY + box.maxY) / 2 - canvasHeight / 2 / safeScale,
  };
}

export function render(
  ctx: CanvasRenderingContext2D,
  stitches: readonly Stitch[],
  options: RenderOptions,
): void {
  const { viewport: v, blockThreads } = options;
  const canvas = ctx.canvas;
  const width = canvas.width / (window.devicePixelRatio || 1);
  const height = canvas.height / (window.devicePixelRatio || 1);

  ctx.save();
  ctx.clearRect(0, 0, width, height);
  ctx.fillStyle = options.darkBackground ? '#1a1d21' : '#f4f2ee';
  ctx.fillRect(0, 0, width, height);

  if (options.showGrid) drawGrid(ctx, v, width, height, options.darkBackground);
  if (options.showHoop && options.hoop) drawHoop(ctx, options.hoop, v, options.showSafeArea, options.darkBackground);

  const limit = options.upTo < 0 ? stitches.length : Math.min(options.upTo, stitches.length);
  const blocks = colorBlocks(stitches);

  // --- jumps, drawn under the stitching ---------------------------------
  if (options.showJumps) {
    ctx.save();
    ctx.strokeStyle = options.darkBackground ? 'rgba(255,120,120,0.8)' : 'rgba(200,0,0,0.65)';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    for (let i = 1; i < limit; i++) {
      if (stitches[i].command !== StitchCommand.Jump) continue;
      const a = designToScreen(stitches[i - 1], v);
      const b = designToScreen(stitches[i], v);
      ctx.moveTo(a.x, a.y);
      ctx.lineTo(b.x, b.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  // --- the stitching itself ---------------------------------------------
  // Thread thickness: 40-weight embroidery thread is about 0.4 mm across.
  const threadWidth = Math.max(0.8, 4 * v.scale);

  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';

  for (let blockIndex = 0; blockIndex < blocks.length; blockIndex++) {
    const block = blocks[blockIndex];
    if (block.start >= limit) break;
    const thread = blockThreads[blockIndex];
    const color = thread ? threadHex(thread) : '#888888';
    const end = Math.min(block.endExclusive, limit);

    ctx.strokeStyle = color;
    ctx.lineWidth = threadWidth;
    ctx.beginPath();
    let penDown = false;
    for (let i = block.start; i < end; i++) {
      const s = stitches[i];
      if (s.command !== StitchCommand.Stitch) {
        penDown = false;
        continue;
      }
      const p = designToScreen(s, v);
      if (!penDown) {
        ctx.moveTo(p.x, p.y);
        penDown = true;
      } else {
        ctx.lineTo(p.x, p.y);
      }
    }
    ctx.stroke();

    // Needle penetrations, only when zoomed in far enough to be meaningful.
    if (options.showPenetrations && v.scale > 0.7) {
      ctx.fillStyle = options.darkBackground ? 'rgba(255,255,255,0.55)' : 'rgba(0,0,0,0.45)';
      for (let i = block.start; i < end; i++) {
        const s = stitches[i];
        if (s.command !== StitchCommand.Stitch) continue;
        const p = designToScreen(s, v);
        ctx.fillRect(p.x - 0.5, p.y - 0.5, 1, 1);
      }
    }
  }

  // --- selection highlight ----------------------------------------------
  if (options.highlightRange) {
    const { start, end } = options.highlightRange;
    ctx.save();
    ctx.strokeStyle = '#2f6fed';
    ctx.lineWidth = threadWidth + 2;
    ctx.globalAlpha = 0.45;
    ctx.beginPath();
    let penDown = false;
    for (let i = start; i < Math.min(end, limit); i++) {
      const s = stitches[i];
      if (s.command !== StitchCommand.Stitch) {
        penDown = false;
        continue;
      }
      const p = designToScreen(s, v);
      if (!penDown) {
        ctx.moveTo(p.x, p.y);
        penDown = true;
      } else ctx.lineTo(p.x, p.y);
    }
    ctx.stroke();
    ctx.restore();
  }

  // --- needle position during simulation ---------------------------------
  if (options.upTo >= 0 && limit > 0 && limit < stitches.length) {
    const s = stitches[limit - 1];
    const p = designToScreen(s, v);
    ctx.save();
    ctx.strokeStyle = '#111';
    ctx.fillStyle = '#ffd23f';
    ctx.lineWidth = 1.5;
    ctx.beginPath();
    ctx.arc(p.x, p.y, 5, 0, Math.PI * 2);
    ctx.fill();
    ctx.stroke();
    ctx.restore();
  }

  ctx.restore();
}

function drawHoop(
  ctx: CanvasRenderingContext2D,
  hoop: Hoop,
  v: Viewport,
  showSafeArea: boolean,
  dark: boolean,
): void {
  const topLeft = designToScreen({ x: 0, y: 0 }, v);
  const bottomRight = designToScreen({ x: hoop.width, y: hoop.height }, v);

  ctx.save();
  ctx.fillStyle = dark ? '#24282d' : '#ffffff';
  ctx.fillRect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y);

  ctx.strokeStyle = dark ? '#5c6572' : '#9aa2ad';
  ctx.lineWidth = 2;
  ctx.strokeRect(topLeft.x, topLeft.y, bottomRight.x - topLeft.x, bottomRight.y - topLeft.y);

  if (showSafeArea) {
    const m = hoop.safetyMargin;
    const a = designToScreen({ x: m, y: m }, v);
    const b = designToScreen({ x: hoop.width - m, y: hoop.height - m }, v);
    ctx.strokeStyle = dark ? 'rgba(255,200,80,0.55)' : 'rgba(200,140,0,0.6)';
    ctx.setLineDash([6, 4]);
    ctx.lineWidth = 1;
    ctx.strokeRect(a.x, a.y, b.x - a.x, b.y - a.y);
    ctx.setLineDash([]);
  }

  // Centre cross-hairs: operators line these up with the hoop markings.
  const c = designToScreen({ x: hoop.width / 2, y: hoop.height / 2 }, v);
  ctx.strokeStyle = dark ? 'rgba(255,255,255,0.2)' : 'rgba(0,0,0,0.18)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  ctx.moveTo(c.x - 12, c.y);
  ctx.lineTo(c.x + 12, c.y);
  ctx.moveTo(c.x, c.y - 12);
  ctx.lineTo(c.x, c.y + 12);
  ctx.stroke();
  ctx.restore();
}

function drawGrid(
  ctx: CanvasRenderingContext2D,
  v: Viewport,
  width: number,
  height: number,
  dark: boolean,
): void {
  // 10 mm grid; skip it when it would be denser than 8 px.
  const stepUnits = 100;
  const stepPx = stepUnits * v.scale;
  if (stepPx < 8) return;

  ctx.save();
  ctx.strokeStyle = dark ? 'rgba(255,255,255,0.06)' : 'rgba(0,0,0,0.06)';
  ctx.lineWidth = 1;
  ctx.beginPath();
  const startX = Math.floor(v.offsetX / stepUnits) * stepUnits;
  for (let x = startX; ; x += stepUnits) {
    const sx = (x - v.offsetX) * v.scale;
    if (sx > width) break;
    if (sx >= 0) {
      ctx.moveTo(sx, 0);
      ctx.lineTo(sx, height);
    }
  }
  const startY = Math.floor(v.offsetY / stepUnits) * stepUnits;
  for (let y = startY; ; y += stepUnits) {
    const sy = (y - v.offsetY) * v.scale;
    if (sy > height) break;
    if (sy >= 0) {
      ctx.moveTo(0, sy);
      ctx.lineTo(width, sy);
    }
  }
  ctx.stroke();
  ctx.restore();
}

/** Draw a small colour-sequence strip, used by the timeline. */
export function renderTimeline(
  ctx: CanvasRenderingContext2D,
  stitches: readonly Stitch[],
  blockThreads: readonly Thread[],
  progress: number,
): void {
  const canvas = ctx.canvas;
  const dpr = window.devicePixelRatio || 1;
  const width = canvas.width / dpr;
  const height = canvas.height / dpr;
  ctx.clearRect(0, 0, width, height);
  if (stitches.length === 0) return;

  const blocks = colorBlocks(stitches);
  blocks.forEach((block, blockIndex) => {
    const x0 = (block.start / stitches.length) * width;
    const x1 = (block.endExclusive / stitches.length) * width;
    const thread = blockThreads[blockIndex];
    ctx.fillStyle = thread ? threadHex(thread) : '#888';
    ctx.fillRect(x0, 0, Math.max(1, x1 - x0), height);
  });

  const px = progress * width;
  ctx.fillStyle = 'rgba(0,0,0,0.35)';
  ctx.fillRect(px, 0, width - px, height);
  ctx.fillStyle = '#ffffff';
  ctx.fillRect(px - 1, 0, 2, height);
}

export function formatMm(units: number): string {
  return `${unitsToMm(units).toFixed(1)} mm`;
}

import { describe, expect, it } from 'vitest';
import { execFileSync } from 'node:child_process';
import { mkdtempSync, writeFileSync, readFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { writePes } from '../src/infra/pes/pes-writer';
import { readPes } from '../src/infra/pes/pes-reader';
import { buildUniquePalette, nearestPecIndex } from '../src/infra/pes/pec';
import { PEC_THREADS, type Thread } from '../src/domain/thread';
import { StitchCommand, type Stitch } from '../src/domain/stitch';
import { mmToUnits } from '../src/domain/units';

const black = PEC_THREADS.find((t) => t.name === 'Black')!;
const red = PEC_THREADS.find((t) => t.name === 'Red')!;
const yellow = PEC_THREADS.find((t) => t.name === 'Yellow')!;

/** A 20 mm square outline, one colour. */
function squareStitches(): Stitch[] {
  const s = mmToUnits(20);
  const out: Stitch[] = [{ x: 0, y: 0, command: StitchCommand.Jump }];
  const step = mmToUnits(2);
  for (let x = 0; x <= s; x += step) out.push({ x, y: 0, command: StitchCommand.Stitch });
  for (let y = step; y <= s; y += step) out.push({ x: s, y, command: StitchCommand.Stitch });
  for (let x = s - step; x >= 0; x -= step) out.push({ x, y: s, command: StitchCommand.Stitch });
  for (let y = s - step; y >= 0; y -= step) out.push({ x: 0, y, command: StitchCommand.Stitch });
  out.push({ x: 0, y: 0, command: StitchCommand.End });
  return out;
}

/** Two colour blocks separated by a colour change. */
function twoColorStitches(): Stitch[] {
  const out: Stitch[] = [{ x: 0, y: 0, command: StitchCommand.Jump }];
  for (let i = 0; i <= 20; i++) out.push({ x: i * 20, y: 0, command: StitchCommand.Stitch });
  out.push({ x: 400, y: 0, command: StitchCommand.ColorChange });
  out.push({ x: 400, y: 200, command: StitchCommand.Jump });
  for (let i = 0; i <= 20; i++) out.push({ x: 400 - i * 20, y: 200, command: StitchCommand.Stitch });
  out.push({ x: 0, y: 200, command: StitchCommand.End });
  return out;
}

const countStitches = (s: readonly Stitch[]): number =>
  s.filter((x) => x.command === StitchCommand.Stitch).length;

/** Write bytes to a temp file and inspect them with pyembroidery. */
function inspectWithPyembroidery(bytes: Uint8Array): {
  stitch_count: number;
  color_count: number;
  extents: [number, number, number, number];
  thread_colors: string[];
  command_counts: Record<string, number>;
} {
  const dir = mkdtempSync(join(tmpdir(), 'norton-pes-'));
  const file = join(dir, 'design.pes');
  writeFileSync(file, bytes);

  const script = `
import json, sys
import pyembroidery
from pyembroidery.EmbConstant import STITCH, JUMP, TRIM, COLOR_CHANGE, END, COMMAND_MASK, STOP

p = pyembroidery.read(sys.argv[1])
counts = {}
for s in p.stitches:
    c = s[2] & COMMAND_MASK
    name = {STITCH: "stitch", JUMP: "jump", TRIM: "trim", COLOR_CHANGE: "color_change", END: "end", STOP: "stop"}.get(c, str(c))
    counts[name] = counts.get(name, 0) + 1
b = p.bounds()
print(json.dumps({
    "stitch_count": counts.get("stitch", 0),
    "color_count": len(p.threadlist),
    "extents": [b[0], b[1], b[2], b[3]],
    "thread_colors": ["#%06X" % (t.color & 0xFFFFFF) for t in p.threadlist],
    "command_counts": counts,
}))
`;
  const scriptFile = join(dir, 'inspect.py');
  writeFileSync(scriptFile, script);
  const out = execFileSync('python3', [scriptFile, file], { encoding: 'utf8' });
  return JSON.parse(out);
}

describe('PEC palette mapping', () => {
  it('maps a thread to its own palette slot', () => {
    expect(nearestPecIndex(black)).toBe(20);
    expect(nearestPecIndex(red)).toBe(5);
    expect(nearestPecIndex(yellow)).toBe(13);
  });

  it('never assigns two different colours to the same machine slot', () => {
    const palette = buildUniquePalette([black, red, yellow]);
    expect(new Set(palette).size).toBe(3);
  });

  it('reuses the slot when the same colour repeats', () => {
    const palette = buildUniquePalette([black, red, black]);
    expect(palette[0]).toBe(palette[2]);
    expect(palette[0]).not.toBe(palette[1]);
  });
});

describe('PES file structure', () => {
  const bytes = writePes(squareStitches(), [black], { name: 'Square' });

  it('starts with the PES version 1 signature', () => {
    expect(String.fromCharCode(...bytes.subarray(0, 8))).toBe('#PES0001');
  });

  it('points at a PEC block that lies inside the file', () => {
    const offset = bytes[8] | (bytes[9] << 8) | (bytes[10] << 16) | (bytes[11] << 24);
    expect(offset).toBeGreaterThan(12);
    expect(offset).toBeLessThan(bytes.length);
    // The PEC block always opens with the "LA:" label.
    expect(String.fromCharCode(bytes[offset], bytes[offset + 1], bytes[offset + 2])).toBe('LA:');
  });

  it('is a binary file, not text pretending to be one', () => {
    const nonPrintable = [...bytes].filter((b) => b < 0x20 || b > 0x7e).length;
    expect(nonPrintable / bytes.length).toBeGreaterThan(0.3);
    const text = Buffer.from(bytes).toString('utf8');
    expect(text.trimStart().startsWith('{')).toBe(false);
    expect(text.trimStart().startsWith('<')).toBe(false);
  });

  it('contains the CEmbOne / CSewSeg vector section', () => {
    const text = Buffer.from(bytes).toString('latin1');
    expect(text).toContain('CEmbOne');
    expect(text).toContain('CSewSeg');
  });
});

describe('PES export round-trips through our own reader', () => {
  it('preserves stitch count, colours and geometry for a single colour', () => {
    const stitches = squareStitches();
    const bytes = writePes(stitches, [black], { name: 'Square' });
    const back = readPes(bytes);

    expect(back.format).toBe('PES');
    expect(countStitches(back.stitches)).toBe(countStitches(stitches));
    expect(back.threads.length).toBe(1);
    expect(back.threads[0].name).toBe('Black');

    const xs = back.stitches.filter((s) => s.command === StitchCommand.Stitch);
    const width = Math.max(...xs.map((s) => s.x)) - Math.min(...xs.map((s) => s.x));
    const height = Math.max(...xs.map((s) => s.y)) - Math.min(...xs.map((s) => s.y));
    expect(width).toBeCloseTo(mmToUnits(20), 0);
    expect(height).toBeCloseTo(mmToUnits(20), 0);
  });

  it('preserves the colour sequence across a colour change', () => {
    const stitches = twoColorStitches();
    const bytes = writePes(stitches, [red, yellow], { name: 'TwoCol' });
    const back = readPes(bytes);

    expect(back.threads.length).toBe(2);
    expect(back.threads.map((t) => t.name)).toEqual(['Red', 'Yellow']);
    expect(back.stitches.filter((s) => s.command === StitchCommand.ColorChange).length).toBe(1);
    expect(countStitches(back.stitches)).toBe(countStitches(stitches));
  });

  it('rejects a file that is not PES', () => {
    expect(() => readPes(new Uint8Array([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13]))).toThrow(/Not a PES file/);
  });

  it('reports a truncated file rather than reading garbage', () => {
    const bytes = writePes(squareStitches(), [black], { name: 'Square' });
    const truncated = bytes.subarray(0, 200);
    expect(() => readPes(truncated)).toThrow(/truncated or corrupt|outside the/);
  });
});

describe('PES export verified by pyembroidery (independent implementation)', () => {
  it('is read correctly by pyembroidery for a single-colour design', () => {
    const stitches = squareStitches();
    const bytes = writePes(stitches, [black], { name: 'Square' });
    const info = inspectWithPyembroidery(bytes);

    expect(info.stitch_count).toBe(countStitches(stitches));
    expect(info.color_count).toBe(1);
    expect(info.thread_colors[0]).toBe('#000000');

    const [minX, minY, maxX, maxY] = info.extents;
    expect(maxX - minX).toBeCloseTo(mmToUnits(20), 0);
    expect(maxY - minY).toBeCloseTo(mmToUnits(20), 0);
  });

  it('is read correctly by pyembroidery for a two-colour design', () => {
    const stitches = twoColorStitches();
    const bytes = writePes(stitches, [red, yellow], { name: 'TwoCol' });
    const info = inspectWithPyembroidery(bytes);

    expect(info.stitch_count).toBe(countStitches(stitches));
    expect(info.color_count).toBe(2);
    expect(info.thread_colors).toEqual(['#ED171F', '#FFFF00']);
  });

  it('encodes trims as trim commands pyembroidery recognises', () => {
    const stitches: Stitch[] = [
      { x: 0, y: 0, command: StitchCommand.Jump },
      { x: 0, y: 0, command: StitchCommand.Stitch },
      { x: 200, y: 0, command: StitchCommand.Stitch },
      // A long move after stitching: this is a trim in PEC.
      { x: 900, y: 900, command: StitchCommand.Jump },
      { x: 900, y: 900, command: StitchCommand.Stitch },
      { x: 1100, y: 900, command: StitchCommand.Stitch },
      { x: 1100, y: 900, command: StitchCommand.End },
    ];
    const bytes = writePes(stitches, [black], { name: 'Trims' });
    const info = inspectWithPyembroidery(bytes);
    expect(info.command_counts.trim ?? 0).toBeGreaterThanOrEqual(1);
    expect(info.stitch_count).toBe(countStitches(stitches));
  });

  it('handles a long jump that exceeds the 7-bit encoding', () => {
    const stitches: Stitch[] = [
      { x: 0, y: 0, command: StitchCommand.Jump },
      { x: 0, y: 0, command: StitchCommand.Stitch },
      { x: 50, y: 50, command: StitchCommand.Stitch },
      { x: 1200, y: 1700, command: StitchCommand.Jump },
      { x: 1200, y: 1700, command: StitchCommand.Stitch },
      { x: 1250, y: 1750, command: StitchCommand.Stitch },
      { x: 1250, y: 1750, command: StitchCommand.End },
    ];
    const bytes = writePes(stitches, [black], { name: 'LongJmp' });
    const info = inspectWithPyembroidery(bytes);
    const [minX, minY, maxX, maxY] = info.extents;
    expect(maxX - minX).toBeCloseTo(1250, 0);
    expect(maxY - minY).toBeCloseTo(1750, 0);
  });

  it('agrees with pyembroidery on a design with many colour blocks', () => {
    const threads: Thread[] = [black, red, yellow, PEC_THREADS.find((t) => t.name === 'Blue')!];
    const stitches: Stitch[] = [{ x: 0, y: 0, command: StitchCommand.Jump }];
    for (let block = 0; block < threads.length; block++) {
      for (let i = 0; i <= 10; i++) {
        stitches.push({ x: i * 20, y: block * 100, command: StitchCommand.Stitch });
      }
      if (block < threads.length - 1) {
        stitches.push({ x: 200, y: block * 100, command: StitchCommand.ColorChange });
        stitches.push({ x: 0, y: (block + 1) * 100, command: StitchCommand.Jump });
      }
    }
    stitches.push({ x: 200, y: 300, command: StitchCommand.End });

    const bytes = writePes(stitches, threads, { name: 'Multi' });
    const info = inspectWithPyembroidery(bytes);
    expect(info.color_count).toBe(4);
    expect(info.stitch_count).toBe(countStitches(stitches));
    expect(info.thread_colors).toEqual(['#000000', '#ED171F', '#FFFF00', '#0A55A3']);
  });
});

describe('PES import of files written by other software', () => {
  /** Ask pyembroidery to write a PES file, then read it with our reader. */
  function pyembroideryPes(version: string): { bytes: Uint8Array; stitches: number; colors: number } {
    const dir = mkdtempSync(join(tmpdir(), 'norton-foreign-'));
    const file = join(dir, `foreign-v${version}.pes`);
    const script = `
import sys, json, pyembroidery
p = pyembroidery.EmbPattern()
p.add_block([(0,0),(100,0),(100,100),(0,100),(0,0)], "red")
p.add_block([(200,200),(300,200),(300,300),(200,300)], "blue")
pyembroidery.write(p, sys.argv[1], {"version": sys.argv[2]})
q = pyembroidery.read(sys.argv[1])
from pyembroidery.EmbConstant import STITCH, COMMAND_MASK
n = sum(1 for s in q.stitches if (s[2] & COMMAND_MASK) == STITCH)
print(json.dumps({"stitches": n, "colors": len(q.threadlist)}))
`;
    const scriptFile = join(dir, 'w.py');
    writeFileSync(scriptFile, script);
    const out = execFileSync('python3', [scriptFile, file, version], { encoding: 'utf8' });
    const info = JSON.parse(out);
    return { bytes: new Uint8Array(readFileSync(file)), ...info };
  }

  for (const version of ['1', '6']) {
    it(`reads a PES version ${version} file produced by pyembroidery`, () => {
      const { bytes, stitches, colors } = pyembroideryPes(version);
      const design = readPes(bytes);

      expect(design.format).toBe('PES');
      expect(countStitches(design.stitches)).toBe(stitches);
      expect(design.threads.length).toBe(colors);
      // Every imported thread must be a real chart entry, not a placeholder.
      for (const t of design.threads) expect(t.manufacturer).toBe('Brother');
      // The reader must be explicit that shapes were not recovered.
      expect(design.limitations.join(' ')).toMatch(/not recovered/i);
    });
  }

  it('reads a bare PEC file', () => {
    const dir = mkdtempSync(join(tmpdir(), 'norton-pec-'));
    const file = join(dir, 'design.pec');
    const script = `
import sys, pyembroidery
p = pyembroidery.EmbPattern()
p.add_block([(0,0),(150,0),(150,150),(0,150),(0,0)], "green")
p.write(sys.argv[1])
`;
    const scriptFile = join(dir, 'w.py');
    writeFileSync(scriptFile, script);
    execFileSync('python3', [scriptFile, file]);

    const design = readPes(new Uint8Array(readFileSync(file)));
    expect(design.format).toBe('PEC');
    expect(countStitches(design.stitches)).toBeGreaterThan(0);
  });
});

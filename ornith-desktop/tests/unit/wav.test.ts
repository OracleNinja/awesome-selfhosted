import { describe, expect, it } from 'vitest';
import { downsample, encodeWav, peakAmplitude, SILENCE_THRESHOLD } from '../../shared/wav';

function readAscii(bytes: Uint8Array, offset: number, length: number): string {
  return String.fromCharCode(...bytes.slice(offset, offset + length));
}

function view(bytes: Uint8Array): DataView {
  return new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
}

describe('encodeWav', () => {
  it('writes a valid RIFF/WAVE header', () => {
    const wav = encodeWav(new Float32Array([0, 0.5, -0.5]), 16000);
    expect(readAscii(wav, 0, 4)).toBe('RIFF');
    expect(readAscii(wav, 8, 4)).toBe('WAVE');
    expect(readAscii(wav, 12, 4)).toBe('fmt ');
    expect(readAscii(wav, 36, 4)).toBe('data');
  });

  it('declares 16-bit mono PCM at the given rate', () => {
    const v = view(encodeWav(new Float32Array(10), 16000));
    expect(v.getUint16(20, true)).toBe(1); // PCM
    expect(v.getUint16(22, true)).toBe(1); // mono
    expect(v.getUint32(24, true)).toBe(16000); // sample rate
    expect(v.getUint16(34, true)).toBe(16); // bits
    expect(v.getUint32(28, true)).toBe(32000); // byte rate = rate * 2
  });

  it('sizes the file and data chunk correctly', () => {
    const wav = encodeWav(new Float32Array(100), 16000);
    expect(wav.byteLength).toBe(44 + 200);
    const v = view(wav);
    expect(v.getUint32(40, true)).toBe(200);
    expect(v.getUint32(4, true)).toBe(36 + 200);
  });

  it('scales samples to signed 16-bit', () => {
    const v = view(encodeWav(new Float32Array([0, 1, -1]), 16000));
    expect(v.getInt16(44, true)).toBe(0);
    expect(v.getInt16(46, true)).toBe(32767);
    expect(v.getInt16(48, true)).toBe(-32767);
  });

  // Without clamping, a value above 1 wraps to the opposite sign and clicks.
  it('clamps out-of-range samples instead of wrapping', () => {
    const v = view(encodeWav(new Float32Array([2.5, -2.5]), 16000));
    expect(v.getInt16(44, true)).toBe(32767);
    expect(v.getInt16(46, true)).toBe(-32767);
  });

  it('handles an empty sample array', () => {
    const wav = encodeWav(new Float32Array(0), 16000);
    expect(wav.byteLength).toBe(44);
    expect(view(wav).getUint32(40, true)).toBe(0);
  });
});

describe('downsample', () => {
  it('returns the input unchanged when rates match', () => {
    const input = new Float32Array([1, 2, 3]);
    expect(downsample(input, 16000, 16000)).toBe(input);
  });

  it('reduces length by the rate ratio', () => {
    const input = new Float32Array(48000);
    expect(downsample(input, 48000, 16000).length).toBe(16000);
  });

  it('averages each source window rather than dropping samples', () => {
    // 4 samples at 4Hz -> 2 samples at 2Hz: means of [1,3] and [5,7].
    const out = downsample(new Float32Array([1, 3, 5, 7]), 4, 2);
    expect(Array.from(out)).toEqual([2, 6]);
  });

  it('preserves a constant signal exactly', () => {
    const out = downsample(new Float32Array(4800).fill(0.25), 48000, 16000);
    expect(out.every((v) => Math.abs(v - 0.25) < 1e-6)).toBe(true);
  });

  it('refuses to upsample', () => {
    expect(() => downsample(new Float32Array(10), 8000, 16000)).toThrow(/upsample/i);
  });
});

describe('peakAmplitude', () => {
  it('finds the largest absolute value', () => {
    expect(peakAmplitude(new Float32Array([0.1, -0.8, 0.3]))).toBeCloseTo(0.8);
  });

  it('reports zero for silence', () => {
    expect(peakAmplitude(new Float32Array(100))).toBe(0);
  });

  it('classifies near-silence below the threshold', () => {
    expect(peakAmplitude(new Float32Array([0.001, -0.002]))).toBeLessThan(SILENCE_THRESHOLD);
  });

  it('classifies real speech above the threshold', () => {
    expect(peakAmplitude(new Float32Array([0.05, -0.4]))).toBeGreaterThan(SILENCE_THRESHOLD);
  });
});

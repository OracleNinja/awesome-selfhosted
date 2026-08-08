/**
 * PCM capture helpers.
 *
 * MediaRecorder produces WebM/Opus, which AVFoundation (and therefore the macOS
 * Speech framework) cannot read. Capturing raw PCM and encoding a WAV ourselves
 * sidesteps that entirely — and unlike a recorder pipeline, this is a pure
 * function that can be tested without a browser.
 */

/**
 * Resamples mono Float32 audio by averaging each source window, which is a
 * cheap low-pass and avoids the aliasing that naive decimation produces.
 */
export function downsample(
  input: Float32Array,
  inputRate: number,
  targetRate: number,
): Float32Array {
  if (targetRate === inputRate) return input;
  if (targetRate > inputRate) {
    throw new Error(`Cannot upsample ${inputRate}Hz to ${targetRate}Hz`);
  }

  const ratio = inputRate / targetRate;
  const outLength = Math.floor(input.length / ratio);
  const output = new Float32Array(outLength);

  for (let i = 0; i < outLength; i += 1) {
    const start = Math.floor(i * ratio);
    const end = Math.min(Math.floor((i + 1) * ratio), input.length);
    let sum = 0;
    for (let j = start; j < end; j += 1) sum += input[j];
    output[i] = end > start ? sum / (end - start) : 0;
  }

  return output;
}

/** Encodes mono Float32 samples (-1..1) as a 16-bit PCM WAV file. */
export function encodeWav(samples: Float32Array, sampleRate: number): Uint8Array {
  const bytesPerSample = 2;
  const dataBytes = samples.length * bytesPerSample;
  const buffer = new ArrayBuffer(44 + dataBytes);
  const view = new DataView(buffer);

  const writeAscii = (offset: number, text: string) => {
    for (let i = 0; i < text.length; i += 1) view.setUint8(offset + i, text.charCodeAt(i));
  };

  writeAscii(0, 'RIFF');
  view.setUint32(4, 36 + dataBytes, true);
  writeAscii(8, 'WAVE');

  writeAscii(12, 'fmt ');
  view.setUint32(16, 16, true); // PCM chunk size
  view.setUint16(20, 1, true); // format: PCM
  view.setUint16(22, 1, true); // channels: mono
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * bytesPerSample, true); // byte rate
  view.setUint16(32, bytesPerSample, true); // block align
  view.setUint16(34, 16, true); // bits per sample

  writeAscii(36, 'data');
  view.setUint32(40, dataBytes, true);

  let offset = 44;
  for (let i = 0; i < samples.length; i += 1) {
    // Clamp before scaling, or a value slightly outside -1..1 wraps to the
    // opposite sign and produces an audible click.
    const clamped = Math.max(-1, Math.min(1, samples[i]));
    view.setInt16(offset, Math.round(clamped * 32767), true);
    offset += bytesPerSample;
  }

  return new Uint8Array(buffer);
}

/** Peak amplitude, used to reject silence before paying for transcription. */
export function peakAmplitude(samples: Float32Array): number {
  let peak = 0;
  for (let i = 0; i < samples.length; i += 1) {
    const value = Math.abs(samples[i]);
    if (value > peak) peak = value;
  }
  return peak;
}

/** Below this peak the clip is treated as silence and never sent to the engine. */
export const SILENCE_THRESHOLD = 0.01;

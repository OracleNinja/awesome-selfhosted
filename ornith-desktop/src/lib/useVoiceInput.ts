import { useCallback, useEffect, useRef, useState } from 'react';
import { bridge } from './bridge';
import { downsample, encodeWav, peakAmplitude, SILENCE_THRESHOLD } from '../../shared/wav';
import { MAX_RECORDING_SECONDS, STT_SAMPLE_RATE } from '../../shared/voice';

export type RecordingState = 'idle' | 'recording' | 'transcribing';

export interface VoiceInputError {
  message: string;
  /** True when the user (or the OS) refused access, so retrying won't help. */
  permanent: boolean;
}

/**
 * Microphone capture in the renderer, transcription in main.
 *
 * Capturing here means the macOS permission prompt is attributed to the app
 * rather than to a spawned helper binary, which is the usual failure mode.
 * Raw PCM is collected rather than using MediaRecorder, because MediaRecorder
 * emits WebM/Opus and AVFoundation cannot read it.
 */
export function useVoiceInput(locale: string, onTranscript: (text: string) => void) {
  const [state, setState] = useState<RecordingState>('idle');
  const [error, setError] = useState<VoiceInputError | null>(null);

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const chunksRef = useRef<Float32Array[]>([]);
  const sampleRateRef = useRef<number>(STT_SAMPLE_RATE);
  const cancelledRef = useRef(false);
  const limitTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const teardown = useCallback(() => {
    if (limitTimerRef.current) {
      clearTimeout(limitTimerRef.current);
      limitTimerRef.current = null;
    }
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void contextRef.current?.close().catch(() => {});
    contextRef.current = null;
  }, []);

  useEffect(() => teardown, [teardown]);

  const describeError = (err: unknown): VoiceInputError => {
    const name = (err as { name?: string })?.name ?? '';
    if (name === 'NotAllowedError' || name === 'SecurityError') {
      return {
        message:
          'Microphone access was denied. Enable it in System Settings → Privacy & Security → Microphone.',
        permanent: true,
      };
    }
    if (name === 'NotFoundError' || name === 'OverconstrainedError') {
      return { message: 'No microphone was found. Connect one and try again.', permanent: false };
    }
    if (name === 'NotReadableError') {
      return {
        message: 'The microphone is in use by another application.',
        permanent: false,
      };
    }
    return {
      message: err instanceof Error ? err.message : 'Could not start recording.',
      permanent: false,
    };
  };

  const stop = useCallback(async () => {
    if (state !== 'recording') return;

    teardown();
    const collected = chunksRef.current;
    chunksRef.current = [];

    if (cancelledRef.current) {
      cancelledRef.current = false;
      setState('idle');
      return;
    }

    const total = collected.reduce((n, chunk) => n + chunk.length, 0);
    if (total === 0) {
      setState('idle');
      setError({ message: 'No audio was captured.', permanent: false });
      return;
    }

    const merged = new Float32Array(total);
    let offset = 0;
    for (const chunk of collected) {
      merged.set(chunk, offset);
      offset += chunk.length;
    }

    // Don't pay for transcription on a clip that is effectively silence.
    if (peakAmplitude(merged) < SILENCE_THRESHOLD) {
      setState('idle');
      setError({ message: "Didn't hear anything — try speaking a little louder.", permanent: false });
      return;
    }

    setState('transcribing');
    try {
      const reduced = downsample(merged, sampleRateRef.current, STT_SAMPLE_RATE);
      const wav = encodeWav(reduced, STT_SAMPLE_RATE);
      const result = await bridge.voice.transcribe(wav, locale);

      if (result.error) {
        setError({ message: result.error, permanent: false });
      } else if (!result.text) {
        setError({ message: 'Nothing was recognised in that recording.', permanent: false });
      } else {
        onTranscript(result.text);
      }
    } catch (err) {
      setError({
        message: err instanceof Error ? err.message : 'Transcription failed.',
        permanent: false,
      });
    } finally {
      setState('idle');
    }
  }, [state, teardown, locale, onTranscript]);

  const start = useCallback(async () => {
    if (state !== 'idle') return;
    setError(null);
    cancelledRef.current = false;
    chunksRef.current = [];

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { channelCount: 1, echoCancellation: true, noiseSuppression: true },
      });
      streamRef.current = stream;

      const context = new AudioContext();
      contextRef.current = context;
      sampleRateRef.current = context.sampleRate;

      const source = context.createMediaStreamSource(stream);
      // ScriptProcessorNode is deprecated but needs no separate worklet module,
      // which would require its own URL under the app's CSP. Adequate here;
      // a worklet is the eventual upgrade.
      const processor = context.createScriptProcessor(4096, 1, 1);

      processor.onaudioprocess = (event) => {
        chunksRef.current.push(new Float32Array(event.inputBuffer.getChannelData(0)));
      };

      source.connect(processor);
      // Chromium will not run the processor unless it reaches the destination;
      // a zero gain keeps it running without echoing the mic to the speakers.
      const mute = context.createGain();
      mute.gain.value = 0;
      processor.connect(mute);
      mute.connect(context.destination);

      setState('recording');

      limitTimerRef.current = setTimeout(() => {
        void stop();
      }, MAX_RECORDING_SECONDS * 1000);
    } catch (err) {
      teardown();
      setState('idle');
      setError(describeError(err));
    }
  }, [state, teardown, stop]);

  const cancel = useCallback(() => {
    cancelledRef.current = true;
    void stop();
  }, [stop]);

  return {
    state,
    error,
    start,
    stop,
    cancel,
    clearError: useCallback(() => setError(null), []),
  };
}

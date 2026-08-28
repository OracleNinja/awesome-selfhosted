import { useEffect, useRef } from 'react';
import { bridge } from './bridge';

/**
 * Plays replies synthesised by a drive-bundled engine.
 *
 * macOS `say` talks to the speakers from the main process, so this does
 * nothing there. Piper only writes a WAV, so on Windows and Linux the renderer
 * is what actually has audio output, and the bytes come over IPC.
 *
 * Decoding an ArrayBuffer needs no blob or data URL, so the production CSP
 * (`default-src 'self'`, `connect-src 'none'`) is untouched — a `<audio src>`
 * would have forced `media-src blob:` open.
 */
export function useSpokenAudio(): void {
  const contextRef = useRef<AudioContext | null>(null);
  const sourceRef = useRef<AudioBufferSourceNode | null>(null);
  const playingRef = useRef<string | null>(null);

  useEffect(() => {
    function stop(): void {
      const source = sourceRef.current;
      sourceRef.current = null;
      playingRef.current = null;
      if (!source) return;
      // The handler would otherwise report a stop as a natural finish.
      source.onended = null;
      try {
        source.stop();
      } catch {
        /* already finished */
      }
    }

    const offAudio = bridge.on('tts:audio', (payload) => {
      void (async () => {
        stop(); // one utterance at a time, same as the native engine

        try {
          // Created lazily and kept: an AudioContext per utterance exhausts
          // Chromium's limit over a long conversation.
          contextRef.current ??= new AudioContext();
          const context = contextRef.current;
          if (context.state === 'suspended') await context.resume();

          // decodeAudioData detaches the buffer it is given, so it gets a copy;
          // the payload may be reused by the structured-clone layer.
          const bytes = Uint8Array.from(payload.wav);
          const buffer = await context.decodeAudioData(bytes.buffer as ArrayBuffer);

          // Another utterance may have started while we were decoding.
          if (playingRef.current !== null) return;

          const source = context.createBufferSource();
          source.buffer = buffer;
          source.connect(context.destination);
          source.onended = () => {
            if (playingRef.current !== payload.requestId) return;
            sourceRef.current = null;
            playingRef.current = null;
            bridge.voice.finishedSpeaking(payload.requestId);
          };

          sourceRef.current = source;
          playingRef.current = payload.requestId;
          source.start();
        } catch {
          // A malformed WAV must not leave the indicator spinning forever.
          bridge.voice.finishedSpeaking(payload.requestId);
        }
      })();
    });

    // Main says "not speaking" when the user hits Stop, and for the native
    // engine too. One message covers both; only our own id concerns us.
    const offState = bridge.on('tts:state', (payload) => {
      if (payload.speaking) return;
      if (playingRef.current === null) return;
      if (payload.requestId !== null && payload.requestId !== playingRef.current) return;
      stop();
    });

    return () => {
      offAudio();
      offState();
      stop();
      void contextRef.current?.close().catch(() => {});
      contextRef.current = null;
    };
  }, []);
}

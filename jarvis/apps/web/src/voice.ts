/**
 * Browser voice support.
 *
 * The Web Speech API is the fallback path: recognition and synthesis both run
 * on the client, so no audio leaves the machine and no key is needed. When the
 * server reports server-side (NVIDIA) voice providers instead, the app posts to
 * /api/voice/* and plays the returned audio.
 *
 * Everything here reports honestly whether it is supported — the UI disables
 * the microphone button rather than pretending to listen.
 */

interface SpeechRecognitionLike extends EventTarget {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  start(): void;
  stop(): void;
  abort(): void;
  onresult: ((event: any) => void) | null;
  onerror: ((event: any) => void) | null;
  onend: (() => void) | null;
}

function recognitionConstructor(): (new () => SpeechRecognitionLike) | null {
  const w = window as any;
  return w.SpeechRecognition ?? w.webkitSpeechRecognition ?? null;
}

export function speechRecognitionSupported(): boolean {
  return recognitionConstructor() !== null;
}

export function speechSynthesisSupported(): boolean {
  return typeof window !== 'undefined' && 'speechSynthesis' in window;
}

export interface DictationHandle {
  stop(): void;
}

/** Start browser dictation. Returns null when unsupported. */
export function startDictation(callbacks: {
  onPartial?: (text: string) => void;
  onFinal: (text: string) => void;
  onError?: (message: string) => void;
  onEnd?: () => void;
}): DictationHandle | null {
  const Recognition = recognitionConstructor();
  if (!Recognition) return null;

  const recognition = new Recognition();
  recognition.lang = navigator.language || 'en-US';
  recognition.continuous = false;
  recognition.interimResults = true;

  let finalText = '';

  recognition.onresult = (event: any) => {
    let interim = '';
    for (let i = event.resultIndex; i < event.results.length; i += 1) {
      const result = event.results[i];
      if (result.isFinal) finalText += result[0].transcript;
      else interim += result[0].transcript;
    }
    if (interim && callbacks.onPartial) callbacks.onPartial(finalText + interim);
  };

  recognition.onerror = (event: any) => {
    callbacks.onError?.(
      event?.error === 'not-allowed'
        ? 'Microphone permission was denied.'
        : `Speech recognition error: ${event?.error ?? 'unknown'}`,
    );
  };

  recognition.onend = () => {
    if (finalText.trim()) callbacks.onFinal(finalText.trim());
    callbacks.onEnd?.();
  };

  recognition.start();
  return { stop: () => recognition.stop() };
}

/** Speak text in the browser. Returns false when unsupported. */
export function speakInBrowser(text: string): boolean {
  if (!speechSynthesisSupported()) return false;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.rate = 1.02;
  utterance.pitch = 0.95;
  window.speechSynthesis.speak(utterance);
  return true;
}

export function stopSpeaking(): void {
  if (speechSynthesisSupported()) window.speechSynthesis.cancel();
}

/** Play base64 audio returned by a server-side TTS provider. */
export async function playAudioB64(audioB64: string, mimeType: string): Promise<void> {
  const audio = new Audio(`data:${mimeType};base64,${audioB64}`);
  await audio.play();
}

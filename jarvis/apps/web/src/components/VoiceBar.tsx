/**
 * The command bar: microphone, BRIEF ME, and the command field.
 *
 * All three funnel into the runtime. BRIEF ME sends an ordinary user turn
 * through `/api/chat` — there is no separate briefing engine here, and nothing
 * in this file interprets a command. React collects text and hands it over.
 *
 * Voice phase is client-owned in browser mode and clearly labelled as such:
 * the Web Speech API runs in this tab, so the runtime genuinely cannot report
 * whether a microphone is open. In server mode the phase brackets the real
 * `/api/voice/*` request instead.
 */
import { useEffect, useRef, useState } from 'react';
import { useRuntime, useRuntimeClient } from '../runtime/react';
import {
  speakInBrowser,
  speechRecognitionSupported,
  speechSynthesisSupported,
  startDictation,
  stopSpeaking,
  type DictationHandle,
} from '../voice';

export function VoiceBar({ conversationId, onConversation }: {
  conversationId: string | null;
  onConversation: (id: string) => void;
}) {
  const client = useRuntimeClient();
  const [draft, setDraft] = useState('');
  const [speakReplies, setSpeakReplies] = useState(false);
  const [lastReply, setLastReply] = useState('');
  const dictation = useRef<DictationHandle | null>(null);

  const voice = useRuntime(
    (state) => state.voice,
    (a, b) => a.phase === b.phase && a.error === b.error && a.sttMode === b.sttMode,
  );
  const inFlight = useRuntime((state) => state.commandInFlight);
  const commandError = useRuntime((state) => state.lastCommandError);

  const serverVoice = voice.sttMode === 'server';
  const micSupported = serverVoice || speechRecognitionSupported();
  const speakerSupported = voice.ttsMode === 'server' || speechSynthesisSupported();

  // Report browser capability into the store so the top bar and core agree.
  useEffect(() => {
    client.store.setVoice({ supported: micSupported });
  }, [client, micSupported]);

  // Speak new replies. In browser mode this is speechSynthesis; the phase is
  // published so the core sphere shows a real speaking state.
  useEffect(() => {
    if (!speakReplies || !lastReply) return;
    if (voice.ttsMode === 'server') return; // server-side TTS is not wired to autoplay yet
    client.store.setVoicePhase('speaking');
    const ok = speakInBrowser(lastReply);
    if (!ok) {
      client.store.setVoicePhase('error', 'This browser cannot synthesise speech.');
      return;
    }
    const done = () => client.store.setVoicePhase('idle');
    window.speechSynthesis.addEventListener('end', done, { once: true });
    // speechSynthesis 'end' is unreliable across browsers; fall back on a
    // length-proportional estimate so the core does not stay stuck "speaking".
    const estimate = Math.min(30_000, 1200 + lastReply.length * 55);
    const timer = setTimeout(done, estimate);
    return () => {
      clearTimeout(timer);
      window.speechSynthesis.removeEventListener('end', done);
    };
  }, [client, lastReply, speakReplies, voice.ttsMode]);

  const submit = async (text: string) => {
    const message = text.trim();
    if (!message || inFlight) return;
    setDraft('');
    client.store.setVoicePhase('processing');
    const result = await client.sendCommand(message, conversationId);
    client.store.setVoicePhase('idle');
    if (result.conversationId) onConversation(result.conversationId);
    if (result.reply) setLastReply(result.reply);
  };

  const brief = async () => {
    if (inFlight) return;
    client.store.setVoicePhase('processing');
    const result = await client.brief(conversationId);
    client.store.setVoicePhase('idle');
    if (result.conversationId) onConversation(result.conversationId);
    if (result.reply) setLastReply(result.reply);
  };

  const toggleMic = () => {
    if (voice.phase === 'listening') {
      dictation.current?.stop();
      return;
    }
    if (!micSupported) {
      client.store.setVoicePhase('error', 'Speech recognition is not available in this browser.');
      return;
    }
    const handle = startDictation({
      onPartial: (text) => setDraft(text),
      onFinal: (text) => {
        setDraft(text);
        void submit(text);
      },
      onError: (message) => client.store.setVoicePhase('error', message),
      onEnd: () => {
        client.store.setVoice({ phase: 'idle' });
      },
    });
    if (!handle) {
      client.store.setVoicePhase('error', 'Speech recognition is not available in this browser.');
      return;
    }
    dictation.current = handle;
    client.store.setVoicePhase('listening');
  };

  return (
    <div className="voicebar" data-testid="voice-bar">
      {(voice.error || commandError) && (
        <div className="voicebar-error small" data-testid="command-error">
          {voice.error ?? commandError}
        </div>
      )}

      <div className="voicebar-row">
        <button
          className={`mic-orb ${voice.phase}`}
          onClick={toggleMic}
          disabled={!micSupported}
          aria-pressed={voice.phase === 'listening'}
          title={
            micSupported
              ? `Voice input (${voice.sttMode})`
              : 'Speech recognition is unavailable in this browser'
          }
          data-testid="mic-orb"
          data-phase={voice.phase}
        >
          <span className="mic-ring" />
          <span className="mic-icon">{voice.phase === 'listening' ? '■' : '🎙'}</span>
        </button>

        <button
          className="btn btn-primary brief-btn"
          onClick={() => void brief()}
          disabled={inFlight}
          data-testid="brief-me"
        >
          {inFlight ? 'WORKING…' : 'BRIEF ME'}
        </button>

        <input
          className="input command-input"
          placeholder={
            voice.phase === 'listening' ? 'Listening…' : 'Command JARVIS…'
          }
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter') void submit(draft);
          }}
          data-testid="command-input"
        />

        <button
          className="btn"
          onClick={() => void submit(draft)}
          disabled={inFlight || !draft.trim()}
          data-testid="command-send"
        >
          SEND
        </button>

        <label className="row small muted speak-toggle">
          <input
            type="checkbox"
            checked={speakReplies}
            disabled={!speakerSupported}
            onChange={(event) => {
              setSpeakReplies(event.target.checked);
              if (!event.target.checked) {
                stopSpeaking();
                client.store.setVoicePhase('idle');
              }
            }}
          />
          Speak
        </label>
      </div>

      {lastReply && (
        <div className="voicebar-reply small" data-testid="last-reply">
          {lastReply}
        </div>
      )}
    </div>
  );
}

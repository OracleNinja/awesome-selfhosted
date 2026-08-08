/**
 * Context budgeting (SPEC §5.4 / C5).
 *
 * Ollama silently discards the oldest tokens when a request exceeds num_ctx.
 * Doing the trimming here instead means the app knows it happened and can tell
 * the user, rather than leaving them to conclude the model "forgot".
 *
 * Pure: no I/O.
 */
import { CHARS_PER_TOKEN, RESPONSE_RESERVE_RATIO } from '../../shared/defaults';
import type { ChatMessage } from '../../shared/types';

export interface WireMessage {
  role: 'user' | 'assistant' | 'system';
  content: string;
}

export interface BuiltContext {
  messages: WireMessage[];
  /** How many leading messages were dropped to fit the window. */
  dropped: number;
}

export function estimateTokens(text: string): number {
  return Math.ceil(text.length / CHARS_PER_TOKEN);
}

/**
 * Builds the wire payload from stored history, newest-first until the budget is
 * spent. System messages are always kept. Reasoning text is never sent back —
 * it is a rendering artefact, not conversation.
 */
export function buildContext(history: readonly ChatMessage[], numCtx: number): BuiltContext {
  const budget = Math.max(256, Math.floor(numCtx * (1 - RESPONSE_RESERVE_RATIO)));

  const usable = history.filter(
    (m) => m.status !== 'error' && (m.role === 'user' || m.content.trim().length > 0),
  );

  const system = usable.filter((m) => m.role === 'system');
  const turns = usable.filter((m) => m.role !== 'system');

  let used = system.reduce((n, m) => n + estimateTokens(m.content), 0);
  const kept: ChatMessage[] = [];

  for (let i = turns.length - 1; i >= 0; i -= 1) {
    const cost = estimateTokens(turns[i].content);
    // Always keep the most recent turn, even if it alone exceeds the budget —
    // sending nothing would be worse, and Ollama will trim it server-side.
    if (kept.length > 0 && used + cost > budget) break;
    used += cost;
    kept.unshift(turns[i]);
  }

  const messages: WireMessage[] = [
    ...system.map((m) => ({ role: 'system' as const, content: m.content })),
    ...kept.map((m) => ({ role: m.role as 'user' | 'assistant', content: m.content })),
  ];

  return { messages, dropped: turns.length - kept.length };
}

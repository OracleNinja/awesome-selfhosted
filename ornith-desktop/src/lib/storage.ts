import type { Conversation } from '../types';

const STORAGE_KEY = 'ornith.conversations.v1';
const MODEL_KEY = 'ornith.model.v1';

export function loadConversations(): Conversation[] {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    // Shape-check rather than trusting whatever a previous version wrote.
    return parsed.filter(
      (c): c is Conversation =>
        typeof c === 'object' &&
        c !== null &&
        typeof (c as Conversation).id === 'string' &&
        Array.isArray((c as Conversation).messages),
    );
  } catch {
    return [];
  }
}

export function saveConversations(conversations: Conversation[]): void {
  try {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(conversations));
  } catch {
    // Quota exceeded or storage disabled — chats stay in memory for this session.
  }
}

export function loadPreferredModel(): string | null {
  try {
    return localStorage.getItem(MODEL_KEY);
  } catch {
    return null;
  }
}

export function savePreferredModel(model: string): void {
  try {
    localStorage.setItem(MODEL_KEY, model);
  } catch {
    // Non-fatal: the choice just won't survive a restart.
  }
}

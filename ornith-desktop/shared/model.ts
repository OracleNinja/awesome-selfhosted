/**
 * SPEC C4: Ollama's /api/tags reports fully-qualified names, so a model created
 * as `ornith-en` comes back as `ornith-en:latest`. A plain Array.includes check
 * reports a working model as missing.
 */
export function normaliseModelName(name: string): string {
  const trimmed = name.trim();
  return trimmed.includes(':') ? trimmed : `${trimmed}:latest`;
}

export function modelMatches(installed: string, wanted: string): boolean {
  return normaliseModelName(installed) === normaliseModelName(wanted);
}

/** True when any installed model matches the wanted name. */
export function isModelInstalled(installed: readonly string[], wanted: string): boolean {
  return installed.some((name) => modelMatches(name, wanted));
}

/**
 * Picks the model to use: the configured one if present, else the default, else
 * the first installed. Returns the configured name unchanged when nothing is
 * installed, so the UI can report it as missing rather than silently switching.
 */
export function resolveActiveModel(
  installed: readonly string[],
  configured: string,
  fallback: string,
): string {
  const match = installed.find((name) => modelMatches(name, configured));
  if (match) return configured;

  const fallbackMatch = installed.find((name) => modelMatches(name, fallback));
  if (fallbackMatch) return fallback;

  return installed[0] ?? configured;
}

/** Derives a conversation title from the first user message. */
export function deriveTitle(text: string, maxLength = 42): string {
  const stripped = text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/`([^`]*)`/g, '$1')
    .replace(/^#{1,6}\s+/gm, '')
    .replace(/[*_~>]/g, '')
    .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
    .replace(/\s+/g, ' ')
    .trim();

  if (!stripped) return 'New chat';
  // Use the spread operator so surrogate pairs and combining marks survive.
  const chars = [...stripped];
  if (chars.length <= maxLength) return stripped;

  const clipped = chars.slice(0, maxLength).join('');
  const lastSpace = clipped.lastIndexOf(' ');
  const base = lastSpace > maxLength * 0.5 ? clipped.slice(0, lastSpace) : clipped;
  return `${base.trimEnd()}…`;
}

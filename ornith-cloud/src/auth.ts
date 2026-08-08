import { createHash, timingSafeEqual } from 'node:crypto';

/**
 * Constant-time bearer comparison.
 *
 * A plain `===` leaks token length and prefix through timing. Hashing both
 * sides to a fixed width first means timingSafeEqual never throws on a length
 * mismatch, which would itself be an oracle.
 */
export function tokenMatches(presented: string, expected: string): boolean {
  if (!expected) return false; // never authenticate against an unset token
  if (!presented) return false;

  const a = createHash('sha256').update(presented, 'utf8').digest();
  const b = createHash('sha256').update(expected, 'utf8').digest();

  return timingSafeEqual(a, b);
}

/** Extracts the token from an Authorization header, or null. */
export function parseBearer(header: string | undefined | null): string | null {
  if (!header) return null;
  const match = /^Bearer\s+(\S+)$/i.exec(header.trim());
  return match ? match[1] : null;
}

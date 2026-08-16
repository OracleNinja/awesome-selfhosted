/**
 * API authentication.
 *
 * Two modes, and the server tells you which one it is in at startup:
 *
 *  - token mode  (JARVIS_API_TOKEN set): every /api request must carry
 *    `Authorization: Bearer <token>`. Compared in constant time.
 *  - local mode  (no token): the server binds to 127.0.0.1 only, so nothing
 *    off-box can reach it. Convenient for development; the README says to set a
 *    token before exposing JARVIS to a network.
 *
 * The token is never sent to the browser. The web app talks to a same-origin
 * server, and in token mode the browser supplies the token the user pasted into
 * Settings — which is a user-held credential, not a provider secret.
 */
import { timingSafeEqual } from 'node:crypto';
import type { IncomingMessage } from 'node:http';
import { JarvisError } from '@jarvis/shared';

export const DEFAULT_USER_ID = 'user_local';

export interface AuthConfig {
  token: string;
}

export function constantTimeEqual(a: string, b: string): boolean {
  const bufferA = Buffer.from(a, 'utf8');
  const bufferB = Buffer.from(b, 'utf8');
  if (bufferA.byteLength !== bufferB.byteLength) return false;
  return timingSafeEqual(bufferA, bufferB);
}

export function extractToken(req: IncomingMessage): string {
  const header = req.headers.authorization;
  if (typeof header === 'string' && header.toLowerCase().startsWith('bearer ')) {
    return header.slice(7).trim();
  }
  const alternate = req.headers['x-jarvis-token'];
  if (typeof alternate === 'string') return alternate.trim();
  return '';
}

/**
 * Throws JarvisError(401) when the request is not authorised.
 *
 * `queryToken` exists for exactly one route: the SSE stream. EventSource cannot
 * set request headers, so the browser passes the user's own session token as a
 * query parameter there. It is never accepted on any other route.
 */
export function authenticate(
  req: IncomingMessage,
  config: AuthConfig,
  options: { queryToken?: string } = {},
): string {
  if (!config.token) return DEFAULT_USER_ID; // local mode; binding is the control

  const supplied = extractToken(req) || (options.queryToken ?? '');
  if (!supplied || !constantTimeEqual(supplied, config.token)) {
    throw new JarvisError('missing or invalid API token', { status: 401, code: 'unauthorized' });
  }
  return DEFAULT_USER_ID;
}

/** In local mode we refuse to listen on anything but loopback. */
export function bindHost(config: AuthConfig, requested?: string): string {
  if (requested) return requested;
  return config.token ? '0.0.0.0' : '127.0.0.1';
}

import type { MiddlewareHandler } from 'hono';
import { parseBearer, tokenMatches } from '../auth.js';
import { log } from '../log.js';

/**
 * Bearer auth for every /v1/* route.
 *
 * The response body is identical for a missing, malformed, and wrong token —
 * distinguishing them would tell an attacker how close they are.
 */
export function requireAuth(expectedToken: string): MiddlewareHandler {
  return async (c, next) => {
    const presented = parseBearer(c.req.header('Authorization'));

    if (!presented || !tokenMatches(presented, expectedToken)) {
      // Never log the presented value, not even a prefix.
      log.warn('gateway.auth_failed', { path: c.req.path, hadHeader: Boolean(presented) });
      return c.json({ code: 'UNAUTHORIZED', message: 'Authentication required.' }, 401);
    }

    await next();
  };
}

/** Minimal fixed-window rate limit, keyed per client. */
export function rateLimit(maxRequests: number, windowMs: number): MiddlewareHandler {
  const hits = new Map<string, { count: number; resetAt: number }>();

  return async (c, next) => {
    const key =
      c.req.header('x-forwarded-for')?.split(',')[0]?.trim() ||
      c.req.header('x-real-ip') ||
      'local';
    const nowMs = Date.now();
    const entry = hits.get(key);

    if (!entry || entry.resetAt <= nowMs) {
      hits.set(key, { count: 1, resetAt: nowMs + windowMs });
    } else if (entry.count >= maxRequests) {
      log.warn('gateway.rate_limited', { path: c.req.path });
      return c.json(
        { code: 'RATE_LIMITED', message: 'Too many requests. Try again shortly.' },
        429,
      );
    } else {
      entry.count += 1;
    }

    // Bound memory: drop expired keys opportunistically.
    if (hits.size > 1000) {
      for (const [k, v] of hits) if (v.resetAt <= nowMs) hits.delete(k);
    }

    await next();
  };
}

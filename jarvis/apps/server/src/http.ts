/**
 * A small HTTP router over node:http.
 *
 * JARVIS needs routing, JSON bodies, SSE and static files — about 150 lines of
 * work. Pulling in a framework for that would add a dependency surface to a
 * process that holds every credential in the system, so it is written out here
 * where it can be read in one sitting.
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import { JarvisError, redact } from '@jarvis/shared';

export type Method = 'GET' | 'HEAD' | 'POST' | 'PUT' | 'PATCH' | 'DELETE' | 'OPTIONS';

export interface RequestContext {
  req: IncomingMessage;
  res: ServerResponse;
  method: Method;
  path: string;
  params: Record<string, string>;
  query: URLSearchParams;
  userId: string;
  body: <T = Record<string, unknown>>() => Promise<T>;
  json: (status: number, data: unknown) => void;
  sse: () => (event: string, data: unknown) => void;
}

export type Handler = (ctx: RequestContext) => Promise<void> | void;

interface Route {
  method: Method;
  segments: string[];
  handler: Handler;
  /** Routes that skip the auth check (health, and the static app itself). */
  public: boolean;
}

const MAX_BODY_BYTES = 25 * 1024 * 1024; // generous: voice/image payloads are base64

export class Router {
  private routes: Route[] = [];

  add(method: Method, path: string, handler: Handler, options: { public?: boolean } = {}): this {
    this.routes.push({
      method,
      segments: path.split('/').filter(Boolean),
      handler,
      public: options.public ?? false,
    });
    return this;
  }

  get(path: string, handler: Handler, options?: { public?: boolean }): this {
    return this.add('GET', path, handler, options);
  }
  post(path: string, handler: Handler, options?: { public?: boolean }): this {
    return this.add('POST', path, handler, options);
  }
  delete(path: string, handler: Handler, options?: { public?: boolean }): this {
    return this.add('DELETE', path, handler, options);
  }

  match(method: string, path: string): { route: Route; params: Record<string, string> } | null {
    const segments = path.split('/').filter(Boolean);
    for (const route of this.routes) {
      if (route.method !== method) continue;
      if (route.segments.length !== segments.length) continue;

      const params: Record<string, string> = {};
      let matched = true;
      for (let i = 0; i < route.segments.length; i += 1) {
        const expected = route.segments[i]!;
        const actual = segments[i]!;
        if (expected.startsWith(':')) params[expected.slice(1)] = decodeURIComponent(actual);
        else if (expected !== actual) {
          matched = false;
          break;
        }
      }
      if (matched) return { route, params };
    }
    return null;
  }

  isPublic(method: string, path: string): boolean {
    return this.match(method, path)?.route.public ?? false;
  }

  get size(): number {
    return this.routes.length;
  }
}

export async function readBody<T>(req: IncomingMessage): Promise<T> {
  const chunks: Buffer[] = [];
  let total = 0;
  for await (const chunk of req) {
    const buffer = chunk as Buffer;
    total += buffer.byteLength;
    if (total > MAX_BODY_BYTES) throw new JarvisError('request body too large', { status: 413 });
    chunks.push(buffer);
  }
  if (total === 0) return {} as T;
  const raw = Buffer.concat(chunks).toString('utf8');
  try {
    return JSON.parse(raw) as T;
  } catch {
    throw new JarvisError('request body is not valid JSON', { status: 400, code: 'bad_json' });
  }
}

export function sendJson(res: ServerResponse, status: number, data: unknown): void {
  const payload = JSON.stringify(data);
  res.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(payload),
    'cache-control': 'no-store',
    // The API is JSON-only and never embeds untrusted HTML.
    'x-content-type-options': 'nosniff',
    'referrer-policy': 'no-referrer',
  });
  res.end(payload);
}

export function sendError(res: ServerResponse, error: unknown): void {
  const status = error instanceof JarvisError ? error.status : 500;
  const code = error instanceof JarvisError ? error.code : 'internal_error';
  const message = redact((error as Error).message ?? 'internal error');
  sendJson(res, status, { error: { code, message } });
}

/** Start a Server-Sent Events stream and return a writer. */
export function startSse(res: ServerResponse): (event: string, data: unknown) => void {
  res.writeHead(200, {
    'content-type': 'text/event-stream; charset=utf-8',
    'cache-control': 'no-store',
    connection: 'keep-alive',
    'x-accel-buffering': 'no',
  });
  res.write(': connected\n\n');
  return (event, data) => {
    if (res.writableEnded) return;
    res.write(`event: ${event}\ndata: ${JSON.stringify(data)}\n\n`);
  };
}

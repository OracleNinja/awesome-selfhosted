/**
 * The HTTP server: request pipeline, auth, static hosting.
 *
 * Exported as a factory so tests can start it on an ephemeral port and hit it
 * with real HTTP rather than calling handlers directly.
 */
import { createServer, type Server } from 'node:http';
import { createReadStream, existsSync, statSync } from 'node:fs';
import { extname, join, normalize, sep } from 'node:path';
import type { Jarvis } from '@jarvis/core';
import { JarvisError } from '@jarvis/shared';
import { authenticate, bindHost, DEFAULT_USER_ID } from './auth.ts';
import { readBody, sendError, sendJson, startSse, type Method, type RequestContext } from './http.ts';
import { createRouter } from './routes.ts';

const MIME_TYPES: Record<string, string> = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.mjs': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
  '.json': 'application/json; charset=utf-8',
  '.svg': 'image/svg+xml',
  '.png': 'image/png',
  '.jpg': 'image/jpeg',
  '.webp': 'image/webp',
  '.ico': 'image/x-icon',
  '.woff2': 'font/woff2',
  '.map': 'application/json; charset=utf-8',
};

export interface JarvisServerOptions {
  jarvis: Jarvis;
  /** Directory of the built web app. When present it is served at `/`. */
  webDir?: string;
  host?: string;
}

export function createJarvisServer(options: JarvisServerOptions): Server {
  const { jarvis } = options;
  const router = createRouter(jarvis);
  const token = jarvis.config.apiToken;

  return createServer(async (req, res) => {
    const url = new URL(req.url ?? '/', `http://${req.headers.host ?? 'localhost'}`);
    const path = url.pathname;
    const method = (req.method ?? 'GET') as Method;

    // The API is same-origin; no CORS headers are emitted on purpose. A browser
    // page on another origin therefore cannot read JARVIS's responses.
    if (method === 'OPTIONS') {
      res.writeHead(204, { allow: 'GET, POST, DELETE' });
      res.end();
      return;
    }

    try {
      if (path.startsWith('/api/')) {
        const matched = router.match(method, path);
        if (!matched) {
          throw new JarvisError(`no route for ${method} ${path}`, { status: 404, code: 'not_found' });
        }

        const userId = matched.route.public
          ? DEFAULT_USER_ID
          : authenticate(
              req,
              { token },
              // EventSource cannot send headers; only the stream route accepts
              // the user's session token as a query parameter.
              path === '/api/events/stream' ? { queryToken: url.searchParams.get('token') ?? '' } : {},
            );

        const ctx: RequestContext = {
          req,
          res,
          method,
          path,
          params: matched.params,
          query: url.searchParams,
          userId,
          body: <T>() => readBody<T>(req),
          json: (status, data) => sendJson(res, status, data),
          sse: () => startSse(res),
        };

        await matched.route.handler(ctx);
        return;
      }

      if (options.webDir && (method === 'GET' || method === 'HEAD')) {
        serveStatic(options.webDir, path, res);
        return;
      }

      throw new JarvisError(`not found: ${path}`, { status: 404, code: 'not_found' });
    } catch (error) {
      if (!res.headersSent) sendError(res, error);
      else if (!res.writableEnded) res.end();
    }
  });
}

/** Static file serving with SPA fallback. Path traversal is rejected. */
function serveStatic(webDir: string, requestPath: string, res: import('node:http').ServerResponse): void {
  const relative = normalize(decodeURIComponent(requestPath)).replace(/^(\.\.[/\\])+/, '');
  const candidate = join(webDir, relative);

  if (!candidate.startsWith(webDir + sep) && candidate !== webDir) {
    sendJson(res, 403, { error: { code: 'forbidden', message: 'path traversal rejected' } });
    return;
  }

  let filePath = candidate;
  if (!existsSync(filePath) || statSync(filePath).isDirectory()) {
    filePath = join(webDir, 'index.html');
  }
  if (!existsSync(filePath)) {
    sendJson(res, 404, {
      error: {
        code: 'web_not_built',
        message: 'The web app has not been built. Run `npm run build:web` (or `npm run dev` for development).',
      },
    });
    return;
  }

  const type = MIME_TYPES[extname(filePath).toLowerCase()] ?? 'application/octet-stream';
  const immutable = filePath.includes(`${sep}assets${sep}`);
  res.writeHead(200, {
    'content-type': type,
    'cache-control': immutable ? 'public, max-age=31536000, immutable' : 'no-cache',
    'x-content-type-options': 'nosniff',
  });
  createReadStream(filePath).pipe(res);
}

export async function startJarvisServer(
  options: JarvisServerOptions & { port?: number },
): Promise<{ server: Server; port: number; host: string }> {
  const server = createJarvisServer(options);
  const host = bindHost({ token: options.jarvis.config.apiToken }, options.host);
  const port = options.port ?? options.jarvis.config.port;

  await new Promise<void>((resolve, reject) => {
    server.once('error', reject);
    server.listen(port, host, () => {
      server.removeListener('error', reject);
      resolve();
    });
  });

  const address = server.address();
  const actualPort = typeof address === 'object' && address ? address.port : port;
  return { server, port: actualPort, host };
}

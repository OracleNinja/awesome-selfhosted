# Production

## Build and run

```bash
npm install
npm run build          # bundles the server, builds the UI
npm start              # node apps/server/dist/server.js
```

The production server serves the built UI from `apps/web/dist` at `/`, so a
single process on a single port covers everything. The build output is one
bundled ESM file plus `better-sqlite3` as the only external runtime dependency.

## Required configuration

```bash
NODE_ENV=production
PORT=8787

# Authentication — REQUIRED for any non-loopback deployment.
JARVIS_API_TOKEN=<64 hex chars>

MODEL_PROVIDER=nvidia
NVIDIA_API_KEY=nvapi-...
NVIDIA_BASE_URL=https://integrate.api.nvidia.com/v1
NVIDIA_MODEL=meta/llama-3.3-70b-instruct

DATABASE_URL=file:/var/lib/jarvis/jarvis.db
JARVIS_WORKSPACE_DIR=/var/lib/jarvis/workspace

APPROVAL_REQUIRED_LEVELS=EXTERNAL_ACTION,DESTRUCTIVE
```

Generate a token:

```bash
node -e "console.log(require('crypto').randomBytes(32).toString('hex'))"
```

Without `JARVIS_API_TOKEN` the server binds to `127.0.0.1` only and refuses to
listen on a public interface. That is a safety default, not a bug.

## TLS and reverse proxy

JARVIS speaks plain HTTP. Terminate TLS in front of it.

```nginx
server {
  listen 443 ssl http2;
  server_name jarvis.example.com;

  ssl_certificate     /etc/letsencrypt/live/jarvis.example.com/fullchain.pem;
  ssl_certificate_key /etc/letsencrypt/live/jarvis.example.com/privkey.pem;

  location / {
    proxy_pass http://127.0.0.1:8787;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;

    # Server-Sent Events: do not buffer the activity stream.
    proxy_buffering off;
    proxy_read_timeout 3600s;
  }
}
```

`proxy_buffering off` matters — without it the live activity feed arrives in
batches or not at all.

## systemd

```ini
[Unit]
Description=JARVIS
After=network.target

[Service]
Type=simple
User=jarvis
WorkingDirectory=/opt/jarvis
EnvironmentFile=/etc/jarvis/env
ExecStart=/usr/bin/node apps/server/dist/server.js
Restart=on-failure
RestartSec=5

# The workspace and database are the only paths JARVIS needs to write.
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/var/lib/jarvis

[Install]
WantedBy=multi-user.target
```

Keep `/etc/jarvis/env` at mode `0600`, owned by root. It holds every provider
key in the system.

## Docker

```dockerfile
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
COPY packages ./packages
COPY apps ./apps
RUN npm ci
COPY . .
RUN npm run build

FROM node:22-slim
WORKDIR /app
ENV NODE_ENV=production
COPY --from=build /app/node_modules ./node_modules
COPY --from=build /app/apps/server/dist ./apps/server/dist
COPY --from=build /app/apps/web/dist ./apps/web/dist
COPY --from=build /app/agents ./agents
VOLUME /var/lib/jarvis
EXPOSE 8787
CMD ["node", "apps/server/dist/server.js"]
```

Copy `agents/` into the image — charter overrides are read from disk at startup.

## Health checks

`GET /api/health` is unauthenticated and returns liveness plus schema version:

```json
{"status":"ok","version":"0.1.0","database":{"ok":true,"schemaVersion":1}}
```

It returns 503 if the schema is incomplete. It deliberately exposes nothing
else. For provider connectivity use the authenticated
`POST /api/system/provider-check`, which performs a real completion — do not
put that on a liveness probe, it costs tokens.

## Backups

Everything durable is in the SQLite file. With WAL enabled, copy it safely with:

```bash
sqlite3 /var/lib/jarvis/jarvis.db ".backup '/backups/jarvis-$(date +%F).db'"
```

Back up `JARVIS_WORKSPACE_DIR` too if agents write files you care about. The
audit log lives in the same database — treat a backup as a compliance record.

## Upgrading

Migrations are append-only and run automatically at startup, inside a
transaction, tracked by `user_version`. Back up first; there is no down-migration.

## Operational notes

- **Rate limiting** — not built in. Add it at the proxy; a compromised token can
  otherwise exhaust provider quota.
- **Log rotation** — JARVIS logs to stdout. Let journald or Docker handle it.
- **Approval timeout** — `APPROVAL_TIMEOUT_SECONDS` (default 900). Longer means
  a pending destructive action stays executable for longer.
- **Single user** — v0.1 has one user identity. Do not hand the token to people
  who should not share a memory store.

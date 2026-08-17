# JARVIS — personal AI operating system
#
# Multi-stage. No secret ever enters the build context or any layer: every
# credential arrives at runtime as an environment variable (Fly: `fly secrets
# set`), read by packages/shared/src/config.ts.
#
# Values here match the application, verified against the source rather than
# assumed:
#   entrypoint   apps/server/dist/server.js   (scripts/build-server.mjs outfile)
#   port         PORT, default 8787           (packages/shared/src/config.ts)
#   build        npm run build                (esbuild bundle + vite static UI)
#   node         >=20.11                      (package.json engines)
#   externals    better-sqlite3 only          (the sole non-node: import in the
#                                              bundle; everything else is inlined)
#
# The directory layout below is NOT arbitrary. `repoRoot()` resolves
# `dirname(import.meta.url)/../../..`, which from
# /app/apps/server/dist/server.js is /app. The server then reads
# `repoRoot()/apps/web/dist` for the Control Room and `repoRoot()/agents` for
# the agent charters, so both must sit under /app at exactly that depth.
#
# Debian rather than Alpine on purpose: better-sqlite3 publishes glibc
# prebuilds, so npm fetches a binary instead of compiling sqlite from source.
# The build toolchain is still installed as a fallback for platforms with no
# prebuild — it stays in the discarded stages and never reaches the image.

# ---------------------------------------------------------------- deps stage
# Production dependencies only. Reused verbatim by the runtime stage.
FROM node:22-bookworm-slim AS deps
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*
# npm ci in a workspace refuses to run unless every workspace manifest is
# present, so the workspace directories are copied before install.
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps ./apps
RUN npm ci --omit=dev

# --------------------------------------------------------------- build stage
FROM node:22-bookworm-slim AS build
WORKDIR /app
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 make g++ ca-certificates \
 && rm -rf /var/lib/apt/lists/*
COPY package.json package-lock.json ./
COPY packages ./packages
COPY apps ./apps
RUN npm ci
COPY tsconfig.json ./
COPY scripts ./scripts
# Bundles the server and builds the static Control Room into apps/web/dist.
RUN npm run build

# ------------------------------------------------------------- runtime stage
FROM node:22-bookworm-slim AS runtime
ENV NODE_ENV=production
# Default only; Fly overrides it from fly.toml [env].
ENV PORT=8787
WORKDIR /app

COPY --from=deps  /app/node_modules          ./node_modules
COPY --from=build /app/apps/server/dist      ./apps/server/dist
COPY --from=build /app/apps/web/dist         ./apps/web/dist
# Agent charters are data, read at boot. Without them the roster falls back to
# the built-in definitions and reports a charter problem instead of failing.
COPY agents ./agents
COPY package.json ./

# Drop root. The application writes only to its database and workspace, both of
# which live on a mounted volume outside this directory — see fly.toml.
USER node

EXPOSE 8787

# The compiled entrypoint directly: `npm start` would add a shell plus an extra
# process for no benefit, and would swallow the signals close() depends on to
# release the SQLite handle and shut MCP child processes down cleanly.
CMD ["node", "apps/server/dist/server.js"]

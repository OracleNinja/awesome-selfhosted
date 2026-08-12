# Ornith Cloud Gateway
#
# Multi-stage. No secret ever enters the build context or any layer — all
# credentials arrive at runtime through `fly secrets set`, which surfaces them
# as environment variables read by src/config.ts.
#
# Values here match the application, verified against the source rather than
# assumed:
#   entrypoint  dist/src/index.js   (tsconfig rootDir "." + outDir "dist")
#   port        PORT, default 8787  (src/config.ts)
#   build       tsc -p tsconfig.json
#   node        >=22                (package.json engines)

# ---------------------------------------------------------------- deps stage
# Production dependencies only, resolved once and reused by the runtime stage.
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci --omit=dev

# --------------------------------------------------------------- build stage
FROM node:22-alpine AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY tsconfig.json ./
# `tests/` is deliberately not copied: tsconfig includes it, so copying it
# would emit dist/tests into the image. Omitting it leaves dist/src alone,
# which is what the entrypoint expects.
COPY src ./src
RUN npm run build

# ------------------------------------------------------------- runtime stage
FROM node:22-alpine AS runtime
ENV NODE_ENV=production
# Default only; Fly overrides it from fly.toml [env].
ENV PORT=8787
WORKDIR /app

COPY --from=deps  /app/node_modules ./node_modules
COPY --from=build /app/dist         ./dist
COPY package.json ./

# Drop root. The gateway writes nothing to disk — the retrieval cache is
# in-memory — so a read-only application directory is sufficient.
USER node

EXPOSE 8787

# Node's ESM loader needs the compiled entrypoint; `npm start` would add a
# shell and an extra process for no benefit, and would swallow signals.
CMD ["node", "dist/src/index.js"]

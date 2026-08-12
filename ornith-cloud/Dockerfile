# syntax=docker/dockerfile:1

# ---- build stage: compile TypeScript ------------------------------------
FROM node:22-alpine AS build
WORKDIR /app

# Install every dependency (dev + prod) from the committed lockfile.
COPY package.json package-lock.json ./
RUN npm ci

# Compile src (and tests) to dist/ using the repo's own tsconfig.
COPY tsconfig.json ./
COPY src ./src
RUN npm run build

# ---- runtime stage: production image ------------------------------------
FROM node:22-alpine AS runtime
WORKDIR /app
ENV NODE_ENV=production

# Only production dependencies.
COPY package.json package-lock.json ./
RUN npm ci --omit=dev && npm cache clean --force

# Compiled output only — no sources, no tests, no dev tooling.
COPY --from=build /app/dist ./dist

USER node
EXPOSE 8787

CMD ["node", "dist/src/index.js"]

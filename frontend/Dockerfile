# syntax=docker/dockerfile:1
# Static-serve web image: builds the Next.js static export, serves it with Caddy and proxies /api/* to the API.
#   docker build -t pm-web frontend
#   docker run -p 8080:8080 -e API_ORIGIN=http://api:8000 pm-web
# (Cloudflare Pages needs no image: it publishes out/ directly, see README.md.)
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
COPY scripts ./scripts
RUN npm ci

FROM node:22-alpine AS build
WORKDIR /app
ENV NEXT_TELEMETRY_DISABLED=1
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* values are baked into the static files at build time. Empty API URL = same origin (default).
ARG NEXT_PUBLIC_API_URL=""
ENV NEXT_PUBLIC_API_URL=${NEXT_PUBLIC_API_URL}
RUN npm run build

FROM caddy:2-alpine AS runner
ENV PORT=8080 XDG_CONFIG_HOME=/tmp/caddy-config XDG_DATA_HOME=/tmp/caddy-data
COPY --from=build /app/out /srv
COPY Caddyfile /etc/caddy/Caddyfile
USER nobody
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s CMD wget -qO- http://127.0.0.1:8080/he/login/ >/dev/null || exit 1
CMD ["caddy", "run", "--config", "/etc/caddy/Caddyfile", "--adapter", "caddyfile"]

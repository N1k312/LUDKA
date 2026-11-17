# Сборка Mini App и веб-сервер Caddy в одном образе.
# Контекст сборки — корень репозитория: docker build -f deploy/web.Dockerfile .
FROM node:22-bookworm-slim AS build
WORKDIR /src
COPY miniapp/package.json miniapp/package-lock.json ./
RUN npm ci
COPY miniapp/ ./
RUN npm run build

FROM caddy:2.10-alpine
COPY deploy/Caddyfile /etc/caddy/Caddyfile
COPY --from=build /src/dist /srv/app

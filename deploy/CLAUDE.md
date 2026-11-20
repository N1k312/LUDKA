# deploy — рабочий запуск на компьютере владельца

Перед работой: [../docs/operations.md](../docs/operations.md) (ранбук), [../docs/architecture.md](../docs/architecture.md).

| Файл | Что |
| --- | --- |
| `compose.server.yml` | дополняет корневой `docker-compose.yml`: `web` (Caddy), `tunnel` (cloudflared, метрики на `127.0.0.1:2000`), `backup` |
| `Caddyfile` | `/app/*` — статика; `/api/*`, `/tg/*`, `/health`, `/media/*` — в `api:8000`; остальное 404 |
| `web.Dockerfile` | сборка Mini App (node) + Caddy |
| `backup.sh` | ждёт `pg_isready`, `pg_dump` в файл, проверяет непустой, gzip; картинки клубов — tar; ротация |
| `connect-club.md` | инструкция для людей по подключению клуба |

## Правила

- **Только LF** в `backup.sh` и `Caddyfile` (тест `test_s19`). После правки проверить: `file deploy/backup.sh` не должен писать «CRLF».
- Новый публичный путь в Caddy — осознанно, с обновлением `test_s15` и `docs/roles-and-security.md`.
- Порты сервисов — только `127.0.0.1:…` (тест `test_s14`).
- Изменил `compose.server.yml` — проверь `docker compose -f docker-compose.yml -f deploy/compose.server.yml config --quiet`.

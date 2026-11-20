# Эксплуатация (ранбук)

Все команды — из корня репозитория. `C` ниже = `docker compose -f docker-compose.yml -f deploy/compose.server.yml`.

## Запуск и обновление

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml up -d --build
```

- После изменения кода сервера достаточно `… up -d --build api`; Mini App или Caddyfile — `… up -d --build web`.
- После изменения `.env` контейнер нужно **пересоздать** (`up -d api`), простой `restart` не перечитывает `env_file`.
- Миграции применяются при старте `api` сами.

Проверка, что всё живо:

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml ps
```

```bash
curl -s http://127.0.0.1:2000/quicktunnel
```

```bash
curl -s http://127.0.0.1:8000/health
```

В логах `api` после старта должно быть «Новый адрес туннеля: … — перенастраиваю ботов».

## Типовые ситуации

| Симптом | Причина | Что делать |
| --- | --- | --- |
| Боты молчат после перезагрузки | Docker Desktop не запустился | Запустить Docker Desktop; включить «Start Docker Desktop when you sign in» |
| `failed to connect to the docker API … dockerDesktopLinuxEngine` | то же | то же |
| Кнопки «Играть» в старых сообщениях ведут на мёртвый адрес | туннель перезапустился, адрес сменился | нормально: кнопка у поля ввода и `/start` уже с новым адресом |
| Контейнер `backup` перезапускается в цикле, в логах `set: Illegal option -` | файл со строками CRLF | `sed -i 's/\r$//' deploy/backup.sh`, пересоздать `backup` |
| Бэкап весит ~20 байт | дамп пустой | скрипт сейчас это ловит («backup FAILED»); проверить, что `db` здоров |
| `api` перезапускается | база ещё не готова или ошибка миграции | `C logs api --tail 50` |
| Вебхук указывает не туда | вручную запускали `refresh-webhooks` или dev-туннель | перезапустить `api` — первый опрос туннеля перенастроит всех |

## Туннель и вебхуки

- Адрес туннеля узнаётся из метрик cloudflared (`127.0.0.1:2000/quicktunnel`), API следит за ним сам.
- `app.cli refresh-webhooks` и `connect-bot` берут живой адрес (туннель → иначе `.env`, только если отвечает `/health`), на мёртвый адрес ботов не переставят.
- Проверить вебхук бота: `getWebhookInfo` с токеном из `.env` (токен в чат не выводить).

## Бэкапы

- Раз в сутки (и при старте контейнера): `bot-ГГГГ-ММ-ДД_ЧЧММ.sql.gz` (база) и `media-….tar.gz` (картинки клубов) в `BACKUP_DIR` (по умолчанию `./backups`), хранение `BACKUP_KEEP_DAYS` (14).
- `BACKUP_DIR` стоит указать в облачной папке — сейчас копии лежат на том же компьютере.
- Восстановление базы (стек запущен):

```bash
gunzip -c backups/bot-2026-11-20_0918.sql.gz | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

- Картинки: `tar -xzf media-….tar.gz` в том `media` (через временный контейнер с этим томом).

## Режим разработки

1. Остановить рабочий стек (`C down`) — иначе два туннеля будут перетягивать вебхуки.
2. `docker compose up -d` (db + api), `npm run dev` в `miniapp`, `cloudflared tunnel --url http://127.0.0.1:5173`.
3. Адрес туннеля → `PUBLIC_BASE_URL` в `.env`, `docker compose up -d`, затем `app.cli refresh-webhooks`.
4. Mini App без Telegram: `http://127.0.0.1:5173/app/?mock` (варианты в [../miniapp/CLAUDE.md](../miniapp/CLAUDE.md)).
5. После разработки — снова рабочий стек.

## Настройки за владельцем (не код)

- [x] Автозапуск Docker Desktop при входе в Windows.
- [x] Отключить сон компьютера-сервера.
- [ ] Автовход в Windows (`netplwiz`) — иначе после ночного обновления Windows ждёт пароль и Docker не стартует.
- [ ] `BACKUP_DIR` в облачной папке.
- [ ] Закрытый репозиторий на GitHub.

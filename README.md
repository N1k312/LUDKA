# Платформа мини-игр лояльности для компьютерных клубов

У каждого клуба свой Telegram-бот с Mini App: гости бесплатно играют в колесо, стаканчики и кейс и выигрывают бонусы клуба по промокоду, админ гасит код в боте. Клуб настраивает игры, призы и шансы в своём боте, владелец платформы управляет всеми клубами в боте-пульте.

Требования — [requirments.md](requirments.md), план работ — [plan.md](plan.md), подключение клуба — [deploy/connect-club.md](deploy/connect-club.md), документация для разработки — [docs/](docs/architecture.md).

## Структура

| Папка | Что внутри |
| --- | --- |
| `backend/` | FastAPI, боты aiogram (боты клубов и пульт), работа с базой, тесты |
| `miniapp/` | Mini App на React + Vite + TypeScript: лобби игр, колесо, стаканчики, кейс, призы, правила |
| `deploy/` | Рабочий запуск на своём компьютере: Caddy, туннель, бэкапы |
| `docs/` | Архитектура, модель данных, боты, безопасность, эксплуатация, тесты, решения |

## Первый запуск

1. Запустить Docker Desktop.
2. Скопировать `.env.example` в `.env` и заполнить пароль базы и `ENCRYPTION_KEY` (команда генерации ключа — в самом файле).

## Запуск всего проекта

```bash
docker compose up -d --build
```

Проверка: http://127.0.0.1:8000/health должен вернуть `{"status":"ok","db":"ok"}`. Документация API: http://127.0.0.1:8000/docs.

Остановить:

```bash
docker compose down
```

## Разработка бэкенда без Docker

База всё равно запускается в Docker:

```bash
docker compose up -d db
```

Окружение Python (один раз):

```bash
cd backend
py -3.12 -m venv .venv
.venv\Scripts\python -m pip install -r requirements-dev.txt
```

Тесты и линтер (подробно — [docs/testing.md](docs/testing.md)):

```bash
cd backend
.venv\Scripts\python -m pytest -q
```

```bash
cd backend
.venv\Scripts\python -m pyflakes app tests
```

API с автоперезагрузкой при изменении кода:

```bash
cd backend
.venv\Scripts\python -m uvicorn app.main:app --reload
```

Тесты используют отдельную базу `<имя>_test`, она пересоздаётся при каждом запуске, данные разработки не трогаются.

## Миграции и тестовые данные

Применить миграции (в Docker это происходит автоматически при старте `api`):

```bash
cd backend
.venv\Scripts\alembic upgrade head
```

После изменения моделей в `app/models.py` создать новую миграцию:

```bash
cd backend
.venv\Scripts\alembic revision --autogenerate -m "что изменилось"
```

Создать клуб-пилот с колесом и призами (повторный запуск ничего не дублирует; стаканчики и кейс включаются в боте после добавления призов):

```bash
cd backend
.venv\Scripts\python -m app.seed
```

## Mini App

Зависимости (один раз):

```bash
cd miniapp
npm install
```

Dev-сервер с горячей перезагрузкой: http://127.0.0.1:5173/app/. Он же проксирует `/api`, `/tg`, `/health` и `/media` в бэкенд на порту 8000.

```bash
cd miniapp
npm run dev
```

Без Telegram интерфейс открывается только в мок-режиме с тестовыми данными всех трёх игр: `/app/?mock`, а также `?mock=unregistered`, `=unavailable`, `=cooldown`, `=slow`, `=offline`; оформление клуба — `&brand=neon|sunset|ocean|forest|gold`, логотип — `&logo`, вкладка призов — `&tab=prizes`. В production-сборку мок не попадает.

Сборка (проверка типов + `dist/`):

```bash
cd miniapp
npm run build
```

## Бот и Mini App в Telegram при разработке

Telegram требует HTTPS и для вебхуков, и для Mini App, поэтому наружу открывается dev-сервер Mini App туннелем cloudflared, а он передаёт API и вебхуки в бэкенд.

1. Запустить бэкенд (`docker compose up -d --build`), Mini App (`npm run dev` в `miniapp`) и туннель:

   ```bash
   cloudflared tunnel --url http://127.0.0.1:5173
   ```

2. Скопировать адрес вида `https://….trycloudflare.com` из вывода в `PUBLIC_BASE_URL` в `.env` и пересоздать API:

   ```bash
   docker compose up -d
   ```

3. Первый раз — подключить бота к клубу (токен берётся из `TEST_BOT_TOKEN`):

   ```bash
   cd backend
   .venv\Scripts\python -m app.cli connect-bot --club-id 1
   ```

   При следующих запусках адрес туннеля меняется — достаточно перенастроить вебхуки и кнопку Mini App:

   ```bash
   cd backend
   .venv\Scripts\python -m app.cli refresh-webhooks
   ```

Остальные консольные команды (запасной путь, обычно всё делается в боте-пульте): `.venv\Scripts\python -m app.cli --help`.

## Рабочий запуск на своём компьютере (без VPS и домена)

Компьютер (ваш или ПК в клубе) работает как сервер. Всё поднимается одной командой:

| Сервис | Что делает |
| --- | --- |
| `db` | PostgreSQL |
| `api` | FastAPI + боты всех клубов |
| `web` | Caddy: собранный Mini App на `/app/`, проксирует `/api`, `/tg`, `/health`, `/media` в `api` |
| `tunnel` | cloudflared: публичный HTTPS-адрес `https://….trycloudflare.com` |
| `backup` | Бэкап базы и картинок клубов сразу при старте и затем раз в сутки в `BACKUP_DIR` |

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml up -d --build
```

**Адрес туннеля меняется при каждом перезапуске** — это нормально: API раз в минуту спрашивает его у cloudflared и при смене сам перенастраивает вебхуки и кнопку «Играть» у всех ботов (в логах `api`: «Новый адрес туннеля», затем «Все боты перенастроены»). Новый адрес выдерживается 90 секунд, чтобы Telegram успел его увидеть, поэтому после включения компьютера боты начинают отвечать примерно через 2 минуты; сообщения за это время не теряются. QR-коды у кассы ведут на `t.me/<бот>` и не меняются. Кнопки «Играть» в *старых* сообщениях бота после смены адреса не откроются — гостю достаточно нажать кнопку «Играть» у поля ввода или отправить `/start`.

Статус и логи:

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml ps
```

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml logs -f api
```

Остановить:

```bash
docker compose -f docker-compose.yml -f deploy/compose.server.yml down
```

### Чтобы платформа работала сама

1. Docker Desktop → Settings → General → включить **Start Docker Desktop when you sign in**. Все сервисы с `restart: unless-stopped` поднимутся после перезагрузки сами.
2. Windows → Параметры → Система → Питание: **сон — никогда** (при подключении к сети). Пока компьютер спит или выключен, бот не отвечает.
3. Если компьютер может перезагрузиться без вас (обновления Windows), включите автовход в Windows — Docker Desktop стартует только после входа в систему.

### Бэкапы

Файлы `bot-ГГГГ-ММ-ДД_ЧЧММ.sql.gz` (база) и `media-….tar.gz` (картинки клубов) в папке `BACKUP_DIR` из `.env` (по умолчанию `./backups`), хранятся `BACKUP_KEEP_DAYS` дней. В бэкапе есть телефоны гостей — папку никому не открывать. Чтобы копии пережили потерю компьютера, укажите папку OneDrive / Google Drive или другой диск, например `BACKUP_DIR=C:/Users/<имя>/OneDrive/bot-backups`, и перезапустите стек.

Восстановление (база должна быть запущена):

```bash
gunzip -c backups/bot-2026-11-17_1008.sql.gz | docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB"'
```

### Режим разработки и рабочий режим — не одновременно

Оба поднимают свой туннель и переключают на себя вебхуки ботов. Перед разработкой остановите рабочий стек (`down`), после — запустите снова.

## База в DBeaver

Хост `localhost`, порт `5432`; пользователь, пароль и имя базы — из `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).

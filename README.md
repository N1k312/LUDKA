# Платформа мини-игр лояльности для компьютерных клубов

Требования — [requirments.md](requirments.md), план работ — [plan.md](plan.md).

## Структура

| Папка | Что внутри |
| --- | --- |
| `backend/` | FastAPI, боты aiogram, работа с базой, тесты |
| `miniapp/` | Mini App на React + Vite + TypeScript |
| `deploy/` | Конфиги для сервера (появятся на шаге 9) |

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

Тесты:

```bash
cd backend
.venv\Scripts\python -m pytest
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

Создать клуб-пилот с колесом и призами (повторный запуск ничего не дублирует):

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

Dev-сервер с горячей перезагрузкой: http://127.0.0.1:5173/app/. Он же проксирует `/api`, `/tg` и `/health` в бэкенд на порту 8000.

```bash
cd miniapp
npm run dev
```

Без Telegram интерфейс открывается только в мок-режиме с тестовыми данными: `/app/?mock`, а также `?mock=unregistered`, `?mock=unavailable`, `?mock=cooldown`. В production-сборку мок не попадает.

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

Остальные команды суперадмина: `.venv\Scripts\python -m app.cli --help`.

## База в DBeaver

Хост `localhost`, порт `5432`; пользователь, пароль и имя базы — из `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).

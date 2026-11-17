# Платформа мини-игр лояльности для компьютерных клубов

Требования — [requirments.md](requirments.md), план работ — [plan.md](plan.md).

## Структура

| Папка | Что внутри |
| --- | --- |
| `backend/` | FastAPI, боты aiogram, работа с базой, тесты |
| `miniapp/` | Mini App на React + Vite (появится на шаге 6) |
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

## Бот в Telegram при разработке

Telegram шлёт вебхуки только на HTTPS, поэтому API открывается наружу туннелем cloudflared.

1. Запустить проект (`docker compose up -d --build`) и туннель:

   ```bash
   cloudflared tunnel --url http://localhost:8000
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

   При следующих запусках адрес туннеля меняется — достаточно перенастроить вебхуки:

   ```bash
   cd backend
   .venv\Scripts\python -m app.cli refresh-webhooks
   ```

Остальные команды суперадмина: `.venv\Scripts\python -m app.cli --help`.

## База в DBeaver

Хост `localhost`, порт `5432`; пользователь, пароль и имя базы — из `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).

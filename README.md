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

## База в DBeaver

Хост `localhost`, порт `5432`; пользователь, пароль и имя базы — из `.env` (`POSTGRES_USER`, `POSTGRES_PASSWORD`, `POSTGRES_DB`).

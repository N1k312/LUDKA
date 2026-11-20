# backend — FastAPI + aiogram

Перед работой: [../docs/architecture.md](../docs/architecture.md), для ботов — [../docs/bots.md](../docs/bots.md), для тестов — [../docs/testing.md](../docs/testing.md).

## Где что

| Путь | Что |
| --- | --- |
| `app/main.py` | приложение, роутеры, `/media`, фоновые задачи |
| `app/config.py` | настройки из `.env` (`get_settings()`); в тестах меняются через `monkeypatch.setattr(get_settings(), …)` |
| `app/models.py` | все таблицы, `MAX_BIGINT` |
| `app/routes/` | `telegram.py` (вебхуки клубов), `platform.py` (вебхук пульта), `miniapp.py` (`/api/*`) |
| `app/bots/` | `handlers.py` гость, `admin.py` админ-панель, `setup.py` подключение/настройка бота, `registry.py` кэш `Bot`, `notify.py` сообщения вне диалога |
| `app/platform_bot/` | пульт: `handlers.py`, `setup.py` (вебхук, секрет), `dispatcher.py` |
| `app/services/` | логика без Telegram: `wheel`, `codes`, `redemption`, `guests`, `guest_list`, `staff`, `subscription`, `branding`, `clubs`, `stats`, `tunnel`, `qr` |
| `app/cli.py` | консольные команды суперадмина (`python -m app.cli --help`) |
| `migrations/versions/` | миграции Alembic |
| `tests/`, `tests/audit/` | тесты (см. docs/testing.md) |

## Соглашения

- Бизнес-логика — в `services/`, обработчики Telegram и роуты только вызывают её и форматируют ответ.
- Ответы ботов — HTML (`parse_mode` по умолчанию), пользовательские строки — `html.quote`.
- Даты в базе — UTC, в сообщениях — часовой пояс клуба (`bots/notify.py::local_datetime`).
- Новые `callback_data`: префиксы `p:` (пульт, `Nav`), `g:` (гости), `redeem:`, `staff:rm:`, `admin:`, `consent:`, `guest:` — ≤ 64 байт.
- Ошибки Telegram после записи в базу не должны откатывать запись: `await db.commit()` до ответа или `suppress(TelegramAPIError)`.
- Venv: `.venv/Scripts/python` (Windows). Вывод тестов — с `PYTHONIOENCODING=utf-8`.

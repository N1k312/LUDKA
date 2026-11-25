# backend — FastAPI + aiogram

Перед работой: [../docs/architecture.md](../docs/architecture.md), для ботов — [../docs/bots.md](../docs/bots.md), для тестов — [../docs/testing.md](../docs/testing.md).

## Где что

| Путь | Что |
| --- | --- |
| `app/main.py` | приложение, роутеры, `/media`, фоновые задачи |
| `app/config.py` | настройки из `.env` (`get_settings()`); в тестах меняются через `monkeypatch.setattr(get_settings(), …)` |
| `app/models.py` | все таблицы, `MAX_BIGINT` |
| `app/routes/` | `telegram.py` (вебхуки клубов), `platform.py` (вебхук пульта), `miniapp.py` (`/api/*`) |
| `app/bots/` | `handlers.py` гость, `admin.py` админ-панель, `prize_editor.py` «🎮 Игры и призы», `setup.py` подключение/настройка бота, `registry.py` кэш `Bot`, `notify.py` сообщения вне диалога, `dispatcher.py` порядок роутеров |
| `app/platform_bot/` | пульт: `handlers.py`, `setup.py` (вебхук, секрет), `dispatcher.py` |
| `app/services/` | логика без Telegram: `wheel` (все три игры: `play`, лимиты, `GAMES`), `prizes` (редактор призов, редкость), `clubs` (клубы, настройки игр), `codes`, `redemption`, `guests`, `guest_list`, `staff`, `subscription`, `branding`, `stats`, `tunnel`, `qr` |
| `app/cli.py` | консольные команды владельца платформы (`python -m app.cli --help`) — запасной путь, призы и лимит только для колеса |
| `migrations/versions/` | миграции Alembic |
| `tests/`, `tests/audit/` | тесты (см. docs/testing.md) |

## Соглашения

- Бизнес-логика — в `services/`, обработчики Telegram и роуты только вызывают её и форматируют ответ.
- Ответы ботов — HTML (`parse_mode` по умолчанию), пользовательские строки — `html.quote`.
- Даты в базе — UTC, в сообщениях — часовой пояс клуба (`bots/notify.py::local_datetime`).
- Новые `callback_data`: префиксы `p:` (пульт, `Nav`), `g:` (гости), `pz:` (игры и призы клуба), `redeem:`, `staff:rm:`, `admin:`, `consent:`, `guest:` — ≤ 64 байт.
- Призы и настройки игр — всегда с указанием игры (`GameType`); без него функции работают с колесом.
- Ошибки Telegram после записи в базу не должны откатывать запись: `await db.commit()` до ответа или `suppress(TelegramAPIError)`.
- Venv: `.venv/Scripts/python` (Windows). Вывод тестов — с `PYTHONIOENCODING=utf-8`.

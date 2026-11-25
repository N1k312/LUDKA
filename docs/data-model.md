# Модель данных

Источник правды — `backend/app/models.py` и миграции в `backend/migrations/versions/`. Этот файл — карта.

## Таблицы

| Таблица | Ключ | Важное |
| --- | --- | --- |
| `clubs` | id | name, timezone (`Asia/Almaty`), status, `rules_text`, `branding` (JSONB) |
| `club_bots` | **bot_id** (Telegram id бота) | club_id UNIQUE (один бот на клуб), `token_encrypted` (Fernet), `webhook_secret`, status |
| `club_staff` | id | club_id, telegram_id, role `owner` (= суперадмин клуба) / `admin`, first_name, username. UNIQUE (club_id, telegram_id); частичный UNIQUE «один owner на клуб» |
| `guests` | id | club_id, telegram_id, username, first_name, phone (`+цифры`), consent_at. UNIQUE (club_id, telegram_id), (club_id, phone), (club_id, id) |
| `games_config` | (club_id, game_type) | game_type `wheel` / `cups` / `case`; cooldown_seconds (по умолчанию 604800), enabled. У нового клуба включено только колесо |
| `prize_options` | id | club_id, **game_type** (у каждой игры свои призы), title, emoji, weight ≥ 0, is_empty, ttl_days, position, **active** (удаление = выключение) |
| `plays` | id | club_id, guest_id, prize_option_id, played_at |
| `prize_codes` | **code** (UNIQUE на платформе) | club_id, guest_id, prize_option_id, play_id, title (копия на момент выдачи), expires_at, redeemed_at, redeemed_by (telegram_id) |
| `redemption_log` | id | club_id, code (без FK — пишутся и несуществующие), admin_id, action `check`/`redeem`, result |
| `subscriptions` | club_id | status `trial`/`active`/`past_due`/`canceled`, trial_ends_at, current_period_end |
| `payments` | id | club_id, amount NUMERIC(12,2) > 0, currency `KZT`, paid_at, comment |

Владельцы платформы в базе не хранятся — `PLATFORM_OWNER_IDS` в `.env`.

## Изоляция клубов в самой базе

`plays` и `prize_codes` ссылаются на гостей, призы и игры **составными** внешними ключами `(club_id, id)`. База не даст связать гостя клуба А с призом клуба Б, даже если код ошибётся. Удаление клуба каскадом удаляет его данные.

## `clubs.branding` (JSONB)

```json
{"palette": "neon", "hub_label": "CA", "code_prefix": "CA",
 "logo": "logo-<sha>.png", "banner": "banner-<sha>.jpg",
 "texts": {"welcome": "...", "win": "...", "lose": "..."}}
```

Пусто/`null` — оформление по умолчанию. Палитры — `services/branding.py::PALETTES`: в Mini App из палитры берутся акцент (кнопки, подсветка, стаканчики), обод колеса и цвета конфетти; секторы колеса окрашены по редкости призов, фон всегда тёмный. Картинки лежат в `MEDIA_DIR/<club_id>/`, раздаются как `/media/<club_id>/<file>`. Менять JSONB только через функции `services/branding.py` (они присваивают новый словарь, иначе SQLAlchemy не увидит изменение).

## Игры и редкость

- Игры: `wheel` (колесо), `cups` (стаканчики, 1 из 6), `case` (кейс). Логика — `services/wheel.py` (`play`), лимит у каждой игры свой.
- Настройки игр клуба — `clubs.game_configs`: недостающие строки `games_config` создаются выключенными (у клубов, созданных до игр, их добавила миграция `b7c1d2e3f405`). Включить игру можно, только если в ней есть приз с промокодом; выключить последнюю включённую нельзя.
- В каждой игре до 12 призов; выключить последний приз игры или последний приз с промокодом нельзя.
- Стаканчики: в момент выбора сервер разыгрывает все 6 по весам призов, гость получает выбранный и видит остальные.
- Гостю отдаётся не вес, а **редкость** (`services/prizes.py::rarity`): доля приза ≤ 5% — `legendary` (красный), ≤ 20% — `rare` (синий), иначе `common` (зелёный), пустой — `empty`.

## Промокоды

`<PREFIX>-XXXXXX`: префикс клуба (2–4 латинские буквы, по умолчанию `GG`) + 6 символов из алфавита без 0/O/1/I/L (`services/codes.py::ALPHABET`). Ввод админа нормализуется (`services/redemption.py::normalize_code`): регистр, пробел/дефис, префикс можно не вводить — подставится префикс клуба.

## Миграции

- Изменил модель → `.venv\Scripts\alembic revision --autogenerate -m "что изменилось"` → просмотреть файл миграции → `alembic upgrade head`.
- `alembic check` должен говорить «No new upgrade operations detected».
- В Docker миграции применяются автоматически при старте `api`.

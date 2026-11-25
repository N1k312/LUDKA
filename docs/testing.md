# Тесты

435 тестов, все зелёные (на 2026-11-25). Тесты используют отдельную базу `<имя>_test` в том же PostgreSQL (пересоздаётся при каждом запуске, данные разработки не трогает) и **фейковый Telegram** — в сеть ничего не уходит.

## Команды (из `backend/`)

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m pytest -q -p no:cacheprovider -p no:logging --tb=line
```

```bash
.venv/Scripts/python -m pyflakes app tests
```

Без `PYTHONIOENCODING=utf-8` русский вывод в консоли Windows превращается в кракозябры. База `db` должна быть запущена.

Mini App (из `miniapp/`): `npm run build` — проверка типов и сборка. Проверка, что мок не попал в сборку: в `dist/assets/*.js` не должно быть `mockShareContact`.

## Наборы

| Файл | Что проверяет |
| --- | --- |
| `tests/test_*.py` | модули по отдельности: схема, боты, регистрация, API Mini App, админка, роли, подписка, туннель, бот платформы |
| `tests/audit/test_bugs.py` | граничные случаи и сбои Telegram («сторожа» исправленных багов B1–B7) |
| `tests/audit/test_functional.py` | сквозные сценарии по требованиям (путь гостя, подключение клуба, подписка, два клуба…) |
| `tests/audit/test_security.py` | атаки: матрица авторизации API, вебхуки, повышение прав, инъекции, секреты, конфигурация, файлы контейнеров |
| `tests/audit/test_branding.py` | оформление клуба, префикс кодов, регистрация из Mini App, приветствие, «Сегодня» |
| `tests/audit/test_reset.py` | сброс регистрации гостя и очистка данных клуба |
| `tests/audit/test_guest_texts.py` | гостю бот пишет без эмодзи: регистрация, приветствие, правила, промокод, «Приз получен», кнопки |
| `tests/audit/test_access.py` | доступ клуба: подписка без автоотключения, отключить/вернуть в пульте, «К оплате», отключённый клуб гасит выданные коды, права |
| `tests/audit/test_games.py` | три игры: API, редкость без шансов, стаканчики, лимиты, включение, бот и пульт; сторожа багов аудита 25.11 (приз с шансом 0 скрыт, параллельное создание настроек игр) |
| `tests/audit/test_prize_editor.py` | «🎮 Игры и призы» (призы): правка, защита колеса, права, изоляция клубов, уведомление, пульт |
| `tests/audit/test_guest_list.py` | «🙋 Гости»: список, поиск, карточка, изоляция |

## Фикстуры и помощники

| Имя | Где | Зачем |
| --- | --- | --- |
| `session` | `tests/conftest.py` | сессия в транзакции, откатывается после теста |
| `client` | `tests/conftest.py` | HTTP-клиент приложения на этой сессии |
| `telegram` | `tests/conftest.py` | `FakeTelegram` вместо сети; `PUBLIC_BASE_URL=https://example.test` |
| `isolated_settings` (autouse) | `tests/conftest.py` | выключает бот платформы, владельцев и туннель из `.env` |
| `engine` | `tests/conftest.py` | для тестов гонок: свои сессии + ручная очистка |
| `pbot`, `platform_on` | `tests/audit/conftest.py` | клиент бота платформы, владелец = 9000 |
| `strict` | `tests/audit/conftest.py` | `StrictTelegram`: «message is not modified», сбои методов |
| `lenient_client` | `tests/audit/conftest.py` | получить 500 вместо исключения |
| `media` | `tests/audit/test_branding.py` | картинки клубов во временную папку |
| `BotClient`, `message_update`, `callback_update`, `contact_update`, `users_shared_update` | `tests/botkit.py` | отправка обновлений в вебхук бота |
| `club_with_bot`, `register`, `api`, `nav`, `say` | `tests/audit/conftest.py` | клуб с ботом, регистрация гостя, заголовки `initData`, кнопки пульта |

## Подводные камни

- В тестах с сессией в транзакции **не вызывать `db.rollback()` в коде** без нужды: откатится и подготовка теста.
- Ответ, зависящий от сбоя Telegram или отката, проверять на **настоящих коммитах** (`engine` + свои сессии), как в `test_b06` и тестах гонок.
- `PlatformClient.texts()` / `BotClient.send()` очищают записанные вызовы при каждой отправке — ответ сохранять сразу.
- Новый эндпоинт API — добавить в `ENDPOINTS` в `test_security.py` (матрица авторизации).
- Новый публичный путь Caddy — осознанно добавить в `test_s15`.
- Новая `callback_data` — в `test_b14` (лимит 64 байта).

## Нагрузочный тест

Код — `backend/loadtest/` (`server.py` — сервер с заглушкой Telegram, `run.py` — сценарии и замеры). Результаты и расчёт на 1 клуб — [load-test.md](load-test.md). Рабочая база и боты не затрагиваются: всё идёт во временную базу, каждый прогон начинается с чистой схемы.

Временная база (из корня):

```bash
docker run -d --rm --name loadtest-db -e POSTGRES_USER=load -e POSTGRES_PASSWORD=load -e POSTGRES_DB=load -p 127.0.0.1:55432:5432 postgres:17
```

Быстрый прогон — сервер на Windows из venv (из `backend/`):

```bash
PYTHONIOENCODING=utf-8 .venv/Scripts/python -m loadtest.run
```

Точный прогон — сервер на рабочем образе в Linux (так получены цифры в load-test.md), из корня:

```bash
docker network create loadnet && docker network connect loadnet loadtest-db
```

```bash
MSYS_NO_PATHCONV=1 docker run -d --rm --name loadtest-api --network loadnet --env-file .env -e LOADTEST_DB=postgresql+asyncpg://load:load@loadtest-db:5432/load -e LOADTEST_HOST=0.0.0.0 -v "$PWD/backend/loadtest:/app/loadtest:ro" -v "$PWD/backend/tests:/app/tests:ro" -p 127.0.0.1:8766:8765 bot-api:latest python -m loadtest.server
```

```bash
cd backend && PYTHONIOENCODING=utf-8 LOADTEST_API_CONTAINER=loadtest-api LOADTEST_PORT=8766 .venv/Scripts/python -m loadtest.run
```

После: `docker stop loadtest-api loadtest-db`, `docker network rm loadnet`.

Настройки через переменные: `LOADTEST_LEVELS` («10:300,100:800» — одновременно:заходов), `LOADTEST_EXTRAS=0` (только игры), `LOADTEST_WORKERS` (процессов генератора, 6), `LOADTEST_TG_DELAY` (задержка заглушки Telegram, 0.15 с), `LOADTEST_POOL` («20:20» — пул соединений с базой для сравнения).

- `--env-file .env` нужен только ради `ENCRYPTION_KEY`: бот платформы, туннель и адрес базы `server.py` подменяет, Telegram заменён заглушкой.
- Один процесс генератора упирается в своё ядро раньше сервера — смотрите колонку загрузки генератора, она должна быть далеко от 100%.

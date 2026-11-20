# miniapp — React + Vite + TypeScript

Перед работой: [../docs/architecture.md](../docs/architecture.md) (как Mini App получает клуб и авторизуется), [../docs/bots.md](../docs/bots.md) (кнопки, открывающие Mini App).

## Файлы

| Файл | Что |
| --- | --- |
| `src/App.tsx` | экраны: регистрация, колесо, выигрыш, призы, правила; MainButton/BackButton; оформление клуба |
| `src/Wheel.tsx` | колесо на SVG: цвета клуба, надпись в центре, вращение в покое, докрутка до сектора от сервера |
| `src/Prizes.tsx` | «Мои призы», сортировка, QR на весь экран (`QrModal`) |
| `src/Scanner.tsx` | режим `?scan=1` для админа: сканер Telegram → `sendData` боту |
| `src/api.ts` | запросы с `initData` и `X-Bot-Id`, типы, `shareContact()` |
| `src/telegram.ts` | типы `Telegram.WebApp`, `haptic`, `goFullscreen`, `reducedMotion` |
| `src/mock.ts` | мок бэкенда — **только** `npm run dev` с `?mock` |
| `src/sound.ts`, `src/Confetti.tsx`, `src/format.ts`, `src/styles.css` | звук, конфетти, форматы дат, стили (цвета из темы Telegram, `--accent` клуба) |

## Проверка в браузере без Telegram

`npm run dev`, затем `http://127.0.0.1:5173/app/?mock`. Варианты: `?mock=unregistered`, `=unavailable`, `=cooldown`, `=slow`, `=offline`; оформление клуба: `&brand=neon|sunset|ocean|forest|gold` и `&logo`; вкладка: `&tab=prizes`. В браузерном превью панель может быть скрыта — анимации там идут медленно, скриншоты запаздывают; надёжнее `get_page_text`.

## Правила

- Мок не должен попадать в production-сборку: после `npm run build` в `dist/assets/*.js` нет `mockShareContact`.
- Mini App ничего не решает: приз, лимит, регистрация — на сервере. Никаких `club_id` от клиента.
- Новые поля API — сначала в `backend` и типах `api.ts`, затем в `mock.ts`.
- Анимации — с учётом `reducedMotion()`.

# miniapp — React + Vite + TypeScript

Перед работой: [../docs/architecture.md](../docs/architecture.md) (как Mini App получает клуб и авторизуется), [../docs/bots.md](../docs/bots.md) (кнопки, открывающие Mini App).

## Файлы

| Файл | Что |
| --- | --- |
| `src/App.tsx` | экраны: регистрация, лобби игр, экран игры, выигрыш, призы, правила; MainButton/BackButton; акцент клуба |
| `src/Wheel.tsx` | колесо на SVG: цвет сектора = редкость, надпись в центре, вращение в покое, докрутка до сектора от сервера |
| `src/Cups.tsx` | «Стаканчики»: 6 штук без перемешивания, поднимается выбранный, потом остальные (`layout` с сервера) |
| `src/CaseGame.tsx` | «Кейс»: лента призов докручивается до выигрыша; остальные предметы — для вида |
| `src/rarity.ts` | цвета и названия редкости, оформление карточек игр |
| `src/Icon.tsx` | свои SVG-иконки (`Icon`, `PrizeIcon`) и картинки игр (`GameArt`, `CupArt`, `CaseArt`) — **вместо эмодзи** |
| `src/Prizes.tsx` | «Мои призы», сортировка, QR на весь экран (`QrModal`) |
| `src/Scanner.tsx` | режим `?scan=1` для админа: сканер Telegram → `sendData` боту |
| `src/api.ts` | запросы с `initData` и `X-Bot-Id`, типы, `shareContact()` |
| `src/telegram.ts` | типы `Telegram.WebApp`, `haptic`, `goFullscreen`, `reducedMotion` |
| `src/mock.ts` | мок бэкенда — **только** `npm run dev` с `?mock` |
| `src/sound.ts`, `src/Confetti.tsx`, `src/format.ts`, `src/styles.css` | звук, конфетти, форматы дат, стили (всегда тёмные, `--accent` клуба, `--r-*` — цвета редкости) |

## Проверка в браузере без Telegram

`npm run dev`, затем `http://127.0.0.1:5173/app/?mock`. Варианты: `?mock=unregistered`, `=unavailable`, `=cooldown`, `=slow`, `=offline`; оформление клуба: `&brand=neon|sunset|ocean|forest|gold` и `&logo`; вкладка: `&tab=prizes`. В браузерном превью панель может быть скрыта — анимации там идут медленно, скриншоты запаздывают; надёжнее `get_page_text`.

## Правила

- Мок не должен попадать в production-сборку: после `npm run build` в `dist/assets/*.js` нет `mockShareContact`.
- Mini App ничего не решает: приз, лимит, регистрация — на сервере. Никаких `club_id` от клиента.
- Шансы гостю не показываются: сервер отдаёт только `rarity`.
- **Без эмодзи в интерфейсе гостя**: иконки — из `Icon.tsx` (нужна новая — дорисуй SVG там же), эмодзи приза из базы не выводится — вместо него `PrizeIcon` в цвете редкости. Исключение — `Scanner.tsx` (экран админа).
- Новые поля API — сначала в `backend` и типах `api.ts`, затем в `mock.ts`.
- Анимации — с учётом `reducedMotion()`.

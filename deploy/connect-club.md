# Подключение клуба к платформе

Инструкция для суперадмина (MVP: всё делается командами из папки `backend`).
Команды выполняются, когда платформа запущена (`docker compose ... up -d`).

## 1. Владелец клуба создаёт бота

1. В Telegram открыть **@BotFather** → `/newbot`.
2. Имя бота — название клуба (его видят гости), username — например `gg_<клуб>_bot`.
3. Передать токен суперадмину **лично или в защищённом канале**. Токен — пароль от бота.

**Main Mini App** в @BotFather (`/mybots` → бот → Bot Settings → Configure Mini App) при запуске без домена **не включаем**: адрес туннеля меняется при перезапуске, а в @BotFather он задаётся вручную. Гости открывают игру кнопкой «Играть» в боте — её адрес платформа обновляет сама. Когда появится постоянный домен, Main Mini App включается с адресом `https://<домен>/app/?bot=<id бота>`, а QR печатается с `--startapp`.

## 2. Суперадмин создаёт клуб

```bash
.venv\Scripts\python -m app.cli create-club --name "Название клуба"
```

Команда выведет `id` клуба — он нужен дальше. Пробный период — 14 дней.

## 3. Призы на колесе

Вес — относительный шанс; удобно, когда сумма весов = 100 (тогда вес = процент).
Обязательно добавьте пустой сектор.

```bash
.venv\Scripts\python -m app.cli add-prize --club-id N --emoji 🎮 --title "1 час игры" --weight 25
.venv\Scripts\python -m app.cli add-prize --club-id N --emoji 🥤 --title "Энергетик" --weight 20
.venv\Scripts\python -m app.cli add-prize --club-id N --emoji 💸 --title "Скидка 10%" --weight 25
.venv\Scripts\python -m app.cli add-prize --club-id N --emoji 😔 --title "Повезёт в следующий раз" --weight 30 --empty
.venv\Scripts\python -m app.cli list-prizes --club-id N
```

## 4. Подключение бота

Токен вводится скрытым вводом (не попадает в историю команд):

```bash
.venv\Scripts\python -m app.cli connect-bot --club-id N
```

Платформа проверит токен, сохранит его зашифрованным, поставит вебхук, кнопку «Играть», описание и команды бота.
Если в `.env` задан `TEST_BOT_TOKEN`, команда возьмёт его — для реальных клубов оставьте `TEST_BOT_TOKEN` пустым.

## 5. Админы клуба

Админу нужен его Telegram id: пусть напишет боту клуба `/start`, затем id можно посмотреть в базе
(`guests.telegram_id`) или через любой бот вида @userinfobot.

```bash
.venv\Scripts\python -m app.cli add-staff --club-id N --telegram-id 123456789 --role admin
```

Владельцу — `--role owner`. В меню бота у сотрудника появится `/admin`.

## 6. QR-код для кассы

```bash
.venv\Scripts\python -m app.cli make-qr --club-id N --out ../qr/club-N.svg
```

SVG печатается в любом размере без потери качества. Ссылка ведёт на `t.me/<бот>?start=desk` и не зависит от адреса сервера.

## 7. Правила акции

Правила по умолчанию пустые — гость увидит «уточняйте у администратора». Задать текст (пока через базу, редактор — на этапе 2):

```sql
UPDATE clubs SET rules_text = '1. Крутить колесо можно раз в 7 дней. ...' WHERE id = N;
```

## 8. Оплата

После оплаты счётом или переводом:

```bash
.venv\Scripts\python -m app.cli extend-subscription --club-id N --days 30 --amount 20000 --comment "счёт №1"
```

`list-clubs` показывает статус подписки и дату, до которой оплачено.

## Проверка

1. Отсканировать QR → `/start` → поделиться номером → принять правила → «Играть» → крутить колесо.
2. Админ: `/admin` → отправить промокод → «Погасить» → гость получает «Приз получен».

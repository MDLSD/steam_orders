# Steam Buy-Order Telegram Notifier

Телеграм-бот, который уведомляет, когда срабатывают твои **ордера на покупку**
на торговой площадке Steam (CS2 и любые другие предметы).

## Возможности

- 🔔 **Уведомления о срабатывании ордеров**
  - ✅ полностью исполнен — имя предмета, цена, количество;
  - 🟡 частично исполнен — сколько куплено сейчас и сколько осталось.
- 🔄 **Контроль сессии Steam** — бот сам замечает, что cookie `steamLoginSecure`
  протухла, и пишет об этом в Telegram (один раз, без спама). После обновления
  cookie присылает «сессия восстановлена».
- 💬 **Команды в чате:**
  - `/orders` — показать все активные ордеры на покупку;
  - `/status` — состояние бота (жив ли, активна ли сессия, число ордеров, интервал);
  - `/setcookie <значение>` — обновить cookie `steamLoginSecure` прямо из Telegram,
    без захода на сервер. Новая cookie проверяется перед применением, сохраняется
    в `.env` (переживёт перезапуск), а сообщение с cookie бот сразу удаляет из чата;
  - `/start` · `/help` — справка.
- 🔒 **Безопасность** — секреты только в `.env` / переменных окружения, в коде их нет.
  Бот отвечает на команды **только** в разрешённый чат (`TELEGRAM_CHAT_ID`).

## Как это работает

HTML-страницы маркета Steam (`/market/`, `/market/mybuyorders/`) отдаются скриптам
как «разлогиненные» даже с валидной cookie. Поэтому бот обращается к AJAX-эндпоинту
`market/mylistings/`, который возвращает JSON — в его поле `results_html` лежат твои
ордера на покупку. Бот раз в `CHECK_INTERVAL` секунд снимает «снимок» ордеров,
сравнивает с предыдущим и при уменьшении количества / исчезновении ордера шлёт
уведомление в Telegram.

## Структура проекта

| Файл | Назначение |
|------|------------|
| `bot.py` | Основной скрипт бота |
| `test_session.py` | Диагностика cookie: проверяет авторизацию и печатает список ордеров |
| `.env.example` | Шаблон файла с секретами |
| `requirements.txt` | Зависимости Python |
| `steam-order-bot.service` | Unit-файл systemd для автозапуска |

## Установка

### 1. Получить данные

- **Бот и токен** — создай бота через [@BotFather](https://t.me/BotFather) (`/newbot`).
- **chat_id** — напиши своему боту любое сообщение, открой
  `https://api.telegram.org/bot<ТОКЕН>/getUpdates` и найди `"chat":{"id": ...}`.
- **Cookie `steamLoginSecure`** — залогинься на `steamcommunity.com`, открой
  DevTools → Application → Cookies → `steamcommunity.com` → скопируй значение
  `steamLoginSecure` целиком.

### 2. Установить и настроить

```bash
git clone https://github.com/MDLSD/steam_orders.git
cd steam_orders

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
nano .env          # вписать токен, chat_id, cookie
chmod 600 .env
```

Содержимое `.env`:

```env
TELEGRAM_TOKEN=токен_от_BotFather
TELEGRAM_CHAT_ID=твой_chat_id
STEAM_LOGIN_SECURE=значение_cookie
CHECK_INTERVAL=180
```

### 3. Проверить cookie

```bash
python3 test_session.py
```

Должно вывести `✅ Авторизация ОК` и список ордеров.

### 4. Запустить

```bash
python3 bot.py
```

## Автозапуск на VPS (systemd)

Создай unit-файл (укажи свои пути; пример — для пользователя `root` и папки
`/root/steam_orders`):

```bash
sudo tee /etc/systemd/system/steam-order-bot.service > /dev/null << 'EOF'
[Unit]
Description=Steam buy-order Telegram notifier
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=/root/steam_orders
EnvironmentFile=/root/steam_orders/.env
ExecStart=/root/steam_orders/venv/bin/python bot.py
Restart=always
RestartSec=15

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now steam-order-bot
sudo systemctl status steam-order-bot --no-pager
```

> ⚠️ В systemd все пути (`WorkingDirectory`, `EnvironmentFile`, `ExecStart`)
> должны быть **абсолютными** (начинаться с `/`).

### Полезные команды

| Действие | Команда |
|----------|---------|
| Статус | `sudo systemctl status steam-order-bot` |
| Логи | `journalctl -u steam-order-bot -f` |
| Перезапуск | `sudo systemctl restart steam-order-bot` |
| Остановить | `sudo systemctl stop steam-order-bot` |
| Обновить код | `git pull && sudo systemctl restart steam-order-bot` |

## Про cookie

Токен `steamLoginSecure` живёт примерно сутки и Steam периодически его меняет.
Когда он протухнет, бот пришлёт `⚠️ Сессия Steam протухла`. Обновить можно
двумя способами:

**Способ 1 (проще) — прямо из Telegram:**

```
/setcookie новое_значение_steamLoginSecure
```

Бот проверит cookie, сохранит её в `.env` и удалит твоё сообщение с cookie.
Перезапуск не нужен.

**Способ 2 — вручную на сервере:**

```bash
nano /root/steam_orders/.env        # заменить STEAM_LOGIN_SECURE
sudo systemctl restart steam-order-bot
```

## Настройки

| Переменная | По умолчанию | Описание |
|------------|--------------|----------|
| `TELEGRAM_TOKEN` | — | Токен бота от BotFather |
| `TELEGRAM_CHAT_ID` | — | ID чата для уведомлений |
| `STEAM_LOGIN_SECURE` | — | Cookie авторизации Steam |
| `CHECK_INTERVAL` | `180` | Интервал проверки ордеров, сек |

> Не ставь `CHECK_INTERVAL` слишком малым (рекомендуется ≥ 120 сек), чтобы не
> попасть под ограничения Steam.

## Дисклеймер

Бот использует неофициальные эндпоинты Steam и работает от имени твоей сессии.
Используй на свой риск; не делись cookie и файлом `.env`. Если cookie случайно
попала в публичный репозиторий — смени её (и токен бота) немедленно.

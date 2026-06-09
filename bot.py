#!/usr/bin/env python3
"""
Уведомления в Telegram, когда срабатывают твои ордера на покупку
на торговой площадке Steam (CS2 и любые другие предметы).

Возможности:
  - следит за ордерами на покупку и шлёт уведомление, когда ордер сработал;
  - сам определяет, что cookie/сессия Steam протухла, и предупреждает об этом;
  - отвечает на команды в чате:
        /orders     - показать все активные ордеры
        /status     - проверить, что бот жив
        /setcookie  - обновить cookie steamLoginSecure прямо из Telegram
        /start      - помощь

Все секреты берутся из переменных окружения / файла .env — в коде ничего нет.
"""

import os
import re
import sys
import time
import html
import requests

# Опционально подхватываем переменные из файла .env (если установлен python-dotenv).
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ============================ CONFIG ============================
def _require(name: str) -> str:
    val = os.environ.get(name)
    if not val:
        sys.exit(
            f"[!] Не задана переменная окружения {name}.\n"
            f"    Скопируй .env.example в .env и заполни значения, "
            f"либо экспортируй переменную вручную."
        )
    return val


TELEGRAM_TOKEN = _require("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = _require("TELEGRAM_CHAT_ID")
STEAM_LOGIN_SECURE = _require("STEAM_LOGIN_SECURE")

# Как часто проверять ордера (в секундах). Не ставь слишком мало, чтобы не словить бан.
CHECK_INTERVAL = int(os.environ.get("CHECK_INTERVAL", "180"))
# ===============================================================

# HTML-страницы маркета Steam отдаёт скриптам как «разлогиненные».
# Рабочий способ — AJAX-эндпоинт mylistings: он возвращает JSON, в котором
# в поле results_html лежат и продажи, и ордера на покупку (mybuyorder_*).
MY_LISTINGS_URL = "https://steamcommunity.com/market/mylistings/?count=100"
# Long-poll для команд: на столько секунд «висит» запрос getUpdates.
COMMAND_POLL_TIMEOUT = 20
# Путь к .env рядом со скриптом — для сохранения cookie через /setcookie.
ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")


class SessionExpired(Exception):
    """Steam считает нас разлогиненными — cookie протухла."""


# --------------------------- Telegram ---------------------------
def send_telegram(text: str, chat_id: str = TELEGRAM_CHAT_ID) -> None:
    # parse_mode не используем намеренно: спецсимволы (< > &) в названиях
    # предметов или подсказках иначе ломают отправку с ошибкой 400.
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        r = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=20)
        if not r.ok:
            print(f"[!] Telegram отклонил сообщение: {r.status_code} {r.text}", flush=True)
    except requests.RequestException as e:
        print(f"[!] Не удалось отправить в Telegram: {e}", flush=True)


def register_commands() -> None:
    """Регистрирует команды в меню Telegram (кнопка «/» в чате)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/setMyCommands"
    commands = [
        {"command": "orders", "description": "Показать активные ордеры"},
        {"command": "status", "description": "Статус бота"},
        {"command": "setcookie", "description": "Обновить cookie steamLoginSecure"},
        {"command": "help", "description": "Помощь"},
    ]
    try:
        requests.post(url, json={"commands": commands}, timeout=20)
    except requests.RequestException as e:
        print(f"[!] setMyCommands: {e}", flush=True)


def delete_message(chat_id: str, message_id: int) -> None:
    """Удаляет сообщение (используется, чтобы стереть из чата cookie)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/deleteMessage"
    try:
        requests.post(url, data={"chat_id": chat_id, "message_id": message_id}, timeout=20)
    except requests.RequestException:
        pass


def save_cookie_to_env(new_value: str) -> None:
    """Перезаписывает STEAM_LOGIN_SECURE в .env, чтобы значение пережило перезапуск."""
    lines = []
    found = False
    if os.path.exists(ENV_PATH):
        with open(ENV_PATH, "r", encoding="utf-8") as f:
            for line in f:
                if line.strip().startswith("STEAM_LOGIN_SECURE="):
                    lines.append(f"STEAM_LOGIN_SECURE={new_value}\n")
                    found = True
                else:
                    lines.append(line)
    if not found:
        lines.append(f"STEAM_LOGIN_SECURE={new_value}\n")
    with open(ENV_PATH, "w", encoding="utf-8") as f:
        f.writelines(lines)
    try:
        os.chmod(ENV_PATH, 0o600)
    except OSError:
        pass


def get_updates(offset):
    """Забирает новые сообщения боту (long-polling)."""
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": COMMAND_POLL_TIMEOUT}
    if offset is not None:
        params["offset"] = offset
    try:
        r = requests.get(url, params=params, timeout=COMMAND_POLL_TIMEOUT + 10)
        r.raise_for_status()
        return r.json().get("result", [])
    except (requests.RequestException, ValueError) as e:
        print(f"[!] getUpdates: {e}")
        return []


# ----------------------------- Steam ----------------------------
def fetch_buy_orders(cookie: str = None) -> dict:
    """
    Возвращает {order_id: {"name": ..., "qty": int, "price": str}}.
    Бросает SessionExpired, если Steam нас не узнал (cookie протухла).
    Если cookie не передана — берётся текущая STEAM_LOGIN_SECURE.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                      "AppleWebKit/537.36 (KHTML, like Gecko) "
                      "Chrome/124.0.0.0 Safari/537.36",
        "Referer": "https://steamcommunity.com/market/",
    }
    r = requests.get(
        MY_LISTINGS_URL,
        headers=headers,
        cookies={"steamLoginSecure": cookie or STEAM_LOGIN_SECURE},
        timeout=30,
    )
    # Истёкшая/битая cookie: Steam отвечает 400/401/403 (тело обычно "[]").
    if r.status_code in (400, 401, 403):
        print(f"[!] mylistings вернул {r.status_code} — cookie протухла.", flush=True)
        raise SessionExpired()

    # Прочие HTTP-ошибки (5xx и т.п.) — временные, пусть будут сетевой ошибкой.
    r.raise_for_status()

    # Залогиненному отдаётся JSON-объект с success=true. Иначе — cookie протухла.
    try:
        data = r.json()
    except ValueError:
        print(f"[!] mylistings вернул не JSON (статус {r.status_code}) — cookie протухла.", flush=True)
        raise SessionExpired()
    if not isinstance(data, dict) or not data.get("success"):
        print(f"[!] mylistings success={data.get('success') if isinstance(data, dict) else '?'} "
              f"(статус {r.status_code}) — cookie протухла.", flush=True)
        raise SessionExpired()

    page = data.get("results_html", "")

    orders = {}
    blocks = re.split(r'id="mybuyorder_(\d+)"', page)
    for i in range(1, len(blocks) - 1, 2):
        order_id = blocks[i]
        chunk = blocks[i + 1]

        # Количество и цена лежат внутри market_listing_price:
        #   <span class="market_listing_inline_buyorder_qty">2 @</span> 148₴
        qty_match = re.search(r'market_listing_inline_buyorder_qty">\s*(\d+)\s*@', chunk)
        qty = int(qty_match.group(1)) if qty_match else 1

        price_match = re.search(
            r'market_listing_inline_buyorder_qty">[^<]*</span>\s*([^<]+?)\s*</span>',
            chunk, re.S,
        )
        price = html.unescape(price_match.group(1).strip()) if price_match else "?"

        name_match = re.search(r'market_listing_item_name[^>]*>(.*?)</span>', chunk, re.S)
        name = html.unescape(re.sub(r"<[^>]+>", "", name_match.group(1)).strip()) if name_match else "?"

        orders[order_id] = {"name": name, "qty": qty, "price": price}

    return orders


def format_orders(orders: dict) -> str:
    if not orders:
        return "📭 Активных ордеров на покупку нет."
    lines = [f"📋 Активных ордеров: {len(orders)}\n"]
    for info in orders.values():
        lines.append(f"🎯 {info['name']}\n   💰 {info['price']} × {info['qty']} шт.")
    return "\n".join(lines)


def diff_and_notify(old: dict, new: dict) -> None:
    for order_id, info in old.items():
        if order_id not in new:
            send_telegram(
                f"✅ Ордер сработал ПОЛНОСТЬЮ\n"
                f"🎯 {info['name']}\n"
                f"💰 {info['price']}\n"
                f"📦 куплено: {info['qty']} шт."
            )
        else:
            old_qty, new_qty = info["qty"], new[order_id]["qty"]
            if new_qty < old_qty:
                send_telegram(
                    f"🟡 Ордер сработал ЧАСТИЧНО\n"
                    f"🎯 {info['name']}\n"
                    f"💰 {info['price']}\n"
                    f"📦 куплено сейчас: {old_qty - new_qty} шт. (осталось {new_qty})"
                )


# ----------------------------- Loop -----------------------------
def handle_command(msg: dict, state: dict) -> None:
    global STEAM_LOGIN_SECURE
    text = msg["text"]
    chat_id = str(msg["chat"]["id"])
    cmd = text.strip().split()[0].lower().split("@")[0]
    print(f"[команда] получено: {cmd!r} от чата {chat_id}", flush=True)

    if cmd == "/orders":
        if not state["session_ok"]:
            send_telegram("⚠️ Сессия Steam протухла — список недоступен. Обнови cookie: /setcookie", chat_id)
        else:
            send_telegram(format_orders(state["orders"]), chat_id)

    elif cmd == "/status":
        line = "🟢 сессия Steam активна" if state["session_ok"] else "🔴 сессия Steam протухла"
        send_telegram(
            f"🤖 Бот работает.\n{line}\n"
            f"⏱ интервал проверки: {CHECK_INTERVAL} сек\n"
            f"📋 ордеров сейчас: {len(state['orders'])}",
            chat_id,
        )

    elif cmd == "/setcookie":
        # Сразу удаляем сообщение с cookie из чата — это секрет.
        delete_message(chat_id, msg["message_id"])
        parts = text.strip().split(maxsplit=1)
        if len(parts) < 2 or not parts[1].strip():
            send_telegram(
                "Использование: /setcookie [значение steamLoginSecure]\n"
                "Сообщение с cookie я удалю автоматически.",
                chat_id,
            )
            return
        new_cookie = parts[1].strip()
        send_telegram("⏳ Проверяю новую cookie...", chat_id)
        try:
            orders = fetch_buy_orders(new_cookie)
        except SessionExpired:
            send_telegram("❌ Эта cookie недействительна — Steam не узнал. Старая cookie оставлена.", chat_id)
            return
        except requests.RequestException as e:
            send_telegram(f"⚠️ Не удалось проверить cookie (ошибка сети): {e}", chat_id)
            return
        # Cookie рабочая — применяем и сохраняем.
        STEAM_LOGIN_SECURE = new_cookie
        save_cookie_to_env(new_cookie)
        state["orders"] = orders
        state["session_ok"] = True
        send_telegram(
            f"✅ Cookie обновлена и сохранена в .env.\n📋 Активных ордеров: {len(orders)}",
            chat_id,
        )

    elif cmd in ("/start", "/help"):
        send_telegram(
            "Привет! Я слежу за твоими ордерами на покупку в Steam.\n\n"
            "Команды:\n"
            "/orders — показать активные ордеры\n"
            "/status — проверить, что бот жив\n"
            "/setcookie [значение] — обновить cookie steamLoginSecure",
            chat_id,
        )

    elif cmd.startswith("/"):
        send_telegram(
            f"Неизвестная команда: {cmd}\n"
            "Доступно: /orders /status /setcookie /help",
            chat_id,
        )


def main() -> None:
    print("Бот запущен.", flush=True)
    register_commands()
    state = {"orders": {}, "session_ok": True}
    offset = None
    last_order_check = 0.0
    initialized = False

    while True:
        # --- проверка ордеров по таймеру ---
        if time.time() - last_order_check >= CHECK_INTERVAL:
            last_order_check = time.time()
            try:
                cur = fetch_buy_orders()
                if not state["session_ok"]:
                    state["session_ok"] = True
                    send_telegram("✅ Сессия Steam восстановлена. Слежу за ордерами дальше.")
                if not initialized:
                    state["orders"] = cur
                    initialized = True
                    print(f"Старт: активных ордеров {len(cur)}")
                else:
                    diff_and_notify(state["orders"], cur)
                    state["orders"] = cur
                    print(f"Проверено. Активных ордеров: {len(cur)}")
            except SessionExpired:
                if state["session_ok"]:
                    state["session_ok"] = False
                    send_telegram(
                        "⚠️ Сессия Steam протухла — cookie steamLoginSecure больше не действует.\n"
                        "Обнови её прямо здесь командой:\n/setcookie [новое значение]"
                    )
                print("[!] Сессия Steam протухла.")
            except requests.RequestException as e:
                print(f"[!] Ошибка запроса к Steam: {e}")

        # --- обработка команд (заодно это и пауза через long-poll) ---
        for upd in get_updates(offset):
            offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("channel_post")
            if not msg or "text" not in msg:
                continue
            # отвечаем только в разрешённый чат
            if str(msg["chat"]["id"]) != str(TELEGRAM_CHAT_ID):
                continue
            handle_command(msg, state)


if __name__ == "__main__":
    main()

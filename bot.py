#!/usr/bin/env python3
"""
Уведомления в Telegram, когда срабатывают твои ордера на покупку
на торговой площадке Steam (CS2 и любые другие предметы).

Возможности:
  - следит за ордерами на покупку и шлёт уведомление, когда ордер сработал;
  - сам определяет, что cookie/сессия Steam протухла, и предупреждает об этом;
  - отвечает на команды в чате:
        /orders  - показать все активные ордеры
        /status  - проверить, что бот жив
        /start   - помощь

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

MY_BUY_ORDERS_URL = "https://steamcommunity.com/market/mybuyorders/"
# Long-poll для команд: на столько секунд «висит» запрос getUpdates.
COMMAND_POLL_TIMEOUT = 20


class SessionExpired(Exception):
    """Steam считает нас разлогиненными — cookie протухла."""


# --------------------------- Telegram ---------------------------
def send_telegram(text: str, chat_id: str = TELEGRAM_CHAT_ID) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        requests.post(
            url,
            data={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=20,
        )
    except requests.RequestException as e:
        print(f"[!] Не удалось отправить в Telegram: {e}")


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
def fetch_buy_orders() -> dict:
    """
    Возвращает {order_id: {"name": ..., "qty": int, "price": str}}.
    Бросает SessionExpired, если Steam нас не узнал (cookie протухла).
    """
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Cookie": f"steamLoginSecure={STEAM_LOGIN_SECURE}",
    }
    r = requests.get(MY_BUY_ORDERS_URL, headers=headers, timeout=30)
    r.raise_for_status()

    page = r.text
    try:
        data = r.json()
        if isinstance(data, dict) and "results_html" in data:
            page = data["results_html"]
    except ValueError:
        pass

    # Признак авторизации: Steam пишет g_steamID = "765..." когда мы залогинены,
    # и g_steamID = false когда сессия недействительна.
    m = re.search(r'g_steamID\s*=\s*(false|"\d+")', page)
    if m and m.group(1) == "false":
        raise SessionExpired()

    orders = {}
    blocks = re.split(r'id="mybuyorder_(\d+)"', page)
    for i in range(1, len(blocks) - 1, 2):
        order_id = blocks[i]
        chunk = blocks[i + 1]

        qty_match = re.search(r'market_listing_buyorder_qty[^>]*>\s*(\d+)', chunk)
        qty = int(qty_match.group(1)) if qty_match else 1

        name_match = re.search(r'market_listing_item_name[^>]*>(.*?)</span>', chunk, re.S)
        name = html.unescape(re.sub(r"<[^>]+>", "", name_match.group(1)).strip()) if name_match else "?"

        price_match = re.search(r'market_listing_price[^>]*>(.*?)</span>', chunk, re.S)
        price = html.unescape(re.sub(r"<[^>]+>", "", price_match.group(1)).strip()) if price_match else "?"

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
def handle_command(text: str, chat_id: str, last_orders: dict, session_ok: bool) -> None:
    cmd = text.strip().split()[0].lower().split("@")[0]
    if cmd == "/orders":
        if not session_ok:
            send_telegram("⚠️ Сессия Steam протухла — список недоступен. Обнови cookie.", chat_id)
        else:
            send_telegram(format_orders(last_orders), chat_id)
    elif cmd == "/status":
        state = "🟢 сессия Steam активна" if session_ok else "🔴 сессия Steam протухла"
        send_telegram(
            f"🤖 Бот работает.\n{state}\n"
            f"⏱ интервал проверки: {CHECK_INTERVAL} сек\n"
            f"📋 ордеров сейчас: {len(last_orders)}",
            chat_id,
        )
    elif cmd in ("/start", "/help"):
        send_telegram(
            "Привет! Я слежу за твоими ордерами на покупку в Steam.\n\n"
            "Команды:\n"
            "/orders — показать активные ордеры\n"
            "/status — проверить, что бот жив",
            chat_id,
        )


def main() -> None:
    print("Бот запущен.")
    last_orders: dict = {}
    session_ok = True
    offset = None
    last_order_check = 0.0
    initialized = False

    while True:
        # --- проверка ордеров по таймеру ---
        if time.time() - last_order_check >= CHECK_INTERVAL:
            last_order_check = time.time()
            try:
                cur = fetch_buy_orders()
                if not session_ok:
                    session_ok = True
                    send_telegram("✅ Сессия Steam восстановлена. Слежу за ордерами дальше.")
                if not initialized:
                    last_orders = cur
                    initialized = True
                    print(f"Старт: активных ордеров {len(cur)}")
                else:
                    diff_and_notify(last_orders, cur)
                    last_orders = cur
                    print(f"Проверено. Активных ордеров: {len(cur)}")
            except SessionExpired:
                if session_ok:
                    session_ok = False
                    send_telegram(
                        "⚠️ Сессия Steam протухла — cookie steamLoginSecure больше не действует.\n"
                        "Обнови значение STEAM_LOGIN_SECURE и перезапусти бота."
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
            chat_id = str(msg["chat"]["id"])
            # отвечаем только в разрешённый чат
            if chat_id != str(TELEGRAM_CHAT_ID):
                continue
            handle_command(msg["text"], chat_id, last_orders, session_ok)


if __name__ == "__main__":
    main()

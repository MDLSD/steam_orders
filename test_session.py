#!/usr/bin/env python3
"""
Диагностика cookie steamLoginSecure.
Запуск:  python3 test_session.py
Читает STEAM_LOGIN_SECURE из .env / окружения, проверяет авторизацию
через рабочий AJAX-эндпоинт маркета и печатает список ордеров на покупку.
"""

import os
import re
import sys
import html
import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

cookie = os.environ.get("STEAM_LOGIN_SECURE")
if not cookie:
    sys.exit("[!] STEAM_LOGIN_SECURE не задана (заполни .env).")
cookie = cookie.strip()

print(f"Длина cookie: {len(cookie)} символов")
if len(cookie) < 100:
    print("[!] Подозрительно коротко — возможно, значение обрезано при копировании.")

headers = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/124.0.0.0 Safari/537.36",
    "Referer": "https://steamcommunity.com/market/",
}
r = requests.get(
    "https://steamcommunity.com/market/mylistings/?count=100",
    headers=headers,
    cookies={"steamLoginSecure": cookie},
    timeout=30,
)
print(f"HTTP статус: {r.status_code}")

try:
    data = r.json()
except ValueError:
    sys.exit("❌ Ответ не JSON — cookie недействительна (протухла / не тот домен / разлогинен).")

if not data.get("success"):
    sys.exit("❌ success=false — Steam не узнал тебя, cookie недействительна.")

page = data.get("results_html", "")
blocks = re.split(r'id="mybuyorder_(\d+)"', page)
count = 0
print("✅ Авторизация ОК! Активные ордеры на покупку:")
for i in range(1, len(blocks) - 1, 2):
    chunk = blocks[i + 1]
    qm = re.search(r'market_listing_inline_buyorder_qty">\s*(\d+)\s*@', chunk)
    qty = int(qm.group(1)) if qm else 1
    pm = re.search(r'market_listing_inline_buyorder_qty">[^<]*</span>\s*([^<]+?)\s*</span>', chunk, re.S)
    price = html.unescape(pm.group(1).strip()) if pm else "?"
    nm = re.search(r'market_listing_item_name[^>]*>(.*?)</span>', chunk, re.S)
    name = html.unescape(re.sub(r"<[^>]+>", "", nm.group(1)).strip()) if nm else "?"
    count += 1
    print(f"  x{qty}  {price:<10}  {name}")

print(f"Итого ордеров: {count}")

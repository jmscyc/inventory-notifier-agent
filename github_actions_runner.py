#!/usr/bin/env python3
"""
GitHub Actions 專用無伺服器雲端監控核心腳本
- 不需要本機開機、不需要 CMD
- 每 15 分鐘由 GitHub 伺服器在雲端自動執行
- 若發現有現貨，立即發送 Telegram 即時通知至使用者手機
"""

import asyncio
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

import httpx
from bs4 import BeautifulSoup

try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "tracked_products.json"
STATUS_FILE = BASE_DIR / "last_check_status.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()

def load_tracked_items() -> list:
    if not DATA_FILE.exists():
        print(f"⚠️ 找不到 {DATA_FILE}，建立預設追蹤檔案...")
        default_items = [
            {
                "id": "item-142997",
                "name": "科克蘭 三層抽取式衛生紙 120張 X 24包",
                "target_url": "https://www.costco.com.tw/p/142997",
                "in_stock": False,
                "status_notes": "初始化",
                "last_checked": ""
            }
        ]
        save_tracked_items(default_items)
        return default_items
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"讀取商品清單失敗: {e}")
        return []

def save_tracked_items(items: list):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"儲存商品清單失敗: {e}")

def check_product_inventory(url: str) -> dict:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }
    now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    try:
        with httpx.Client(follow_redirects=True, timeout=20.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html_content = resp.text
    except Exception as e:
        return {
            "url": url,
            "title": "連線失敗",
            "in_stock": False,
            "status_description": f"抓取失敗: {str(e)}",
            "checked_at": now_str,
        }

    soup = BeautifulSoup(html_content, "html.parser")
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.h1:
        title = soup.h1.get_text().strip()
    else:
        title = url

    # Costco 頁面特徵精準判定
    if "costco.com" in url:
        costco_oos_btn = soup.find(
            lambda t: t.name == "button"
            and ("outOfStock" in t.get("class", []) or "缺貨" in t.get_text())
        )
        costco_pdp_add_btn = soup.find("button", id="add-to-cart-button")
        match_code = re.search(r"/p/(\d+)", url)
        product_code = match_code.group(1) if match_code else ""
        pdp_qty = (
            soup.find("input", id=f"quantity_{product_code}")
            if product_code
            else None
        )
        has_qty_selector = pdp_qty is not None and "pdp-input" in pdp_qty.get("class", [])

        if costco_oos_btn:
            in_stock = False
            status_desc = "缺貨 (顯示缺貨按鈕)"
        elif costco_pdp_add_btn and has_qty_selector:
            in_stock = True
            status_desc = "有現貨 (確認有數量選單及加入購物車按鈕)"
        elif costco_pdp_add_btn:
            in_stock = True
            status_desc = "有現貨 (具備加入購物車按鈕)"
        else:
            in_stock = False
            status_desc = "缺貨 (未發現加入購物車按鈕)"

        return {
            "url": url,
            "title": title,
            "in_stock": in_stock,
            "status_description": status_desc,
            "checked_at": now_str,
        }

    # 一般電商判定
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()
    page_text = soup.get_text(separator=" ", strip=True).lower()
    out_of_stock_patterns = [r"缺貨中", r"已售完", r"售完", r"已售罄", r"補貨中", r"暫無庫存", r"out of stock", r"sold out"]
    in_stock_patterns = [r"加入購物車", r"立即購買", r"尚有庫存", r"有現貨", r"現貨", r"add to cart", r"buy now"]

    disabled_cart = bool(
        soup.find(
            lambda tag: tag.name in ["button", "input", "a"]
            and ("disabled" in tag.attrs or "outOfStock" in tag.get("class", []))
            and any(k in tag.get_text().lower() for k in ["cart", "buy", "購買", "購物車", "缺貨"])
        )
    )

    has_oos_text = any(re.search(p, page_text) for p in out_of_stock_patterns)
    has_in_stock_text = any(re.search(p, page_text) for p in in_stock_patterns)

    if disabled_cart or has_oos_text:
        in_stock = False
        status_desc = "缺貨 (符合缺貨關鍵字或按鈕停用)"
    elif has_in_stock_text:
        in_stock = True
        status_desc = "有現貨 (偵測到可購買關鍵字)"
    else:
        in_stock = False
        status_desc = "狀態不明 (預設保守判定為缺貨)"

    return {
        "url": url,
        "title": title,
        "in_stock": in_stock,
        "status_description": status_desc,
        "checked_at": now_str,
    }

async def send_telegram_alert(client: httpx.AsyncClient, title: str, body: str, target_url: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        print("⚠️ 未設定 TELEGRAM_BOT_TOKEN 或 TELEGRAM_CHAT_ID，跳過 Telegram 訊息發送。")
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{title}\n\n{body}",
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if target_url:
        payload["reply_markup"] = {
            "inline_keyboard": [[{"text": "🛒 立刻前往搶購 / 結帳", "url": target_url}]]
        }

    try:
        resp = await client.post(api_url, json=payload, timeout=15.0)
        resp.raise_for_status()
        print(f"✅ Telegram 警報發送成功: {title}")
    except Exception as e:
        print(f"❌ Telegram 發送失敗: {e}")

async def main():
    print("=" * 60)
    print("🚀 啟動 GitHub Actions 雲端巡檢作業...")
    now_taipei = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"🕒 執行時間 (台灣時間): {now_taipei}")
    print("=" * 60)

    items = load_tracked_items()
    if not items:
        print("ℹ️ 目前無追蹤商品。")
        sys.exit(0)

    in_stock_found = []
    oos_found = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for it in items:
            t_url = it.get("target_url")
            if not t_url:
                continue

            prev_stock = it.get("in_stock", False)
            print(f"🔍 正在檢查: {it.get('name', t_url)} ...")
            c_res = check_product_inventory(t_url)

            is_now_stock = c_res.get("in_stock", False)
            status_desc = c_res.get("status_description", "")
            checked_at = c_res.get("checked_at", now_taipei)

            it["in_stock"] = is_now_stock
            it["status_notes"] = status_desc
            it["last_checked"] = checked_at
            if c_res.get("title") and c_res.get("title") != t_url:
                it["name"] = c_res.get("title")

            if is_now_stock:
                in_stock_found.append(it)
                print(f"🎉【發現有現貨！】{it['name']}")
                # 若先前是缺貨或剛補貨，立即發出高優先級通知！
                await send_telegram_alert(
                    client,
                    f"🚨 **【GitHub 雲端緊急補貨通知！】**",
                    f"📦 **商品**：{it['name']}\n🟢 **狀態**：{status_desc}\n⏰ **時間**：`{checked_at}`\n\n⚡ 請在 10 分鐘內點擊下方按鈕結帳！",
                    t_url
                )
            else:
                oos_found.append(it)
                print(f"🔴【依然缺貨中】{it['name']} ({status_desc})")

    # 存檔最新狀態
    save_tracked_items(items)

    status_data = {
        "last_checked_taipei": now_taipei,
        "total_items": len(items),
        "in_stock_count": len(in_stock_found),
        "out_of_stock_count": len(oos_found),
        "summary": f"共檢查 {len(items)} 件 (現貨: {len(in_stock_found)} / 缺貨: {len(oos_found)})"
    }
    with open(STATUS_FILE, "w", encoding="utf-8") as f:
        json.dump(status_data, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"🏁 巡檢完成！{status_data['summary']}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Telegram 實時即時回應服務 (常駐背景處理按鈕點擊與網址新增)
- 點按鈕毫秒級即時反應，不再轉圈圈
- 可在本機背景隨 Windows 開機常駐，或在任何環境執行
"""

import asyncio
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, Any, List

import httpx
from bs4 import BeautifulSoup

try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "tracked_products.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9200030096:AAFNpPRV2zbQj4DFya0pTSfLi7pUDG7_MR0").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "807996552").strip()

def load_data():
    default_config = {
        "is_active": True,
        "quiet_hours_enabled": True,
        "quiet_hours_start": 1,
        "quiet_hours_end": 5,
        "oos_periodic_notify_hours": 4,
        "telegram": {"enabled": True}
    }
    if not DATA_FILE.exists():
        return default_config, []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data.get("settings", default_config), data.get("products", [])
    except Exception:
        return default_config, []

def save_data(settings, products):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"settings": settings, "products": products}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"存檔失敗: {e}")

def get_main_menu():
    return {
        "inline_keyboard": [
            [
                {"text": "📋 檢視商品清單", "callback_data": "menu_list"},
                {"text": "🔍 立即觸發全檢", "callback_data": "menu_check"}
            ],
            [
                {"text": "⚙️ 系統設定儀表板", "callback_data": "menu_status"},
                {"text": "🌙 快速切換靜音", "callback_data": "toggle_quiet"}
            ],
            [
                {"text": "⚡ 開啟/暫停總監控", "callback_data": "toggle_active"},
                {"text": "❓ 操作與功能說明", "callback_data": "menu_help"}
            ]
        ]
    }

async def handle_callback(client: httpx.AsyncClient, cb: dict):
    cb_id = cb.get("id")
    cb_data = cb.get("data", "")
    from_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    msg_id = cb.get("message", {}).get("message_id")

    # 立即消除手機按鈕轉圈圈
    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id})

    settings, products = load_data()

    if cb_data == "menu_main" or cb_data == "menu_help":
        help_text = (
            "🎛️ **【好市多雲端智慧監控控制台】**\n\n"
            "直接點選下方任一按鈕進行操作：\n\n"
            "• **貼網址新增**：直接在對話框貼上好市多商品網址即可新增\n"
            "• **檢視清單**：查看目前監控中的商品，並可單鍵刪除\n"
            "• **立即全檢**：即時抓取所有商品最新庫存\n"
            "• **開關與靜音**：單鍵切換總監控或夜間防打擾"
        )
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
            "chat_id": from_chat_id,
            "message_id": msg_id,
            "text": help_text,
            "parse_mode": "Markdown",
            "reply_markup": get_main_menu()
        })

    elif cb_data == "menu_list":
        if not products:
            reply = "📋 目前尚未追蹤任何商品。直接貼上好市多商品網址即可新增！"
            kb = get_main_menu()
        else:
            reply = "📋 **【目前監控中的商品清單】**\n\n"
            btn_rows = []
            for idx, p in enumerate(products, 1):
                p_name = p.get("name") or p.get("url")
                p_status = p.get("last_status", "巡檢中")
                p_id = p.get("id")
                reply += f"{idx}. **{p_name}**\n   狀態：`{p_status}`\n   編號：`{p_id}`\n\n"
                btn_rows.append([
                    {"text": f"🛒 前往 #{idx}", "url": p.get("url", "")},
                    {"text": f"🗑️ 刪除 #{idx}", "callback_data": f"del_{p_id}"}
                ])
            btn_rows.append([{"text": "🔙 返回主選單", "callback_data": "menu_main"}])
            kb = {"inline_keyboard": btn_rows}

        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
            "chat_id": from_chat_id,
            "message_id": msg_id,
            "text": reply,
            "parse_mode": "Markdown",
            "reply_markup": kb
        })

    elif cb_data.startswith("del_"):
        del_id = cb_data.replace("del_", "").strip()
        before_cnt = len(products)
        products = [p for p in products if str(p.get("id")) != del_id]
        if len(products) < before_cnt:
            save_data(settings, products)
            del_reply = f"🗑️ 已成功將商品 (編號 `{del_id}`) 從清單中刪除！"
        else:
            del_reply = "⚠️ 找不到該商品。"
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
            "chat_id": from_chat_id,
            "text": del_reply,
            "parse_mode": "Markdown",
            "reply_markup": get_main_menu()
        })

    elif cb_data == "menu_status":
        now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
        is_act = "🟢 運作中 (ON)" if settings.get("is_active", True) else "🔴 已暫停 (OFF)"
        q_on = "🟢 已啟用" if settings.get("quiet_hours_enabled", True) else "⚪ 已停用"
        q_range = f"{settings.get('quiet_hours_start', 1):02d}:00 ~ {settings.get('quiet_hours_end', 5):02d}:00"
        oos_h = f"{settings.get('oos_periodic_notify_hours', 4)} 小時"

        status_reply = (
            f"📊 **【系統目前狀態儀表板】**\n\n"
            f"⚡ **總監控狀態**：{is_act}\n"
            f"⏰ **巡檢週期**：每 `15 分鐘` (GitHub 24/7 全自動)\n"
            f"🌙 **夜間靜音時段**：{q_on} (`{q_range}`)\n"
            f"📢 **缺貨提醒週期**：每 `{oos_h}` 一次\n"
            f"📦 **商品數量**：共 `{len(products)}` 件\n"
            f"🕒 **伺服器時間**：`{now_str}`"
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "⚡ 切換總開關 (ON/OFF)", "callback_data": "toggle_active"},
                    {"text": "🌙 切換靜音 (開/關)", "callback_data": "toggle_quiet"}
                ],
                [
                    {"text": "🔍 立即巡檢一次", "callback_data": "menu_check"},
                    {"text": "🔙 返回主選單", "callback_data": "menu_main"}
                ]
            ]
        }
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json={
            "chat_id": from_chat_id,
            "message_id": msg_id,
            "text": status_reply,
            "parse_mode": "Markdown",
            "reply_markup": kb
        })

    elif cb_data == "toggle_active":
        settings["is_active"] = not settings.get("is_active", True)
        save_data(settings, products)
        act_state = "🟢 運作中 (ON)" if settings["is_active"] else "🔴 已暫停 (OFF)"
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
            "chat_id": from_chat_id,
            "text": f"⚡ **總監控狀態已切換為**：{act_state}",
            "parse_mode": "Markdown",
            "reply_markup": get_main_menu()
        })

    elif cb_data == "toggle_quiet":
        settings["quiet_hours_enabled"] = not settings.get("quiet_hours_enabled", True)
        save_data(settings, products)
        q_state = "🟢 已開啟夜間防打擾" if settings["quiet_hours_enabled"] else "☀️ 已關閉夜間防打擾 (24H通知)"
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
            "chat_id": from_chat_id,
            "text": f"🌙 **夜間靜音設定已切換**：{q_state}",
            "parse_mode": "Markdown",
            "reply_markup": get_main_menu()
        })

    elif cb_data == "menu_check":
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
            "chat_id": from_chat_id,
            "text": "🔍 **收到立即巡檢要求！** 正在即時抓取所有商品最新庫存...",
            "parse_mode": "Markdown"
        })
        # 立即在此爬蟲回覆
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res_text = "🏁 **【立即巡檢即時結果】**\n\n"
        for p in products:
            p_url = p.get("url")
            p_name = p.get("name", p_url)
            try:
                r = await client.get(p_url, headers=headers, timeout=15.0)
                soup = BeautifulSoup(r.text, "html.parser")
                costco_oos_btn = soup.find(lambda t: t.name == "button" and ("outOfStock" in t.get("class", []) or "缺貨" in t.get_text()))
                costco_pdp_add_btn = soup.find("button", id="add-to-cart-button")
                in_stock = bool(costco_pdp_add_btn and not costco_oos_btn)
                st = "🟢 有現貨！" if in_stock else "🔴 缺貨中"
                p["last_status"] = st
                res_text += f"• **{p_name}**：`{st}`\n"
            except Exception as e:
                res_text += f"• **{p_name}**：`抓取失敗`\n"
        save_data(settings, products)
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
            "chat_id": from_chat_id,
            "text": res_text,
            "parse_mode": "Markdown",
            "reply_markup": get_main_menu()
        })

async def main():
    print("🤖 Telegram 實時即時按鈕監聽服務已啟動...")
    offset = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 20}
                )
                if resp.status_code == 200:
                    data = resp.json()
                    for upd in data.get("result", []):
                        offset = upd["update_id"] + 1
                        if "callback_query" in upd:
                            asyncio.create_task(handle_callback(client, upd["callback_query"]))
                        elif "message" in upd and "text" in upd["message"]:
                            msg = upd["message"]
                            text = msg["text"].strip()
                            chat_id = str(msg["chat"]["id"])
                            # 貼網址直接新增
                            url_match = re.search(r"https?://[^\s]+costco\.com[^\s]+", text)
                            if url_match:
                                new_url = url_match.group(0)
                                settings, products = load_data()
                                code_match = re.search(r"/p/(\d+)", new_url)
                                p_code = code_match.group(1) if code_match else str(int(time.time()))
                                if not any(p.get("url") == new_url for p in products):
                                    products.append({
                                        "id": p_code,
                                        "name": f"Costco 商品 ({p_code})",
                                        "url": new_url,
                                        "enabled": True,
                                        "last_status": "新加入待巡檢",
                                        "last_checked": datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                                    })
                                    save_data(settings, products)
                                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                                        "chat_id": chat_id,
                                        "text": f"✅ 已成功將商品 `{p_code}` 加入監控清單！",
                                        "parse_mode": "Markdown",
                                        "reply_markup": get_main_menu()
                                    })
                            else:
                                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                                    "chat_id": chat_id,
                                    "text": "🎛️ **請點選下方按鈕操作**：",
                                    "parse_mode": "Markdown",
                                    "reply_markup": get_main_menu()
                                })
            except Exception as e:
                await asyncio.sleep(2)

if __name__ == "__main__":
    asyncio.run(main())

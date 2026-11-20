#!/usr/bin/env python3
"""
Telegram 實時即時回應服務 (底部實體常駐鍵盤 + 訊息互動旗艦版)
- 4 鍵極簡控制台常駐在打字框底部 (ReplyKeyboard)，永遠不被對話推走！
- 隨時點擊 4 鍵任一按鈕，0.2 秒必開必回
- 支援貼網址自動新增
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
        "poll_interval_minutes": 15,
        "oos_periodic_notify_minutes": 240,
        "quiet_hours_enabled": True,
        "quiet_hours_start": 1,
        "quiet_hours_end": 5,
        "telegram": {"enabled": True}
    }
    if not DATA_FILE.exists():
        return default_config, []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            s = data.get("settings", default_config)
            if "oos_periodic_notify_hours" in s and "oos_periodic_notify_minutes" not in s:
                s["oos_periodic_notify_minutes"] = int(s.get("oos_periodic_notify_hours", 4) * 60)
            return s, data.get("products", [])
    except Exception:
        return default_config, []

def save_data(settings, products):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump({"settings": settings, "products": products}, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"存檔失敗: {e}")

def get_persistent_reply_keyboard():
    """常駐在打字框下方、永遠不被對話推走的 4 鍵極簡鍵盤"""
    return {
        "keyboard": [
            [
                {"text": "🛒 商品清單 / 管理"},
                {"text": "🔍 立即檢查庫存"}
            ],
            [
                {"text": "⏱️ 設定檢查與通知頻率"},
                {"text": "⚙️ 系統開關與狀態"}
            ]
        ],
        "resize_keyboard": True,
        "is_persistent": True
    }

async def send_msg(client: httpx.AsyncClient, chat_id: str, text: str, inline_keyboard: list = None):
    """發送訊息並始終附帶常駐 4 鍵鍵盤，確保隨時可按"""
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "reply_markup": {"inline_keyboard": inline_keyboard} if inline_keyboard else get_persistent_reply_keyboard()
    }
    try:
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=payload, timeout=8.0)
    except Exception as e:
        print(f"發送訊息失敗: {e}")

async def handle_callback(client: httpx.AsyncClient, cb: dict):
    cb_id = cb.get("id")
    cb_data = cb.get("data", "")
    from_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    msg_id = cb.get("message", {}).get("message_id")

    try:
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id}, timeout=3.0)
    except Exception:
        pass

    settings, products = load_data()

    if cb_data.startswith("del_"):
        del_id = cb_data.replace("del_", "").strip()
        before_cnt = len(products)
        products = [p for p in products if str(p.get("id")) != del_id]
        if len(products) < before_cnt:
            save_data(settings, products)
            await send_msg(client, from_chat_id, f"🗑️ 已成功將商品 (編號 `{del_id}`) 從清單中刪除！")
        else:
            await send_msg(client, from_chat_id, "⚠️ 找不到該商品。")

    elif cb_data.startswith("set_poll_"):
        new_m = int(cb_data.replace("set_poll_", ""))
        settings["poll_interval_minutes"] = new_m
        save_data(settings, products)
        await send_msg(client, from_chat_id, f"✅ **【巡檢頻率已更新】**：目前設定為每 `{new_m} 分鐘` 檢查一次！")

    elif cb_data.startswith("set_oos_"):
        new_m = int(cb_data.replace("set_oos_", ""))
        settings["oos_periodic_notify_minutes"] = new_m
        settings["oos_periodic_notify_hours"] = round(new_m / 60, 1)
        save_data(settings, products)
        await send_msg(client, from_chat_id, f"✅ **【缺貨提醒頻率已更新】**：目前設定為每 `{new_m} 分鐘` ({new_m//60}小時) 重複提醒一次！")

    elif cb_data == "toggle_active":
        settings["is_active"] = not settings.get("is_active", True)
        save_data(settings, products)
        act_state = "🟢 運作中 (ON)" if settings["is_active"] else "🔴 已暫停 (OFF)"
        await send_msg(client, from_chat_id, f"⚡ **總監控狀態已切換為**：{act_state}")

    elif cb_data == "toggle_quiet":
        settings["quiet_hours_enabled"] = not settings.get("quiet_hours_enabled", True)
        save_data(settings, products)
        q_state = "🟢 已開啟夜間防打擾" if settings["quiet_hours_enabled"] else "☀️ 已關閉夜間防打擾 (24H通知)"
        await send_msg(client, from_chat_id, f"🌙 **夜間靜音設定已切換**：{q_state}")

async def handle_text_command(client: httpx.AsyncClient, chat_id: str, text: str):
    settings, products = load_data()

    # 1. 🛒 商品清單 / 管理
    if text == "🛒 商品清單 / 管理" or text in ["/list", "清單"]:
        if not products:
            await send_msg(client, chat_id, "📋 **【目前監控清單為空】**\n\n直接在對話框貼上好市多商品網址即可新增！")
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
            await send_msg(client, chat_id, reply, btn_rows)

    # 2. 🔍 立即檢查庫存
    elif text == "🔍 立即檢查庫存" or text in ["/check", "檢查"]:
        await send_msg(client, chat_id, "🔍 **正在即時連線好市多官方伺服器抓取庫存中...** (約需 1~2 秒)")
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        res_text = "🏁 **【立即檢查最新結果】**\n\n"
        for p in products:
            p_url = p.get("url")
            p_name = p.get("name", p_url)
            try:
                r = await client.get(p_url, headers=headers, timeout=10.0)
                soup = BeautifulSoup(r.text, "html.parser")
                costco_oos_btn = soup.find(lambda t: t.name == "button" and ("outOfStock" in t.get("class", []) or "缺貨" in t.get_text()))
                costco_pdp_add_btn = soup.find("button", id="add-to-cart-button")
                in_stock = bool(costco_pdp_add_btn and not costco_oos_btn)
                st = "🟢 有現貨！" if in_stock else "🔴 缺貨中"
                p["last_status"] = st
                res_text += f"• **{p_name}**：`{st}`\n"
            except Exception:
                res_text += f"• **{p_name}**：`抓取失敗`\n"

        save_data(settings, products)
        await send_msg(client, chat_id, res_text)

    # 3. ⏱️ 設定檢查與通知頻率
    elif text == "⏱️ 設定檢查與通知頻率" or text in ["/frequency", "頻率"]:
        p_min = settings.get("poll_interval_minutes", 15)
        oos_min = settings.get("oos_periodic_notify_minutes", 240)
        freq_text = (
            f"⏱️ **【檢查與通知頻率設定 (以分鐘為單位)】**\n\n"
            f"⚡ **目前雲端檢查頻率**：每 `{p_min} 分鐘` 一次\n"
            f"📢 **目前缺貨提醒頻率**：每 `{oos_min} 分鐘` 一次 (約 {oos_min//60} 小時)\n\n"
            f"👇 **點擊下方按鈕可直接切換頻率**："
        )
        kb = [
            [
                {"text": "⚡ 巡檢: 5分鐘", "callback_data": "set_poll_5"},
                {"text": "⚡ 巡檢: 15分鐘", "callback_data": "set_poll_15"},
                {"text": "⚡ 巡檢: 30分鐘", "callback_data": "set_poll_30"}
            ],
            [
                {"text": "📢 缺貨: 60分", "callback_data": "set_oos_60"},
                {"text": "📢 缺貨: 120分", "callback_data": "set_oos_120"},
                {"text": "📢 缺貨: 240分", "callback_data": "set_oos_240"}
            ]
        ]
        await send_msg(client, chat_id, freq_text, kb)

    # 4. ⚙️ 系統開關與狀態
    elif text == "⚙️ 系統開關與狀態" or text in ["/status", "狀態"]:
        now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
        is_act = "🟢 運作中 (ON)" if settings.get("is_active", True) else "🔴 已暫停 (OFF)"
        q_on = "🟢 已啟用" if settings.get("quiet_hours_enabled", True) else "⚪ 已停用"
        q_range = f"{settings.get('quiet_hours_start', 1):02d}:00 ~ {settings.get('quiet_hours_end', 5):02d}:00"
        p_min = settings.get("poll_interval_minutes", 15)
        oos_min = settings.get("oos_periodic_notify_minutes", 240)

        status_reply = (
            f"⚙️ **【系統開關與狀態儀表板】**\n\n"
            f"⚡ **總監控狀態**：{is_act}\n"
            f"⏰ **檢查頻率**：每 `{p_min} 分鐘` 一次\n"
            f"📢 **缺貨提醒頻率**：每 `{oos_min} 分鐘` 一次\n"
            f"🌙 **夜間靜音時段**：{q_on} (`{q_range}`)\n"
            f"📦 **商品總數**：共 `{len(products)}` 件\n"
            f"🕒 **伺服器時間**：`{now_str}`"
        )
        kb = [
            [
                {"text": "⚡ 切換總開關 (ON/OFF)", "callback_data": "toggle_active"},
                {"text": "🌙 切換夜間靜音", "callback_data": "toggle_quiet"}
            ]
        ]
        await send_msg(client, chat_id, status_reply, kb)

    # 5. 貼網址直接新增
    else:
        url_match = re.search(r"https?://[^\s]+costco\.com[^\s]+", text)
        if url_match:
            new_url = url_match.group(0)
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
                await send_msg(client, chat_id, f"✅ 已成功將商品 `{p_code}` 加入監控清單！")
            else:
                await send_msg(client, chat_id, f"ℹ️ 該商品網址已在監控清單中！")
        else:
            await send_msg(client, chat_id, "🎛️ **請點選下方常駐按鈕進行操作**：\n\n💡 想新增商品？直接在此貼上好市多商品網址即可！")

async def main():
    print("🤖 Telegram 常駐實體鍵盤監聽服務已啟動...")
    offset = 0
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                resp = await client.get(
                    f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates",
                    params={"offset": offset, "timeout": 1}
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
                            asyncio.create_task(handle_text_command(client, chat_id, text))
            except Exception:
                await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())

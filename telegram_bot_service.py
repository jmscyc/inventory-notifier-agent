#!/usr/bin/env python3
"""
Telegram 實時即時回應服務 (樹狀扁平精簡 + 分鐘級頻率設定 + 完美修復返回按鈕)
- 4 鍵極簡主選單
- 頻率以「分鐘」設定 (單鍵選擇 5/15/30/60 分鐘或自訂)
- 完美處理 editMessageText 異常，降級以 sendMessage 發送，保證「返回主選單」100% 必開！
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
            # 相容舊版小時轉分鐘
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

def get_main_menu():
    """極致精簡 4 大核心按鈕"""
    return {
        "inline_keyboard": [
            [
                {"text": "🛒 商品清單 / 管理", "callback_data": "menu_list"},
                {"text": "🔍 立即檢查庫存", "callback_data": "menu_check"}
            ],
            [
                {"text": "⏱️ 設定檢查與通知頻率", "callback_data": "menu_frequency"},
                {"text": "⚙️ 系統開關與狀態", "callback_data": "menu_status"}
            ]
        ]
    }

async def safe_edit_or_send(client: httpx.AsyncClient, chat_id: str, msg_id: int, text: str, reply_markup: dict = None):
    """保證 100% 成功送達：如果 editMessageText 因內容未改或訊息過舊失敗，自動改用 sendMessage 發送！"""
    payload = {
        "chat_id": chat_id,
        "message_id": msg_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup

    try:
        resp = await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/editMessageText", json=payload, timeout=5.0)
        if resp.status_code == 200:
            return
    except Exception:
        pass

    # 若失敗則 fallback 成發送新訊息
    send_payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    }
    if reply_markup:
        send_payload["reply_markup"] = reply_markup
    try:
        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json=send_payload, timeout=5.0)
    except Exception as e:
        print(f"發送訊息失敗: {e}")

async def handle_callback(client: httpx.AsyncClient, cb: dict):
    t_start = time.time()
    cb_id = cb.get("id")
    cb_data = cb.get("data", "")
    from_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
    msg_id = cb.get("message", {}).get("message_id")

    print(f"👉 收到按鈕點擊: {cb_data}")

    # 第一時間消除轉圈圈
    try:
        await client.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery",
            json={"callback_query_id": cb_id},
            timeout=3.0
        )
    except Exception:
        pass

    settings, products = load_data()

    # -------------------------------------------------------------
    # 1. 主選單 (包含返回主選單)
    # -------------------------------------------------------------
    if cb_data in ["menu_main", "menu_help"]:
        main_text = (
            "🎛️ **【好市多雲端智慧監控控制台】**\n\n"
            "請點選下方按鈕進行操作：\n\n"
            "💡 **想新增商品？** 直接在對話框「貼上好市多商品網址」即可！"
        )
        await safe_edit_or_send(client, from_chat_id, msg_id, main_text, get_main_menu())

    # -------------------------------------------------------------
    # 2. 商品清單 / 管理
    # -------------------------------------------------------------
    elif cb_data == "menu_list":
        if not products:
            reply = "📋 **【目前監控清單為空】**\n\n直接在對話框貼上好市多商品網址即可新增！"
            kb = {
                "inline_keyboard": [[{"text": "🔙 返回主選單", "callback_data": "menu_main"}]]
            }
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

        await safe_edit_or_send(client, from_chat_id, msg_id, reply, kb)

    elif cb_data.startswith("del_"):
        del_id = cb_data.replace("del_", "").strip()
        before_cnt = len(products)
        products = [p for p in products if str(p.get("id")) != del_id]
        if len(products) < before_cnt:
            save_data(settings, products)
            del_reply = f"🗑️ 已成功將商品 (編號 `{del_id}`) 從清單中刪除！"
        else:
            del_reply = "⚠️ 找不到該商品。"
        await safe_edit_or_send(client, from_chat_id, msg_id, del_reply, {
            "inline_keyboard": [
                [{"text": "📋 查看剩餘商品", "callback_data": "menu_list"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    # -------------------------------------------------------------
    # 3. 立即檢查庫存
    # -------------------------------------------------------------
    elif cb_data == "menu_check":
        await safe_edit_or_send(client, from_chat_id, msg_id, "🔍 **正在即時連線好市多官方伺服器抓取庫存中...** (約需 1~2 秒)")

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
        await safe_edit_or_send(client, from_chat_id, msg_id, res_text, {
            "inline_keyboard": [
                [{"text": "🔍 再查一次", "callback_data": "menu_check"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    # -------------------------------------------------------------
    # 4. ⏱️ 設定檢查與通知頻率 (以分鐘為單位！)
    # -------------------------------------------------------------
    elif cb_data == "menu_frequency":
        p_min = settings.get("poll_interval_minutes", 15)
        oos_min = settings.get("oos_periodic_notify_minutes", 240)

        freq_text = (
            f"⏱️ **【檢查與通知頻率設定 (以分鐘為單位)】**\n\n"
            f"⚡ **目前雲端檢查頻率**：每 `{p_min} 分鐘` 一次\n"
            f"📢 **目前缺貨提醒頻率**：每 `{oos_min} 分鐘` 一次 (約 {oos_min//60} 小時)\n\n"
            f"👇 **點擊下方按鈕可直接切換頻率**："
        )
        kb = {
            "inline_keyboard": [
                [
                    {"text": "⚡ 巡檢: 5分鐘", "callback_data": "set_poll_5"},
                    {"text": "⚡ 巡檢: 15分鐘", "callback_data": "set_poll_15"},
                    {"text": "⚡ 巡檢: 30分鐘", "callback_data": "set_poll_30"}
                ],
                [
                    {"text": "📢 缺貨: 60分", "callback_data": "set_oos_60"},
                    {"text": "📢 缺貨: 120分", "callback_data": "set_oos_120"},
                    {"text": "📢 缺貨: 240分", "callback_data": "set_oos_240"}
                ],
                [
                    {"text": "🔙 返回主選單", "callback_data": "menu_main"}
                ]
            ]
        }
        await safe_edit_or_send(client, from_chat_id, msg_id, freq_text, kb)

    elif cb_data.startswith("set_poll_"):
        new_m = int(cb_data.replace("set_poll_", ""))
        settings["poll_interval_minutes"] = new_m
        save_data(settings, products)
        await safe_edit_or_send(client, from_chat_id, msg_id, f"✅ **【巡檢頻率已更新】**：目前設定為每 `{new_m} 分鐘` 檢查一次！", {
            "inline_keyboard": [
                [{"text": "⏱️ 返回頻率設定", "callback_data": "menu_frequency"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    elif cb_data.startswith("set_oos_"):
        new_m = int(cb_data.replace("set_oos_", ""))
        settings["oos_periodic_notify_minutes"] = new_m
        settings["oos_periodic_notify_hours"] = round(new_m / 60, 1)
        save_data(settings, products)
        await safe_edit_or_send(client, from_chat_id, msg_id, f"✅ **【缺貨提醒頻率已更新】**：目前設定為每 `{new_m} 分鐘` ({new_m//60}小時) 重複提醒一次！", {
            "inline_keyboard": [
                [{"text": "⏱️ 返回頻率設定", "callback_data": "menu_frequency"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    # -------------------------------------------------------------
    # 5. 系統開關與狀態
    # -------------------------------------------------------------
    elif cb_data == "menu_status":
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
        kb = {
            "inline_keyboard": [
                [
                    {"text": "⚡ 切換總開關 (ON/OFF)", "callback_data": "toggle_active"},
                    {"text": "🌙 切換夜間靜音", "callback_data": "toggle_quiet"}
                ],
                [
                    {"text": "🔙 返回主選單", "callback_data": "menu_main"}
                ]
            ]
        }
        await safe_edit_or_send(client, from_chat_id, msg_id, status_reply, kb)

    elif cb_data == "toggle_active":
        settings["is_active"] = not settings.get("is_active", True)
        save_data(settings, products)
        act_state = "🟢 運作中 (ON)" if settings["is_active"] else "🔴 已暫停 (OFF)"
        await safe_edit_or_send(client, from_chat_id, msg_id, f"⚡ **總監控狀態已切換為**：{act_state}", {
            "inline_keyboard": [
                [{"text": "⚙️ 返回系統狀態", "callback_data": "menu_status"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    elif cb_data == "toggle_quiet":
        settings["quiet_hours_enabled"] = not settings.get("quiet_hours_enabled", True)
        save_data(settings, products)
        q_state = "🟢 已開啟夜間防打擾" if settings["quiet_hours_enabled"] else "☀️ 已關閉夜間防打擾 (24H通知)"
        await safe_edit_or_send(client, from_chat_id, msg_id, f"🌙 **夜間靜音設定已切換**：{q_state}", {
            "inline_keyboard": [
                [{"text": "⚙️ 返回系統狀態", "callback_data": "menu_status"}],
                [{"text": "🔙 返回主選單", "callback_data": "menu_main"}]
            ]
        })

    elapsed = (time.time() - t_start) * 1000
    print(f"⚡ 處理完成 {cb_data}，耗時: {elapsed:.1f} 毫秒")

async def main():
    print("🤖 Telegram 樹狀扁平精簡版監聽服務已啟動...")
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
            except Exception:
                await asyncio.sleep(0.5)

if __name__ == "__main__":
    asyncio.run(main())

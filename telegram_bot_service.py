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
from run_local_app import check_product_inventory

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

    # ⚡ Telegram 遠端代搶點擊回呼 (Remote Trigger)
    elif cb_data.startswith("buy_"):
        target_id = cb_data.replace("buy_", "").strip()
        matched = [p for p in products if str(p.get("id")) == target_id]
        if matched:
            target_prod = matched[0]
            p_url = target_prod.get("url")
            p_name = target_prod.get("name", "商品")
            p_option = target_prod.get("preferred_option", "")

            await send_msg(client, from_chat_id, f"⚡ **【收到遠端搶購指令】**\n正在調用本機自動加車引擎處理 `{p_name}`...")
            try:
                from app.autobuy_module import execute_autobuy_flow
                res = await execute_autobuy_flow(p_url, p_name, is_test_run=False, preferred_option=p_option)
                st_title = res.get("status_title", "搶購完成")
                st_msg = res.get("status_message", "")
                act_url = res.get("action_url", p_url)
                kb = [[{"text": res.get("action_button_text", "🛒 前往查看"), "url": act_url}]]
                await send_msg(client, from_chat_id, f"{st_title}\n\n{st_msg}", kb)
            except Exception as e:
                await send_msg(client, from_chat_id, f"❌ 遠端搶購執行例外: {e}")
        else:
            await send_msg(client, from_chat_id, "⚠️ 找不到該商品或已被移除。")

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
        await send_msg(client, chat_id, "🔍 **正在即時連線官方伺服器抓取庫存中...** (約需 1~2 秒)")
        from app.autobuy_module import get_random_ua
        headers = {"User-Agent": get_random_ua()}
        res_text = "🏁 **【立即檢查最新結果】**\n\n"
        any_in_stock_btns = []
        for p in products:
            p_url = p.get("url") or p.get("target_url")
            p_name = p.get("name", p_url)
            p_id = str(p.get("id"))
            try:
                crawl_res = await asyncio.to_thread(check_product_inventory, p_url)
                in_stock = crawl_res.get("in_stock", False)
                st = "🟢 有現貨！" if in_stock else "🔴 缺貨中"
                p["last_status"] = st
                p["in_stock"] = in_stock
                curr_price = crawl_res.get("price", "")
                if curr_price:
                    p["current_price"] = curr_price
                price_str = f" (`{curr_price}`)" if curr_price else ""
                res_text += f"• **{p_name}**：`{st}`{price_str}\n"
                if in_stock:
                    any_in_stock_btns.append([
                        {"text": f"⚡ 遠端代搶: {p_name[:12]}", "callback_data": f"buy_{p_id}"},
                        {"text": "🛒 手動頁面", "url": p_url}
                    ])
            except Exception as e:
                res_text += f"• **{p_name}**：`抓取失敗 ({e})`\n"

        save_data(settings, products)
        if any_in_stock_btns:
            res_text += "\n👇 **發現現貨！點擊下方按鈕可直接啟動本機代搶加車**："
        await send_msg(client, chat_id, res_text, any_in_stock_btns if any_in_stock_btns else None)

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

        # 檢驗 Cookie 憑證健康度
        try:
            from app.autobuy_module import check_cookie_health
            c_health = await check_cookie_health()
            c_icon = "🟢 有效" if c_health.get("is_valid") else "🔴 過期或未設定"
            c_desc = f"{c_icon} ({c_health.get('cookie_count', 0)} 筆)"
        except Exception:
            c_desc = "⚪ 未知"

        status_reply = (
            f"⚙️ **【系統開關與狀態儀表板】**\n\n"
            f"⚡ **總監控狀態**：{is_act}\n"
            f"🍪 **Cookie 憑證狀態**：`{c_desc}`\n"
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

    # 指令：設定目標預算價 /setprice [ID] [金額] (例如: /setprice 12345 1500)
    elif text.startswith("/setprice") or text.startswith("設定價格"):
        parts = text.split()
        if len(parts) >= 3:
            t_id = parts[1].strip()
            target_p = re.sub(r"[^\d.]", "", parts[2].strip())
            matched = False
            for p in products:
                if str(p.get("id")) == t_id or t_id in str(p.get("url")):
                    p["target_price"] = float(target_p) if target_p else None
                    matched = True
                    save_data(settings, products)
                    await send_msg(client, chat_id, f"🎯 **【已設定心理預算價格】**\n\n商品：{p.get('name')}\n目標預算上限：`${target_p}` 元\n（當有現貨且價格 ≤ 此金額時才會觸發推播或自動代搶）")
                    break
            if not matched:
                await send_msg(client, chat_id, f"⚠️ 找不到編號為 `{t_id}` 的商品，請確認清單中的編號。")
        else:
            await send_msg(client, chat_id, "ℹ️ 請使用格式：`/setprice [商品編號] [目標金額]`\n例如：`/setprice 123456 1200`")

    # 5. 貼網址直接新增商品 (支援指定規格與預算，如: https://... L號 1200)
    else:
        url_match = re.search(r"https?://[^\s]+", text)
        if url_match:
            new_url = url_match.group(0).strip()
            # 擷取網址後方的額外參數 (例如: L號 1500 或 黑色)
            remaining_text = text.replace(new_url, "").strip()
            
            # 嘗試解析是否有純數字作為預算金額
            budget_price = None
            price_match = re.search(r"\b(\d{2,6})\b", remaining_text)
            if price_match:
                budget_price = float(price_match.group(1))
                remaining_text = remaining_text.replace(price_match.group(0), "").strip()

            pref_opt = remaining_text if remaining_text else ""

            code_match = re.search(r"/p/(\d+)", new_url)
            p_code = code_match.group(1) if code_match else f"item-{abs(hash(new_url)) % 1000000}"

            # 即時爬取確認商品現況
            await send_msg(client, chat_id, f"🔍 正在解析並抓取商品資訊中，請稍候...\n`{new_url}`")
            crawl_res = await asyncio.to_thread(check_product_inventory, new_url)
            p_title = crawl_res.get("title") or f"電商商品 ({p_code})"
            in_stock = crawl_res.get("in_stock", False)
            status_desc = crawl_res.get("status_description", "待巡檢")
            st_icon = "🟢 現貨充足" if in_stock else "🔴 缺貨中"
            curr_price = crawl_res.get("price", "")

            matched_idx = -1
            for i, p in enumerate(products):
                if p.get("url") == new_url or p.get("target_url") == new_url or str(p.get("id")) == str(p_code):
                    matched_idx = i
                    break

            now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
            p_data = {
                "id": p_code,
                "name": p_title,
                "url": new_url,
                "target_url": new_url,
                "enabled": True,
                "in_stock": in_stock,
                "last_status": status_desc,
                "status_notes": status_desc,
                "last_checked": now_str,
                "preferred_option": pref_opt,
                "current_price": curr_price,
                "target_price": budget_price
            }

            budget_info = f"\n💰 **目標預算上限**：`${budget_price}` 元" if budget_price else ""
            price_info = f"\n💵 **當前抓取售價**：`{curr_price}`" if curr_price else ""

            if matched_idx >= 0:
                products[matched_idx] = p_data
                save_data(settings, products)
                msg = (
                    f"🔄 **【商品資訊與規格已更新】**\n\n"
                    f"📦 **商品**：{p_title}\n"
                    f"🏷️ **指定規格**：`{pref_opt or '無 (預設)'}`{price_info}{budget_info}\n"
                    f"📊 **目前狀態**：{st_icon} (`{status_desc}`)\n\n"
                    f"已為您同步更新監控設定！"
                )
            else:
                products.append(p_data)
                save_data(settings, products)
                msg = (
                    f"🎉 **【已成功加入自動監控清單！】**\n\n"
                    f"📦 **商品**：{p_title}\n"
                    f"🏷️ **指定規格**：`{pref_opt or '無 (預設)'}`{price_info}{budget_info}\n"
                    f"📊 **目前狀態**：{st_icon} (`{status_desc}`)\n\n"
                    f"⚡ 系統正每 `{settings.get('poll_interval_minutes', 5)} 分鐘` 自動巡檢，有現貨時將第一時間推播通知！"
                )

            kb = []
            if in_stock:
                kb.append([
                    {"text": "🛒 前往購買賣場", "url": new_url},
                    {"text": "⚡ 立即遠端代搶 (加車)", "callback_data": f"buy_{p_code}"}
                ])
            else:
                kb.append([{"text": "🛒 前往商品賣場", "url": new_url}])

            await send_msg(client, chat_id, msg, kb if kb else None)
        else:
            await send_msg(client, chat_id, "🎛️ **請點選下方常駐按鈕進行操作**：\n\n💡 想新增商品？直接在此貼上電商商品網址（如需指定尺寸可在網址後空格輸入，例如：`https://... L號`）！")

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

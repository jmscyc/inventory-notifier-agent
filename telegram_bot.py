#!/usr/bin/env python3
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import asyncio
import datetime
import os
import re
import sys
import time
import zoneinfo
import httpx
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not TOKEN:
    print("Error: TELEGRAM_BOT_TOKEN is not set in .env")
    sys.exit(1)

from google.adk.runners import Runner
from google.adk.sessions import InMemorySessionService
from google.genai import types
from app.agent import root_agent
from app.firestore_tools import list_tracked_products, remove_tracked_product, add_or_update_tracked_product
from app.inventory_tools import check_product_inventory

session_service = InMemorySessionService()
runner = Runner(agent=root_agent, session_service=session_service, app_name="inventory_bot")
user_sessions = {}

API_BASE = f"https://api.telegram.org/bot{TOKEN}"
try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    # 支援 Windows 預設未安裝 tzdata 之環境
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

# 1. 背景巡檢掃描頻率（分鐘，預設 5 分鐘，支援 1, 3, 5, 10 分鐘切換）
POLL_INTERVAL_MINUTES = 5

# 2. 持續缺貨期間之定期通報頻率（小時，預設 4 小時，支援 1, 2, 4, 8, 12, 24 小時切換，或 0 代表不定期通報）
OOS_NOTIFY_HOURS = 4

# 狀態記錄：追蹤各商品上次通知時間與庫存狀態
# 格式: { product_id: { "last_notified_stock": bool, "last_notified_time": float, "last_in_stock": bool } }
NOTIFY_STATE = {}

# 底部常駐鍵盤
MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "➕ 新增追蹤"}, {"text": "📋 目前追蹤清單"}],
        [{"text": "⚙️ 巡檢設定 (掃描/通知頻率)"}, {"text": "❌ 移除追蹤"}]
    ],
    "resize_keyboard": True,
    "persistent": True,
}


async def send_chat_action(client: httpx.AsyncClient, chat_id: int | str, action: str = "typing"):
    try:
        await client.post(f"{API_BASE}/sendChatAction", json={"chat_id": chat_id, "action": action}, timeout=3.0)
    except Exception:
        pass


async def send_tg_message(
    client: httpx.AsyncClient,
    chat_id: int | str,
    text: str,
    reply_markup: dict = None,
):
    url = f"{API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
    }
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    else:
        payload["reply_markup"] = MAIN_KEYBOARD

    try:
        resp = await client.post(url, json=payload, timeout=15.0)
        if not resp.is_success:
            payload.pop("parse_mode", None)
            await client.post(url, json=payload, timeout=15.0)
    except Exception as e:
        print(f"Failed to send telegram message to {chat_id}: {e}")


async def answer_callback_query(client: httpx.AsyncClient, callback_query_id: str, text: str = ""):
    url = f"{API_BASE}/answerCallbackQuery"
    try:
        await client.post(url, json={"callback_query_id": callback_query_id, "text": text}, timeout=10.0)
    except Exception:
        pass


def is_quiet_hours() -> bool:
    """判斷台北時間是否處於凌晨 01:00 ~ 05:00 免打擾時段"""
    now_taipei = datetime.datetime.now(TAIPEI_TZ)
    return 1 <= now_taipei.hour < 5


def format_tracked_products_fast(items: list) -> str:
    """極速排版清單 (Fast-path)"""
    if not items:
        return "📭 目前追蹤清單中沒有任何商品。\n\n您可以點擊 **【➕ 新增追蹤】** 貼上電商商品網址！"

    notify_desc = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知"
    lines = [f"📦 **【目前追蹤商品清單】**", f"• 掃描頻率：每 {POLL_INTERVAL_MINUTES} 分鐘 | 缺貨通報：{notify_desc}", ""]
    for i, item in enumerate(items, 1):
        name = item.get("name", "未命名商品")
        price = item.get("price", "")
        price_str = f" | 💰 {price}" if price else ""
        in_stock = item.get("in_stock", False)
        status_icon = "🟢 **現貨**" if in_stock else "🔴 **缺貨中**"
        notes = item.get("status_notes", "")
        last_checked = item.get("last_checked", "")
        target_url = item.get("target_url", "")

        lines.append(f"{i}. **{name}**{price_str}")
        lines.append(f"   • 庫存狀態：{status_icon}")
        if notes:
            lines.append(f"   • 詳情：{notes}")
        if last_checked:
            lines.append(f"   • 檢查時間：`{last_checked}`")
        if target_url:
            lines.append(f"   • [前往商品頁面]({target_url})")
        lines.append("")

    lines.append("💡 *提示：點擊個別商品通知下方的【🔄 立即重新檢查】可手動強制刷新！*")
    return "\n".join(lines)


async def handle_remove_menu(client: httpx.AsyncClient, chat_id: int):
    """處理點選『❌ 移除追蹤』按鈕"""
    items = list_tracked_products()
    if not items:
        await send_tg_message(
            client,
            chat_id,
            "📭 目前追蹤清單是空的，沒有可以移除的商品喔！",
            reply_markup=MAIN_KEYBOARD,
        )
        return

    inline_keyboard = []
    for item in items:
        item_id = item.get("id", "")
        name = item.get("name", "未命名商品")
        btn_text = f"🗑️ 移除: {name[:24]}"
        inline_keyboard.append([{"text": btn_text, "callback_data": f"del_{item_id}"}])

    inline_markup = {"inline_keyboard": inline_keyboard}
    await send_tg_message(
        client,
        chat_id,
        "👇 **請直接點擊下方要取消追蹤的商品**：",
        reply_markup=inline_markup,
    )


async def handle_settings_menu(client: httpx.AsyncClient, chat_id: int):
    """呈現主設定選單：讓使用者選擇『調整掃描頻率』或『調整缺貨通知頻率』"""
    notify_str = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知 (不定期提醒)"
    msg = (
        f"⚙️ **【自動巡檢與通知偏好設定】**\n\n"
        f"📊 **目前配置**：\n"
        f"• **背景掃描頻率**：每 **{POLL_INTERVAL_MINUTES}** 分鐘爬蟲掃描一次\n"
        f"• **缺貨通知頻率**：{notify_str}\n"
        f"• **夜間免打擾**：台北時間 01:00 ~ 05:00 暫停主動推播\n"
        f"• **到貨通報**：一旦補貨立即秒速通知 1 次（附帶重新檢查按鈕）\n\n"
        f"請點選欲調整的項目："
    )
    inline_keyboard = [
        [{"text": f"🔄 調整【背景掃描週期】(現: {POLL_INTERVAL_MINUTES}分)", "callback_data": "menu_scan"}],
        [{"text": f"🔔 調整【缺貨通知頻率】(現: {OOS_NOTIFY_HOURS}小時)", "callback_data": "menu_notify"}],
    ]
    await send_tg_message(client, chat_id, msg, reply_markup={"inline_keyboard": inline_keyboard})


async def handle_scan_interval_menu(client: httpx.AsyncClient, chat_id: int):
    """調整背景掃描週期選單"""
    inline_keyboard = [
        [
            {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 1 else ''}每 1 分鐘", "callback_data": "int_1"},
            {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 3 else ''}每 3 分鐘", "callback_data": "int_3"},
        ],
        [
            {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 5 else ''}每 5 分鐘", "callback_data": "int_5"},
            {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 10 else ''}每 10 分鐘", "callback_data": "int_10"},
        ],
        [{"text": "⬅️ 返回設定總覽", "callback_data": "menu_back"}]
    ]
    msg = (
        f"🔄 **【選擇背景掃描週期】**\n\n"
        f"系統將以此間隔在後台定期檢查商品頁面是否有庫存：\n"
        f"(目前：每 {POLL_INTERVAL_MINUTES} 分鐘)"
    )
    await send_tg_message(client, chat_id, msg, reply_markup={"inline_keyboard": inline_keyboard})


async def handle_notify_frequency_menu(client: httpx.AsyncClient, chat_id: int):
    """調整缺貨通知頻率選單"""
    inline_keyboard = [
        [
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 1 else ''}每 1 小時", "callback_data": "notif_1"},
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 2 else ''}每 2 小時", "callback_data": "notif_2"},
        ],
        [
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 4 else ''}每 4 小時", "callback_data": "notif_4"},
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 8 else ''}每 8 小時", "callback_data": "notif_8"},
        ],
        [
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 12 else ''}每 12 小時", "callback_data": "notif_12"},
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 24 else ''}每 24 小時", "callback_data": "notif_24"},
        ],
        [
            {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 0 else ''}🔕 缺貨不定期提醒 (僅初次與有貨時通知)", "callback_data": "notif_0"}
        ],
        [{"text": "⬅️ 返回設定總覽", "callback_data": "menu_back"}]
    ]
    cur_str = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知"
    msg = (
        f"🔔 **【選擇缺貨狀態通知頻率】**\n\n"
        f"當商品持續缺貨時，您希望多久收到一次例行狀態更新？\n"
        f"(目前：{cur_str})\n\n"
        f"💡 *註：一旦商品補貨，系統無論何時皆會第一時間秒速通報！*"
    )
    await send_tg_message(client, chat_id, msg, reply_markup={"inline_keyboard": inline_keyboard})


async def perform_check_and_notify_single(client: httpx.AsyncClient, chat_id: int | str, item: dict, is_manual: bool = False):
    """檢查單一商品並依精確規則發送通知與附帶『🔄 立即重新檢查』按鈕"""
    item_id = item.get("id", "")
    target_url = item.get("target_url", "")
    name = item.get("name", "商品")

    if not target_url:
        return

    # 執行爬蟲驗證
    res = check_product_inventory(target_url)
    in_stock = res.get("in_stock", False)
    status_desc = res.get("status_description", "")
    now_ts = time.time()
    now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # 更新 Firestore
    add_or_update_tracked_product(
        product_id=item_id,
        name=name,
        target_url=target_url,
        in_stock=in_stock,
        status_notes=status_desc,
    )

    state = NOTIFY_STATE.get(item_id, {
        "last_notified_stock": None,
        "last_notified_time": 0,
        "last_in_stock": None,
    })

    last_notified_stock = state.get("last_notified_stock")
    last_notified_time = state.get("last_notified_time", 0)

    should_send = False
    notify_title = ""
    notify_body = ""

    # 手動點擊「立即重新檢查」時一律回報
    if is_manual:
        should_send = True
        if in_stock:
            notify_title = "🎉 **【手動更新：商品有現貨！】**"
            notify_body = f"🟢 目前狀態：{status_desc}\n快前往賣場把握搶購！"
        else:
            notify_title = "ℹ️ **【手動更新：目前依然缺貨中】**"
            notify_body = f"🔴 目前狀態：{status_desc}\n系統已恢復常規每 {POLL_INTERVAL_MINUTES} 分鐘自動監控，有貨第一時間通知您！"
    else:
        # 背景定時自動巡檢邏輯
        # 1. 凌晨 01:00 ~ 05:00 免打擾 (除非使用者手動點)
        if is_quiet_hours():
            NOTIFY_STATE[item_id] = {
                "last_notified_stock": last_notified_stock,
                "last_notified_time": last_notified_time,
                "last_in_stock": in_stock,
            }
            return

        # 2. 狀態由缺貨 -> 有現貨 (初次有貨 / 補貨到貨)：立即通知 1 次！
        if in_stock and (last_notified_stock is not True):
            should_send = True
            notify_title = "🎉 **【好消息！商品補貨上架】**"
            notify_body = (
                f"🟢 **庫存狀態**：{status_desc}\n"
                f"⏰ **偵測時間**：`{now_str}`\n\n"
                f"立即前往下單購買！"
            )
        # 3. 狀態由有貨 -> 變缺貨：初次缺貨通知 1 次
        elif (not in_stock) and (last_notified_stock is None or last_notified_stock is True):
            should_send = True
            notify_title = "📢 **【商品目前缺貨提醒】**"
            notify_body = (
                f"🔴 **庫存狀態**：{status_desc}\n"
                f"⏰ **偵測時間**：`{now_str}`\n\n"
                f"系統將維持每 {POLL_INTERVAL_MINUTES} 分鐘為您自動掃描，一有庫存立即通報！"
            )
        # 4. 持續缺貨期間：依照使用者設定的 OOS_NOTIFY_HOURS 提醒
        elif (not in_stock) and (last_notified_stock is False):
            if OOS_NOTIFY_HOURS > 0:
                interval_seconds = OOS_NOTIFY_HOURS * 3600
                if (now_ts - last_notified_time) >= interval_seconds:
                    should_send = True
                    notify_title = f"⏳ **【商品持續缺貨追蹤中 ({OOS_NOTIFY_HOURS}小時例行回報)】**"
                    notify_body = (
                        f"🔴 目前仍然缺貨中，系統正以每 {POLL_INTERVAL_MINUTES} 分鐘持續巡檢中，請放心！\n"
                        f"⏰ 檢查時間：`{now_str}`"
                    )

    if should_send:
        NOTIFY_STATE[item_id] = {
            "last_notified_stock": in_stock,
            "last_notified_time": now_ts,
            "last_in_stock": in_stock,
        }

        inline_keyboard = [
            [
                {"text": "🔄 立即重新檢查", "callback_data": f"refresh_{item_id}"},
                {"text": "🛒 前往商品頁面", "url": target_url},
            ]
        ]
        msg_text = (
            f"{notify_title}\n\n"
            f"📦 **商品**：{name}\n"
            f"{notify_body}"
        )
        await send_tg_message(client, chat_id, msg_text, reply_markup={"inline_keyboard": inline_keyboard})


async def handle_callback_query(client: httpx.AsyncClient, callback_query: dict):
    """處理所有 Inline 按鈕點擊 (刪除、設定掃描週期、設定通知頻率、手動更新)"""
    global POLL_INTERVAL_MINUTES, OOS_NOTIFY_HOURS
    query_id = callback_query.get("id")
    chat_id = callback_query.get("message", {}).get("chat", {}).get("id")
    data = callback_query.get("data", "")

    # 1. 刪除追蹤商品
    if data.startswith("del_"):
        product_id = data[4:]
        res = remove_tracked_product(product_id)
        msg = res.get("message", "已處理移除。")
        NOTIFY_STATE.pop(product_id, None)
        await answer_callback_query(client, query_id, text="已移除！")
        await send_tg_message(
            client,
            chat_id,
            f"✅ {msg}\n\n您可以點擊下方按鈕查詢最新清單或新增商品！",
            reply_markup=MAIN_KEYBOARD,
        )

    # 2. 開啟選單子頁
    elif data == "menu_scan":
        await answer_callback_query(client, query_id)
        await handle_scan_interval_menu(client, chat_id)

    elif data == "menu_notify":
        await answer_callback_query(client, query_id)
        await handle_notify_frequency_menu(client, chat_id)

    elif data == "menu_back":
        await answer_callback_query(client, query_id)
        await handle_settings_menu(client, chat_id)

    # 3. 設定掃描週期 (int_1, int_3, int_5, int_10)
    elif data.startswith("int_"):
        new_int = int(data[4:])
        POLL_INTERVAL_MINUTES = new_int
        await answer_callback_query(client, query_id, text=f"掃描週期已更新為每 {new_int} 分鐘！")
        await send_tg_message(
            client,
            chat_id,
            f"✅ **設定成功**：背景掃描週期已調整為 **每 {new_int} 分鐘** 一次！",
            reply_markup=MAIN_KEYBOARD,
        )

    # 4. 設定缺貨通知頻率 (notif_0, notif_1, notif_2, notif_4, notif_8, notif_12, notif_24)
    elif data.startswith("notif_"):
        new_hours = int(data[6:])
        OOS_NOTIFY_HOURS = new_hours
        desc = f"每 {new_hours} 小時提醒一次" if new_hours > 0 else "缺貨不定期提醒 (僅在初次缺貨與補貨到貨時發送通知)"
        await answer_callback_query(client, query_id, text="通知頻率已更新！")
        await send_tg_message(
            client,
            chat_id,
            f"✅ **設定成功**：持續缺貨通報頻率已調整為 **{desc}**！",
            reply_markup=MAIN_KEYBOARD,
        )

    # 5. 點擊「🔄 立即重新檢查」
    elif data.startswith("refresh_"):
        product_id = data[8:]
        await answer_callback_query(client, query_id, text="正在為您重新檢查庫存...")
        items = list_tracked_products()
        matched = [i for i in items if i.get("id") == product_id]
        if matched:
            await perform_check_and_notify_single(client, chat_id, matched[0], is_manual=True)
        else:
            await send_tg_message(client, chat_id, "⚠️ 該商品已不在追蹤清單中。", reply_markup=MAIN_KEYBOARD)

    else:
        await answer_callback_query(client, query_id)


async def handle_message(client: httpx.AsyncClient, chat_id: int, user_text: str):
    user_text_clean = user_text.strip()

    if not os.getenv("TELEGRAM_CHAT_ID"):
        os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)

    # 快捷指令分流
    if user_text_clean in ["➕ 新增追蹤", "新增", "新增追蹤"]:
        help_msg = (
            "✨ **【新增商品追蹤】**\n\n"
            "請直接在此**貼上您想監控的電商商品網址**（例如 Costco 好市多、PChome 等）🔗\n\n"
            "收到網址後，我會立即：\n"
            "1. 檢查商品頁面的真實庫存與購買按鈕\n"
            "2. 自動登記至專屬追蹤資料庫\n"
            f"3. 啟動背景巡檢（每 {POLL_INTERVAL_MINUTES} 分鐘），一旦補貨立即主動通知您！"
        )
        await send_tg_message(client, chat_id, help_msg, reply_markup=MAIN_KEYBOARD)
        return

    if user_text_clean in ["❌ 移除追蹤", "移除", "移除追蹤"]:
        await handle_remove_menu(client, chat_id)
        return

    if user_text_clean in ["⚙️ 巡檢設定 (掃描/通知頻率)", "⚙️ 巡檢週期設定", "週期設定", "設定週期", "通知頻率", "設定"]:
        await handle_settings_menu(client, chat_id)
        return

    if user_text_clean in ["📋 目前追蹤清單", "目前追蹤清單", "追蹤清單", "查詢清單", "清單"]:
        items = list_tracked_products()
        if not items:
            await send_tg_message(client, chat_id, "📭 目前追蹤清單中沒有任何商品。\n\n您可以點擊 **【➕ 新增追蹤】** 貼上電商商品網址！", reply_markup=MAIN_KEYBOARD)
            return

        # 立即提示使用者正在全面重新爬蟲最新狀態
        await send_chat_action(client, chat_id, "typing")
        status_msg_id = None
        try:
            status_resp = await client.post(
                f"{API_BASE}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": f"🔄 **正在全面重新爬蟲 {len(items)} 項追蹤商品的當前最新庫存，請稍候...**",
                    "parse_mode": "Markdown",
                },
                timeout=5.0,
            )
            if status_resp.status_code == 200:
                status_msg_id = status_resp.json().get("result", {}).get("message_id")
        except Exception:
            pass

        # 非同步並行爬蟲所有商品最新狀態
        async def check_item(item):
            t_url = item.get("target_url")
            if not t_url:
                return item
            try:
                # 在執行緒中運行同步爬蟲避免卡住事件迴圈
                crawl_res = await asyncio.to_thread(check_product_inventory, t_url)
                item["in_stock"] = crawl_res.get("in_stock", False)
                item["status_notes"] = crawl_res.get("status_description", "")
                item["last_checked"] = crawl_res.get("checked_at", "")
                if crawl_res.get("title") and crawl_res.get("title") != t_url:
                    item["name"] = crawl_res.get("title")
                # 同步更新回 Firestore
                await asyncio.to_thread(
                    add_or_update_tracked_product,
                    product_id=item.get("id"),
                    name=item.get("name"),
                    target_url=t_url,
                    in_stock=item["in_stock"],
                    status_notes=item["status_notes"],
                )
            except Exception as e:
                item["status_notes"] = f"檢查失敗: {e}"
            return item

        fresh_items = await asyncio.gather(*(check_item(item) for item in items))
        fresh_reply = format_tracked_products_fast(list(fresh_items))

        # 編輯更新提示訊息
        updated_ok = False
        if status_msg_id:
            try:
                edit_resp = await client.post(
                    f"{API_BASE}/editMessageText",
                    json={
                        "chat_id": chat_id,
                        "message_id": status_msg_id,
                        "text": fresh_reply,
                        "parse_mode": "Markdown",
                        "disable_web_page_preview": True,
                    },
                    timeout=15.0,
                )
                if edit_resp.status_code == 200:
                    updated_ok = True
            except Exception:
                pass

        if not updated_ok:
            await send_tg_message(client, chat_id, fresh_reply, reply_markup=MAIN_KEYBOARD)
        return

    if user_text_clean in ["/start", "start"]:
        notify_desc = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知"
        welcome_msg = (
            "您好！我是您的 **商品庫存查詢與 Telegram 提醒助理** 📦🔔\n\n"
            f"🤖 **後台全天候自動巡檢運作中**\n"
            f"• 掃描頻率：每 {POLL_INTERVAL_MINUTES} 分鐘\n"
            f"• 缺貨回報：{notify_desc}\n"
            "• 夜間免打擾：台北時間 01:00 ~ 05:00\n"
            "• 補貨到貨：秒速主動推播\n\n"
            "您可以點擊下方按鈕開始操作或自訂偏好："
        )
        await send_tg_message(client, chat_id, welcome_msg, reply_markup=MAIN_KEYBOARD)
        return

    # 其餘交由 Agent + 立即處理中反饋
    await send_chat_action(client, chat_id, "typing")

    processing_msg_id = None
    try:
        proc_resp = await client.post(
            f"{API_BASE}/sendMessage",
            json={
                "chat_id": chat_id,
                "text": "⏳ **訊息已接收，系統正在處理中，請稍候...**",
                "parse_mode": "Markdown",
            },
            timeout=5.0,
        )
        if proc_resp.status_code == 200:
            processing_msg_id = proc_resp.json().get("result", {}).get("message_id")
    except Exception as e:
        print(f"Failed to send processing placeholder: {e}")

    if chat_id not in user_sessions:
        session = session_service.create_session_sync(user_id=str(chat_id), app_name="inventory_bot")
        user_sessions[chat_id] = session.id

    session_id = user_sessions[chat_id]
    print(f"\n[Telegram Incoming from {chat_id}]: {user_text_clean}")

    msg = types.Content(role="user", parts=[types.Part.from_text(text=user_text_clean)])
    reply_parts = []
    try:
        async def keep_typing():
            while True:
                await asyncio.sleep(4.0)
                await send_chat_action(client, chat_id, "typing")

        typing_task = asyncio.create_task(keep_typing())
        try:
            for event in runner.run(
                new_message=msg,
                user_id=str(chat_id),
                session_id=session_id,
            ):
                if event.content and event.content.parts:
                    for p in event.content.parts:
                        if getattr(p, "text", None):
                            reply_parts.append(p.text)
        finally:
            typing_task.cancel()

    except Exception as e:
        reply_parts.append(f"處理訊息時發生錯誤：{str(e)}")

    reply_text = "".join(reply_parts).strip()
    if not reply_text:
        reply_text = "已為您處理完畢！"

    print(f"[Agent Reply to {chat_id}]: {reply_text[:100]}...")

    edited_success = False
    if processing_msg_id:
        try:
            edit_resp = await client.post(
                f"{API_BASE}/editMessageText",
                json={
                    "chat_id": chat_id,
                    "message_id": processing_msg_id,
                    "text": reply_text,
                    "parse_mode": "Markdown",
                    "disable_web_page_preview": True,
                },
                timeout=15.0,
            )
            if edit_resp.status_code == 200:
                edited_success = True
        except Exception:
            pass

    if not edited_success:
        await send_tg_message(client, chat_id, reply_text, reply_markup=MAIN_KEYBOARD)


async def background_scheduler_loop(client: httpx.AsyncClient):
    """背景全天候巡檢任務迴圈"""
    print("⏰ Background Inventory Scheduler started.")
    await asyncio.sleep(10)

    while True:
        try:
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if chat_id:
                items = list_tracked_products()
                for item in items:
                    if item.get("target_url"):
                        try:
                            await perform_check_and_notify_single(client, chat_id, item, is_manual=False)
                        except Exception as item_err:
                            print(f"Error checking item {item.get('id')}: {item_err}")
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"Scheduler loop error: {e}")

        await asyncio.sleep(POLL_INTERVAL_MINUTES * 60)


async def main():
    print(f"🤖 Starting Full Autonomous Telegram Bot with Dual Frequency Settings (Token: {TOKEN[:10]}...)")
    offset = 0

    async with httpx.AsyncClient(timeout=30.0) as client:
        me = await client.get(f"{API_BASE}/getMe")
        print(f"Connected as: {me.json().get('result', {}).get('username')}")

        asyncio.create_task(background_scheduler_loop(client))

        while True:
            try:
                resp = await client.get(f"{API_BASE}/getUpdates", params={"offset": offset, "timeout": 20})
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1

                        if "callback_query" in update:
                            asyncio.create_task(handle_callback_query(client, update["callback_query"]))
                            continue

                        message = update.get("message")
                        if not message:
                            continue

                        text = message.get("text") or message.get("caption") or ""
                        entities = message.get("entities", []) + message.get("caption_entities", [])
                        extracted_urls = []
                        for ent in entities:
                            if ent.get("type") == "text_link" and ent.get("url"):
                                extracted_urls.append(ent["url"])
                            elif ent.get("type") == "url":
                                offset_pos = ent.get("offset", 0)
                                length = ent.get("length", 0)
                                raw_url = text[offset_pos : offset_pos + length]
                                if raw_url:
                                    extracted_urls.append(raw_url)

                        if extracted_urls:
                            unique_urls = list(dict.fromkeys(extracted_urls))
                            for u in unique_urls:
                                if u not in text:
                                    text = f"{text}\n{u}".strip()

                        if not text:
                            continue

                        chat_id = message["chat"]["id"]
                        asyncio.create_task(handle_message(client, chat_id, text))
            except httpx.RequestError as e:
                print(f"Connection error: {e}")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"Polling loop error: {e}")
                await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBot stopped.")

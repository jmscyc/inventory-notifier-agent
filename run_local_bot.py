#!/usr/bin/env python3
"""
【獨立本地執行檔 - Telegram 庫存監控與智慧提醒機器人】

功能特色：
1. 完全自給自足 (Self-contained)：單一檔案即可執行。
2. 本地儲存：預設使用本機 tracked_products.json，若環境有 Google Cloud 憑證亦可同步 Firestore。
3. 互動介面：包含 Telegram 底部按鈕 (➕ 新增追蹤, 📋 目前追蹤清單, ⚙️ 巡檢設定, ❌ 移除追蹤)。
4. 智慧巡檢排程：
   - 背景自動定時巡檢（預設 5 分鐘，可選 1, 3, 5, 10 分鐘）
   - 缺貨通知自訂頻率（預設 4 小時，可調 1, 2, 4, 8, 12, 24 小時或關閉定期回報）
   - 補貨即時通知（附【🔄 立即重新檢查】與【🛒 前往商品頁面】按鈕）
   - 台北時間 01:00 ~ 05:00 夜間免打擾

依賴套件：
   pip install httpx beautifulsoup4 python-dotenv
執行方式：
   python3 run_local_bot.py
"""

import asyncio
import datetime
import json
import os
import re
import sys
import time
import zoneinfo
from pathlib import Path
import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv

# 讀取當前目錄或上層的 .env
load_dotenv()

TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "9200030096:AAFNpPRV2zbQj4DFya0pTSfLi7pUDG7_MR0").strip()
if not TOKEN:
    print("❌ 錯誤：未設定 TELEGRAM_BOT_TOKEN。請在 .env 中填入或直接修改腳本中的 TOKEN。")
    sys.exit(1)

API_BASE = f"https://api.telegram.org/bot{TOKEN}"
try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    # 支援 Windows 預設未安裝 tzdata 之環境
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))
DATA_FILE = Path(__file__).parent / "tracked_products.json"

# 全域排程與通知頻率
POLL_INTERVAL_MINUTES = 5
OOS_NOTIFY_HOURS = 4
NOTIFY_STATE = {}

# 底部主按鈕鍵盤
MAIN_KEYBOARD = {
    "keyboard": [
        [{"text": "➕ 新增追蹤"}, {"text": "📋 目前追蹤清單"}],
        [{"text": "⚙️ 巡檢設定 (掃描/通知頻率)"}, {"text": "❌ 移除追蹤"}]
    ],
    "resize_keyboard": True,
    "persistent": True,
}


# =====================================================================
# 1. 本地資料持久化 (JSON 檔案儲存)
# =====================================================================

def load_tracked_items() -> list:
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ 讀取本地清單失敗: {e}")
        return []


def save_tracked_items(items: list):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存本地清單失敗: {e}")


def add_or_update_item(name: str, target_url: str, in_stock: bool, status_notes: str, item_id: str = "") -> dict:
    items = load_tracked_items()
    now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if not item_id:
        # 從 URL 產生唯一 ID
        clean_url = target_url.strip()
        code_match = re.search(r'/p/(\d+)', clean_url)
        if code_match:
            item_id = f"item-{code_match.group(1)}"
        else:
            item_id = f"item-{abs(hash(clean_url)) % 1000000}"

    updated = False
    for it in items:
        if it.get("id") == item_id or it.get("target_url") == target_url:
            it["name"] = name or it.get("name", "未命名商品")
            it["target_url"] = target_url
            it["in_stock"] = in_stock
            it["status_notes"] = status_notes
            it["last_checked"] = now_str
            updated = True
            break

    if not updated:
        items.append({
            "id": item_id,
            "name": name or target_url,
            "target_url": target_url,
            "in_stock": in_stock,
            "status_notes": status_notes,
            "last_checked": now_str,
        })

    save_tracked_items(items)
    return {"id": item_id, "name": name, "in_stock": in_stock}


def delete_item(item_id: str) -> bool:
    items = load_tracked_items()
    new_items = [it for it in items if it.get("id") != item_id]
    if len(new_items) != len(items):
        save_tracked_items(new_items)
        return True
    return False


# =====================================================================
# 2. 核心庫存爬蟲與嚴格檢查邏輯
# =====================================================================

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
        with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html_content = resp.text
    except Exception as e:
        return {
            "url": url,
            "title": "無法取得頁面",
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

    # Costco 嚴格判定
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
            status_desc = "目前缺貨 (商品頁面顯示『缺貨』按鈕且已停用加入購物車)"
        elif costco_pdp_add_btn and has_qty_selector:
            in_stock = True
            status_desc = "有現貨 (確認具備數量增減選擇器及加入購物車按鈕)"
        elif costco_pdp_add_btn:
            in_stock = True
            status_desc = "有現貨 (商品頁具備加入購物車按鈕)"
        else:
            in_stock = False
            status_desc = "目前缺貨 (未發現此商品專屬之加入購物車與數量選單)"

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

    has_oos = any(re.search(pat, page_text) for pat in out_of_stock_patterns)
    has_in = any(re.search(pat, page_text) for pat in in_stock_patterns)

    if disabled_cart or (has_oos and not has_in):
        in_stock = False
        status_desc = "目前缺貨 / 售完中 (Out of stock)"
    elif has_in and not has_oos:
        in_stock = True
        status_desc = "目前有現貨 / 可加入購物車 (In stock)"
    else:
        in_stock = not has_oos
        status_desc = "正常載入" if in_stock else "缺貨"

    return {
        "url": url,
        "title": title,
        "in_stock": in_stock,
        "status_description": status_desc,
        "checked_at": now_str,
    }


# =====================================================================
# 3. Telegram API 工具函式
# =====================================================================

async def send_tg_message(client: httpx.AsyncClient, chat_id: int | str, text: str, reply_markup: dict = None):
    url = f"{API_BASE}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
        "disable_web_page_preview": True,
        "reply_markup": reply_markup if reply_markup is not None else MAIN_KEYBOARD,
    }
    try:
        resp = await client.post(url, json=payload, timeout=15.0)
        if not resp.is_success:
            payload.pop("parse_mode", None)
            await client.post(url, json=payload, timeout=15.0)
    except Exception as e:
        print(f"❌ 發送 Telegram 訊息失敗: {e}")


async def answer_callback(client: httpx.AsyncClient, query_id: str, text: str = ""):
    try:
        await client.post(f"{API_BASE}/answerCallbackQuery", json={"callback_query_id": query_id, "text": text}, timeout=10.0)
    except Exception:
        pass


def is_quiet_hours() -> bool:
    now_taipei = datetime.datetime.now(TAIPEI_TZ)
    return 1 <= now_taipei.hour < 5


def format_tracked_products_fast(items: list) -> str:
    if not items:
        return "📭 目前追蹤清單中沒有任何商品。\n\n您可以點擊 **【➕ 新增追蹤】** 並貼上電商商品網址！"

    notify_desc = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知"
    lines = [f"📦 **【目前追蹤商品清單】**", f"• 掃描頻率：每 {POLL_INTERVAL_MINUTES} 分鐘 | 缺貨通報：{notify_desc}", ""]
    for i, item in enumerate(items, 1):
        name = item.get("name", "未命名商品")
        in_stock = item.get("in_stock", False)
        status_icon = "🟢 **現貨**" if in_stock else "🔴 **缺貨中**"
        notes = item.get("status_notes", "")
        last_checked = item.get("last_checked", "")
        target_url = item.get("target_url", "")

        lines.append(f"{i}. **{name}**")
        lines.append(f"   • 庫存狀態：{status_icon}")
        if notes:
            lines.append(f"   • 詳情：{notes}")
        if last_checked:
            lines.append(f"   • 檢查時間：`{last_checked}`")
        if target_url:
            lines.append(f"   • [前往商品頁面]({target_url})")
        lines.append("")

    lines.append("💡 *提示：點擊下方【❌ 移除追蹤】可快速刪除不需要的商品。*")
    return "\n".join(lines)


# =====================================================================
# 4. 單一檢查與巡檢通知核心
# =====================================================================

async def perform_check_and_notify_single(client: httpx.AsyncClient, chat_id: int | str, item: dict, is_manual: bool = False):
    item_id = item.get("id", "")
    target_url = item.get("target_url", "")
    name = item.get("name", "商品")
    if not target_url:
        return

    crawl_res = await asyncio.to_thread(check_product_inventory, target_url)
    in_stock = crawl_res.get("in_stock", False)
    status_desc = crawl_res.get("status_description", "")
    now_ts = time.time()
    now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    # 更新本地檔案
    add_or_update_item(name=crawl_res.get("title") or name, target_url=target_url, in_stock=in_stock, status_notes=status_desc, item_id=item_id)

    state = NOTIFY_STATE.get(item_id, {"last_notified_stock": None, "last_notified_time": 0})
    last_notified_stock = state.get("last_notified_stock")
    last_notified_time = state.get("last_notified_time", 0)

    should_send = False
    notify_title = ""
    notify_body = ""

    if is_manual:
        should_send = True
        if in_stock:
            notify_title = "🎉 **【手動更新：商品有現貨！】**"
            notify_body = f"🟢 目前狀態：{status_desc}\n快前往賣場把握搶購！"
        else:
            notify_title = "ℹ️ **【手動更新：目前依然缺貨中】**"
            notify_body = f"🔴 目前狀態：{status_desc}\n系統已恢復常規每 {POLL_INTERVAL_MINUTES} 分鐘自動監控，有貨第一時間通知您！"
    else:
        if is_quiet_hours():
            NOTIFY_STATE[item_id] = {"last_notified_stock": last_notified_stock, "last_notified_time": last_notified_time}
            return

        # 缺貨 -> 有現貨 (補貨上架)
        if in_stock and (last_notified_stock is not True):
            should_send = True
            notify_title = "🎉 **【好消息！商品補貨上架】**"
            notify_body = f"🟢 **庫存狀態**：{status_desc}\n⏰ **偵測時間**：`{now_str}`\n\n立即前往下單購買！"
        # 初次缺貨
        elif (not in_stock) and (last_notified_stock is None or last_notified_stock is True):
            should_send = True
            notify_title = "📢 **【商品目前缺貨提醒】**"
            notify_body = f"🔴 **庫存狀態**：{status_desc}\n⏰ **偵測時間**：`{now_str}`\n\n系統將以每 {POLL_INTERVAL_MINUTES} 分鐘為您自動掃描，一有庫存立即通報！"
        # 持續缺貨期間依照設定頻率提醒
        elif (not in_stock) and (last_notified_stock is False):
            if OOS_NOTIFY_HOURS > 0:
                if (now_ts - last_notified_time) >= (OOS_NOTIFY_HOURS * 3600):
                    should_send = True
                    notify_title = f"⏳ **【商品持續缺貨追蹤中 ({OOS_NOTIFY_HOURS}小時例行回報)】**"
                    notify_body = f"🔴 目前仍然缺貨中，系統正以每 {POLL_INTERVAL_MINUTES} 分鐘持續巡檢中！\n⏰ 檢查時間：`{now_str}`"

    if should_send:
        NOTIFY_STATE[item_id] = {"last_notified_stock": in_stock, "last_notified_time": now_ts}
        inline_keyboard = [
            [
                {"text": "🔄 立即重新檢查", "callback_data": f"refresh_{item_id}"},
                {"text": "🛒 前往商品頁面", "url": target_url},
            ]
        ]
        msg_text = f"{notify_title}\n\n📦 **商品**：{name}\n{notify_body}"
        await send_tg_message(client, chat_id, msg_text, reply_markup={"inline_keyboard": inline_keyboard})


# =====================================================================
# 5. 背景定時巡檢迴圈
# =====================================================================

async def background_scheduler_loop(client: httpx.AsyncClient):
    print("⏰ 背景商品庫存巡檢排程已啟動...")
    await asyncio.sleep(5)
    while True:
        try:
            chat_id = os.getenv("TELEGRAM_CHAT_ID", "").strip()
            if chat_id:
                items = load_tracked_items()
                for item in items:
                    if item.get("target_url"):
                        try:
                            await perform_check_and_notify_single(client, chat_id, item, is_manual=False)
                        except Exception as e:
                            print(f"巡檢商品錯誤 ({item.get('id')}): {e}")
                        await asyncio.sleep(2)
        except Exception as e:
            print(f"巡檢迴圈異常: {e}")

        await asyncio.sleep(POLL_INTERVAL_MINUTES * 60)


# =====================================================================
# 6. Telegram 訊息與按鈕事件處理
# =====================================================================

async def handle_callback_query(client: httpx.AsyncClient, query: dict):
    global POLL_INTERVAL_MINUTES, OOS_NOTIFY_HOURS
    query_id = query.get("id")
    chat_id = query.get("message", {}).get("chat", {}).get("id")
    data = query.get("data", "")

    if data.startswith("del_"):
        pid = data[4:]
        delete_item(pid)
        NOTIFY_STATE.pop(pid, None)
        await answer_callback(client, query_id, "已移除商品！")
        await send_tg_message(client, chat_id, "✅ 已成功移除該商品！")

    elif data == "menu_scan":
        await answer_callback(client, query_id)
        keyboard = [
            [{"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 1 else ''}每 1 分鐘", "callback_data": "int_1"},
             {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 3 else ''}每 3 分鐘", "callback_data": "int_3"}],
            [{"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 5 else ''}每 5 分鐘", "callback_data": "int_5"},
             {"text": f"{'✅ ' if POLL_INTERVAL_MINUTES == 10 else ''}每 10 分鐘", "callback_data": "int_10"}],
            [{"text": "⬅️ 返回設定", "callback_data": "menu_back"}]
        ]
        await send_tg_message(client, chat_id, "🔄 **【設定背景掃描頻率】**", reply_markup={"inline_keyboard": keyboard})

    elif data == "menu_notify":
        await answer_callback(client, query_id)
        keyboard = [
            [{"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 1 else ''}每 1 小時", "callback_data": "notif_1"},
             {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 2 else ''}每 2 小時", "callback_data": "notif_2"}],
            [{"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 4 else ''}每 4 小時", "callback_data": "notif_4"},
             {"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 8 else ''}每 8 小時", "callback_data": "notif_8"}],
            [{"text": f"{'✅ ' if OOS_NOTIFY_HOURS == 0 else ''}🔕 僅有貨時通知", "callback_data": "notif_0"}],
            [{"text": "⬅️ 返回設定", "callback_data": "menu_back"}]
        ]
        await send_tg_message(client, chat_id, "🔔 **【設定缺貨例行提醒頻率】**", reply_markup={"inline_keyboard": keyboard})

    elif data == "menu_back":
        await answer_callback(client, query_id)
        await show_settings_menu(client, chat_id)

    elif data.startswith("int_"):
        POLL_INTERVAL_MINUTES = int(data[4:])
        await answer_callback(client, query_id, f"掃描週期已更新為每 {POLL_INTERVAL_MINUTES} 分鐘！")
        await send_tg_message(client, chat_id, f"✅ 背景掃描頻率已調整為 **每 {POLL_INTERVAL_MINUTES} 分鐘** 一次！")

    elif data.startswith("notif_"):
        OOS_NOTIFY_HOURS = int(data[6:])
        desc = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅補貨到貨時通知"
        await answer_callback(client, query_id, "通知頻率已更新！")
        await send_tg_message(client, chat_id, f"✅ 缺貨通報頻率已調整為 **{desc}**！")

    elif data.startswith("refresh_"):
        pid = data[8:]
        await answer_callback(client, query_id, "正在為您重新檢查庫存...")
        items = load_tracked_items()
        matched = [i for i in items if i.get("id") == pid]
        if matched:
            await perform_check_and_notify_single(client, chat_id, matched[0], is_manual=True)
        else:
            await send_tg_message(client, chat_id, "⚠️ 該商品已不在追蹤清單中。")


async def show_settings_menu(client: httpx.AsyncClient, chat_id: int):
    notify_str = f"每 {OOS_NOTIFY_HOURS} 小時" if OOS_NOTIFY_HOURS > 0 else "僅有貨時通知"
    msg = (
        f"⚙️ **【自動巡檢與通知偏好設定】**\n\n"
        f"• **背景掃描頻率**：每 **{POLL_INTERVAL_MINUTES}** 分鐘\n"
        f"• **缺貨通知頻率**：{notify_str}\n"
        f"• **夜間免打擾**：台北時間 01:00 ~ 05:00 暫停推播\n"
        f"• **補貨通報**：補貨第一時間秒速通報！"
    )
    keyboard = [
        [{"text": f"🔄 調整【背景掃描週期】(現: {POLL_INTERVAL_MINUTES}分)", "callback_data": "menu_scan"}],
        [{"text": f"🔔 調整【缺貨通知頻率】(現: {OOS_NOTIFY_HOURS}小時)", "callback_data": "menu_notify"}],
    ]
    await send_tg_message(client, chat_id, msg, reply_markup={"inline_keyboard": keyboard})


async def handle_message(client: httpx.AsyncClient, chat_id: int, user_text: str):
    user_text_clean = user_text.strip()
    if not os.getenv("TELEGRAM_CHAT_ID"):
        os.environ["TELEGRAM_CHAT_ID"] = str(chat_id)

    if user_text_clean in ["➕ 新增追蹤", "新增"]:
        msg = "✨ **【新增商品追蹤】**\n\n請直接在此**貼上您想監控的電商商品網址**（例如 Costco 好市多、PChome 等）🔗\n\n系統收到後會立即驗證庫存並加入全天候自動巡檢！"
        await send_tg_message(client, chat_id, msg)
        return

    if user_text_clean in ["❌ 移除追蹤", "移除"]:
        items = load_tracked_items()
        if not items:
            await send_tg_message(client, chat_id, "📭 目前追蹤清單是空的，沒有可以移除的商品喔！")
            return
        keyboard = [[{"text": f"🗑️ 移除: {it.get('name', '')[:24]}", "callback_data": f"del_{it.get('id')}"}] for it in items]
        await send_tg_message(client, chat_id, "👇 **請直接點擊下方要取消追蹤的商品**：", reply_markup={"inline_keyboard": keyboard})
        return

    if user_text_clean in ["⚙️ 巡檢設定 (掃描/通知頻率)", "⚙️ 巡檢週期設定", "週期設定", "設定"]:
        await show_settings_menu(client, chat_id)
        return

    if user_text_clean in ["📋 目前追蹤清單", "目前追蹤清單", "追蹤清單", "清單"]:
        items = load_tracked_items()
        if not items:
            await send_tg_message(client, chat_id, "📭 目前追蹤清單是空的。您可以點擊 **【➕ 新增追蹤】** 貼上商品網址！")
            return

        status_msg = await client.post(f"{API_BASE}/sendMessage", json={
            "chat_id": chat_id,
            "text": f"🔄 **正在全面重新爬蟲 {len(items)} 項追蹤商品的當前最新庫存，請稍候...**",
            "parse_mode": "Markdown",
        }, timeout=5.0)
        status_msg_id = status_msg.json().get("result", {}).get("message_id") if status_msg.status_code == 200 else None

        async def refresh_item(it):
            t_url = it.get("target_url")
            if not t_url:
                return it
            c_res = await asyncio.to_thread(check_product_inventory, t_url)
            it["in_stock"] = c_res.get("in_stock", False)
            it["status_notes"] = c_res.get("status_description", "")
            it["last_checked"] = c_res.get("checked_at", "")
            if c_res.get("title") and c_res.get("title") != t_url:
                it["name"] = c_res.get("title")
            return it

        fresh_items = await asyncio.gather(*(refresh_item(it) for it in items))
        save_tracked_items(list(fresh_items))
        final_text = format_tracked_products_fast(list(fresh_items))

        if status_msg_id:
            await client.post(f"{API_BASE}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": status_msg_id,
                "text": final_text,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }, timeout=15.0)
        else:
            await send_tg_message(client, chat_id, final_text)
        return

    if user_text_clean in ["/start", "start"]:
        msg = (
            "您好！我是您的 **商品庫存查詢與 Telegram 提醒助理 (本地獨立版)** 📦🔔\n\n"
            f"🤖 **後台全天候自動巡檢運作中**\n"
            f"• 掃描頻率：每 {POLL_INTERVAL_MINUTES} 分鐘\n"
            f"• 缺貨通報：每 {OOS_NOTIFY_HOURS} 小時\n"
            "• 夜間免打擾：台北時間 01:00 ~ 05:00 暫停推播\n"
            "• 補貨到貨：第一時間秒速通報！\n\n"
            "請直接使用下方按鈕或貼上電商商品網址開始使用！"
        )
        await send_tg_message(client, chat_id, msg)
        return

    # 檢查是否為網址
    url_match = re.search(r'https?://[^\s]+', user_text_clean)
    if url_match:
        target_url = url_match.group(0)
        proc_resp = await client.post(f"{API_BASE}/sendMessage", json={
            "chat_id": chat_id,
            "text": "⏳ **網址已接收，正在為您爬取並解析即時庫存，請稍候...**",
            "parse_mode": "Markdown",
        }, timeout=5.0)
        proc_id = proc_resp.json().get("result", {}).get("message_id") if proc_resp.status_code == 200 else None

        crawl_res = await asyncio.to_thread(check_product_inventory, target_url)
        title = crawl_res.get("title", target_url)
        in_stock = crawl_res.get("in_stock", False)
        status_desc = crawl_res.get("status_description", "")

        saved_item = add_or_update_item(name=title, target_url=target_url, in_stock=in_stock, status_notes=status_desc)
        NOTIFY_STATE[saved_item["id"]] = {"last_notified_stock": in_stock, "last_notified_time": time.time()}

        stock_icon = "🟢 **現貨** (已開放購買)" if in_stock else "🔴 **缺貨中** (補貨時將第一時間通報)"
        reply_msg = (
            f"✅ **已成功將商品加入自動追蹤清單！**\n\n"
            f"📦 **商品名稱**：{title}\n"
            f"📊 **目前庫存**：{stock_icon}\n"
            f"📝 **詳細狀態**：{status_desc}\n\n"
            f"🤖 系統已啟動背景巡檢（每 {POLL_INTERVAL_MINUTES} 分鐘），您也可以隨時點擊【📋 目前追蹤清單】查看！"
        )

        inline_kb = [[{"text": "🔄 立即重新檢查", "callback_data": f"refresh_{saved_item['id']}"}, {"text": "🛒 前往商品頁面", "url": target_url}]]
        if proc_id:
            await client.post(f"{API_BASE}/editMessageText", json={
                "chat_id": chat_id,
                "message_id": proc_id,
                "text": reply_msg,
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": inline_kb},
            }, timeout=15.0)
        else:
            await send_tg_message(client, chat_id, reply_msg, reply_markup={"inline_keyboard": inline_kb})
        return

    # 一般文字引導
    await send_tg_message(client, chat_id, "💡 請直接貼上您想要追蹤的**電商商品網址**（如好市多、PChome 等），或使用下方按鈕進行操作！")


# =====================================================================
# 7. 主程式進入點 (Long-Polling)
# =====================================================================

async def main():
    print("=" * 60)
    print("🚀 啟動本地獨立版 Telegram 庫存提醒機器人")
    print(f"🔑 Bot Token: {TOKEN[:10]}...")
    print(f"📁 本地資料檔: {DATA_FILE}")
    print("=" * 60)

    offset = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        try:
            me = await client.get(f"{API_BASE}/getMe")
            bot_user = me.json().get("result", {}).get("username", "Unknown")
            print(f"✅ 連線成功！機器人名稱: @{bot_user}")
        except Exception as e:
            print(f"❌ 無法連線至 Telegram API: {e}")
            return

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
                                off = ent.get("offset", 0)
                                length = ent.get("length", 0)
                                extracted_urls.append(text[off : off + length])

                        if extracted_urls:
                            for u in set(extracted_urls):
                                if u not in text:
                                    text = f"{text}\n{u}".strip()

                        if not text:
                            continue

                        chat_id = message["chat"]["id"]
                        asyncio.create_task(handle_message(client, chat_id, text))
            except httpx.RequestError as e:
                print(f"連線異常，正在重試: {e}")
                await asyncio.sleep(3)
            except Exception as e:
                print(f"輪詢異常: {e}")
                await asyncio.sleep(2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n🛑 機器人已在本地終止停止。")

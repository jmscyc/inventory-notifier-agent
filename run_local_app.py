#!/usr/bin/env python3
"""
【本地全功能整合旗艦版 - Web 控制台 + 預約定時搶登入 + 互動式視覺化爬蟲 + 桌面/Telegram 雙軌通知】

亮點功能：
1. 階梯式自動加車與自動結帳 (Auto-Cart & Auto-Checkout)
2. 預約定時執行 (Scheduled Auto-Login / Auto-Trigger)：精準秒級倒數或立即執行
3. 小白友善的「互動式視覺爬蟲抓取精靈」：
   - 附帶圖文並茂的 F12 瀏覽器抓取教學
   - 支援即時「測試抓取」確認目標元素
4. 現代化 Web UI 控制台 (http://localhost:8000)
5. 支援 HTML5 桌面推播通知與 Telegram 雙向通報
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
from typing import Optional, List

import httpx
from bs4 import BeautifulSoup
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel

from app.autobuy_module import (
    execute_autobuy_flow,
    load_autobuy_config,
    save_autobuy_config,
    load_saved_cookies,
    save_cookies,
    get_masked_card_info,
    get_last_diagnostic_export,
)
from app.scheduled_tasks_module import (
    load_scheduled_tasks,
    save_scheduled_tasks,
    add_scheduled_task,
    delete_scheduled_task,
)
from app.custom_crawler_module import (
    load_custom_rules,
    save_custom_rules,
    add_custom_rule,
    delete_custom_rule,
    test_crawl_url,
)

load_dotenv()

DATA_FILE = Path(__file__).parent / "tracked_products.json"
CONFIG_FILE = Path(__file__).parent / "app_config.json"
try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    # 支援 Windows 預設未安裝 tzdata 之環境
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

# =====================================================================
# 1. 組態設定與本地資料持久化
# =====================================================================

DEFAULT_CONFIG = {
    "poll_interval_minutes": 5,
    "oos_notify_hours": 4,
    "enable_telegram": True,
    "enable_local_notification": True,
    "telegram_bot_token": os.getenv("TELEGRAM_BOT_TOKEN", "9200030096:AAFNpPRV2zbQj4DFya0pTSfLi7pUDG7_MR0").strip(),
    "telegram_chat_id": os.getenv("TELEGRAM_CHAT_ID", "").strip(),
}

def load_config() -> dict:
    if not CONFIG_FILE.exists():
        save_config(DEFAULT_CONFIG)
        return dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        return dict(DEFAULT_CONFIG)

def save_config(cfg: dict):
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存設定失敗: {e}")

APP_CONFIG = load_config()
if "is_scheduler_running" not in APP_CONFIG:
    APP_CONFIG["is_scheduler_running"] = True

NOTIFY_STATE = {}
RECENT_LOCAL_NOTIFICATIONS = []
SCHEDULER_STATUS = {
    "is_running": APP_CONFIG.get("is_scheduler_running", True),
    "last_check_time": "",
    "last_check_result": "系統初始化完成，等待第一次巡檢",
    "last_notify_time": "",
    "next_check_seconds": 0,
    "next_notify_seconds": 0,
}

def load_tracked_items() -> list:
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, dict):
                raw_prods = data.get("products", [])
                # 標準化欄位：相容 target_url 與 url，以及 in_stock 與 last_status
                normalized = []
                for p in raw_prods:
                    if isinstance(p, dict):
                        target_url = p.get("target_url") or p.get("url", "")
                        in_stock = p.get("in_stock")
                        if in_stock is None:
                            last_st = p.get("last_status", "")
                            in_stock = "現貨" in last_st or "有現貨" in last_st
                        item_copy = dict(p)
                        item_copy["target_url"] = target_url
                        item_copy["in_stock"] = in_stock
                        normalized.append(item_copy)
                return normalized
            elif isinstance(data, list):
                return data
            return []
    except Exception:
        return []

def save_tracked_items(items: list):
    try:
        data = {}
        if DATA_FILE.exists():
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                raw = json.load(f)
                if isinstance(raw, dict):
                    data = raw
        data["products"] = items
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存商品清單失敗: {e}")

def add_or_update_item(name: str, target_url: str, in_stock: bool, status_notes: str, item_id: str = "", preferred_option: str = "", current_price: str = "") -> dict:
    items = load_tracked_items()
    now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if not item_id:
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
            if preferred_option:
                it["preferred_option"] = preferred_option
            if current_price:
                it["current_price"] = current_price
                history = it.get("price_history", [])
                # 若歷史為空或價格不同，記錄價格變動
                if not history or history[-1].get("price") != current_price:
                    history.append({"time": now_str, "price": current_price, "in_stock": in_stock})
                    if len(history) > 30:
                        history.pop(0)
                    it["price_history"] = history
            updated = True
            break

    if not updated:
        init_history = []
        if current_price:
            init_history.append({"time": now_str, "price": current_price, "in_stock": in_stock})
        items.append({
            "id": item_id,
            "name": name or target_url,
            "target_url": target_url,
            "in_stock": in_stock,
            "status_notes": status_notes,
            "last_checked": now_str,
            "preferred_option": preferred_option or "",
            "current_price": current_price or "",
            "price_history": init_history
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
# 2. 爬蟲檢測模組 (支援通用電商、自訂規則與動態 UA 偽裝)
# =====================================================================

def check_product_inventory(url: str) -> dict:
    from app.autobuy_module import get_random_ua
    headers = {
        "User-Agent": get_random_ua(),
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
            "title": "無法連線至頁面",
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

    # Costco 頁面判定
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
            status_desc = "目前缺貨 (顯示『缺貨』按鈕且無法加入購物車)"
        elif costco_pdp_add_btn and has_qty_selector:
            in_stock = True
            status_desc = "有現貨 (確認有數量增減選單及加入購物車按鈕)"
        elif costco_pdp_add_btn:
            in_stock = True
            status_desc = "有現貨 (具備加入購物車按鈕)"
        else:
            in_stock = False
            status_desc = "目前缺貨 (未發現加入購物車按鈕)"

        # 擷取商品價格 (例如: $1,299 或 1299)
        price_val = ""
        price_tag = soup.find(class_=re.compile(r"price|product-price|total-price", re.I)) or soup.find(id=re.compile(r"price", re.I))
        if price_tag:
            price_val = price_tag.get_text().strip()
        elif match_p := re.search(r"(\$|NT\$|NT\s?)\s?(\d{1,3}(,\d{3})*(\.\d+)?)", html_content):
            price_val = match_p.group(0).strip()

        return {
            "url": url,
            "title": title,
            "in_stock": in_stock,
            "status_description": status_desc,
            "checked_at": now_str,
            "price": price_val
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
        status_desc = "目前缺貨 / 售完"
    elif has_in and not has_oos:
        in_stock = True
        status_desc = "目前有現貨 / 可加入購物車"
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
# 3. 雙向通知發送器 (本機推播 + Telegram)
# =====================================================================

def is_quiet_hours() -> bool:
    now_taipei = datetime.datetime.now(TAIPEI_TZ)
    return 1 <= now_taipei.hour < 5

async def broadcast_notification(client: Optional[httpx.AsyncClient], title: str, body: str, target_url: str = "", button_text: str = "🛒 前往目標頁面"):
    if APP_CONFIG.get("enable_local_notification", True):
        event = {
            "id": f"notif_{int(time.time() * 1000)}",
            "title": title.replace("**", "").replace("【", "").replace("】", ""),
            "body": body.replace("**", ""),
            "url": target_url,
            "timestamp": datetime.datetime.now(TAIPEI_TZ).strftime("%H:%M:%S")
        }
        RECENT_LOCAL_NOTIFICATIONS.append(event)
        if len(RECENT_LOCAL_NOTIFICATIONS) > 30:
            RECENT_LOCAL_NOTIFICATIONS.pop(0)
    SCHEDULER_STATUS["last_notify_time"] = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")

    if APP_CONFIG.get("enable_telegram", True) and client:
        token = APP_CONFIG.get("telegram_bot_token")
        chat_id = APP_CONFIG.get("telegram_chat_id")
        if token and chat_id:
            api_url = f"https://api.telegram.org/bot{token}/sendMessage"
            payload = {
                "chat_id": chat_id,
                "text": f"{title}\n\n{body}",
                "parse_mode": "Markdown",
                "disable_web_page_preview": True,
            }
            if target_url:
                payload["reply_markup"] = {
                    "inline_keyboard": [[{"text": button_text, "url": target_url}]]
                }
            try:
                await client.post(api_url, json=payload, timeout=10.0)
            except Exception as e:
                print(f"Telegram 推播失敗: {e}")


async def perform_check_and_notify_single(client: Optional[httpx.AsyncClient], item: dict, is_manual: bool = False):
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

    add_or_update_item(name=crawl_res.get("title") or name, target_url=target_url, in_stock=in_stock, status_notes=status_desc, item_id=item_id)

    state = NOTIFY_STATE.get(item_id, {"last_notified_stock": None, "last_notified_time": 0})
    last_notified_stock = state.get("last_notified_stock")
    last_notified_time = state.get("last_notified_time", 0)

    should_send = False
    notify_title = ""
    notify_body = ""
    action_url = target_url
    btn_text = "🛒 前往商品頁面"

    poll_interval = APP_CONFIG.get("poll_interval_minutes", 5)
    oos_hours = APP_CONFIG.get("oos_notify_hours", 4)

    if is_manual:
        should_send = True
        if in_stock:
            flow_res = await execute_autobuy_flow(target_url, name)
            notify_title = flow_res["status_title"]
            notify_body = flow_res["status_message"]
            action_url = flow_res["action_url"]
            btn_text = flow_res["action_button_text"]
        else:
            notify_title = "ℹ️ **【手動更新：目前依然缺貨中】**"
            notify_body = f"📦 **商品**：{name}\n🔴 目前狀態：{status_desc}\n系統維持每 {poll_interval} 分鐘自動監控！"
    else:
        if is_quiet_hours():
            NOTIFY_STATE[item_id] = {"last_notified_stock": last_notified_stock, "last_notified_time": last_notified_time}
            return

        if in_stock:
            # 有貨：若先前是缺貨、未曾通知，或者距離上次有貨通知超過 15 分鐘，持續提醒有現貨！
            if (last_notified_stock is not True) or (now_ts - last_notified_time >= 900):
                should_send = True
                p_opt = item.get("preferred_option", "")
                flow_res = await execute_autobuy_flow(target_url, name, preferred_option=p_opt)
                notify_title = flow_res["status_title"]
                notify_body = flow_res["status_message"]
                action_url = flow_res["action_url"]
                btn_text = flow_res["action_button_text"]

        elif (not in_stock) and (last_notified_stock is None or last_notified_stock is True):
            should_send = True
            notify_title = "📢 **【商品目前缺貨提醒】**"
            notify_body = f"📦 **商品**：{name}\n🔴 **狀態**：{status_desc}\n系統正每 {poll_interval} 分鐘自動巡檢！"

        elif (not in_stock) and (last_notified_stock is False):
            if oos_hours > 0:
                if (now_ts - last_notified_time) >= (oos_hours * 3600):
                    should_send = True
                    notify_title = f"⏳ **【商品持續缺貨追蹤中 ({oos_hours}小時例行回報)】**"
                    notify_body = f"📦 **商品**：{name}\n🔴 目前仍然缺貨中，系統正持續為您巡檢！"

    if should_send:
        NOTIFY_STATE[item_id] = {"last_notified_stock": in_stock, "last_notified_time": now_ts}
        await broadcast_notification(client, notify_title, notify_body, action_url, btn_text)


# =====================================================================
# 4. 背景排程 Worker (巡檢 + 預約搶登入排程)
# =====================================================================

async def scheduled_tasks_worker():
    """定期檢查並執行時間到達的預約任務"""
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                tasks = load_scheduled_tasks()
                now = datetime.datetime.now(TAIPEI_TZ)
                now_str = now.strftime("%Y-%m-%d %H:%M:%S")

                for t in tasks:
                    if t.get("status") == "PENDING":
                        exec_at = t.get("execute_at", "")
                        is_ready = False
                        if exec_at == "IMMEDIATE":
                            is_ready = True
                        else:
                            try:
                                target_time = datetime.datetime.strptime(exec_at, "%Y-%m-%d %H:%M:%S").replace(tzinfo=TAIPEI_TZ)
                                if now >= target_time:
                                    is_ready = True
                            except Exception:
                                pass

                        if is_ready:
                            t["status"] = "RUNNING"
                            save_scheduled_tasks(tasks)
                            print(f"🎯 觸發預約任務：{t.get('title')} -> {t.get('target_url')}")

                            # 執行搶購或登入導向流程
                            flow_res = await execute_autobuy_flow(t.get("target_url"), t.get("title"))
                            t["status"] = "COMPLETED"
                            t["result_message"] = flow_res["status_title"]
                            save_scheduled_tasks(tasks)

                            # 推播通知
                            await broadcast_notification(
                                client,
                                f"⏰ **【預約任務觸發成功】** {t.get('title')}",
                                f"{flow_res['status_message']}\n時間：`{now_str}`",
                                flow_res["action_url"],
                                flow_res["action_button_text"]
                            )
            except Exception as e:
                print(f"預約任務巡檢異常: {e}")

            await asyncio.sleep(2)


async def background_scheduler():
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            is_on = APP_CONFIG.get("is_scheduler_running", True)
            SCHEDULER_STATUS["is_running"] = is_on
            interval_min = APP_CONFIG.get("poll_interval_minutes", 5)

            if not is_on:
                SCHEDULER_STATUS["last_check_result"] = "監控排程已暫停 (OFF)"
                SCHEDULER_STATUS["next_check_seconds"] = 0
                await asyncio.sleep(2)
                continue

            # 開始執行巡檢
            now = datetime.datetime.now(TAIPEI_TZ)
            now_str = now.strftime("%Y-%m-%d %H:%M:%S")
            SCHEDULER_STATUS["last_check_time"] = now_str

            items = load_tracked_items()
            in_stock_count = 0
            oos_count = 0

            try:
                for item in items:
                    if item.get("target_url"):
                        try:
                            await perform_check_and_notify_single(client, item, is_manual=False)
                        except Exception as e:
                            print(f"巡檢異常 ({item.get('id')}): {e}")
                        await asyncio.sleep(2)
                
                # 計算本次檢查結果摘要
                refreshed_items = load_tracked_items()
                for it in refreshed_items:
                    if it.get("in_stock"):
                        in_stock_count += 1
                    else:
                        oos_count += 1

                if len(refreshed_items) == 0:
                    SCHEDULER_STATUS["last_check_result"] = "檢查完成：目前尚無追蹤商品"
                else:
                    SCHEDULER_STATUS["last_check_result"] = f"共檢查 {len(refreshed_items)} 件商品 (現貨: {in_stock_count} 件 / 缺貨: {oos_count} 件)"
            except Exception as e:
                SCHEDULER_STATUS["last_check_result"] = f"巡檢發生異常: {str(e)}"
                print(f"排程迴圈例外: {e}")

            # 倒數計時器 (加入 ±10% 隨機防爬 Jitter)
            from app.autobuy_module import get_jitter_seconds
            base_s = max(interval_min * 60, 10)
            total_seconds = int(get_jitter_seconds(base_s, jitter_pct=0.1))
            for remaining in range(total_seconds, 0, -1):
                if not APP_CONFIG.get("is_scheduler_running", True):
                    break
                SCHEDULER_STATUS["next_check_seconds"] = remaining
                await asyncio.sleep(1)


async def telegram_bot_worker():
    offset = 0
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            if not APP_CONFIG.get("enable_telegram", True):
                await asyncio.sleep(5)
                continue

            token = APP_CONFIG.get("telegram_bot_token")
            if not token:
                await asyncio.sleep(5)
                continue

            api_base = f"https://api.telegram.org/bot{token}"
            try:
                resp = await client.get(f"{api_base}/getUpdates", params={"offset": offset, "timeout": 20})
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        offset = update["update_id"] + 1
                        msg = update.get("message")
                        if not msg:
                            continue
                        chat_id = msg.get("chat", {}).get("id")
                        text = msg.get("text", "").strip()

                        if str(chat_id) != APP_CONFIG.get("telegram_chat_id"):
                            APP_CONFIG["telegram_chat_id"] = str(chat_id)
                            save_config(APP_CONFIG)

                        if text in ["/start", "start", "清單", "目前追蹤清單"]:
                            items = load_tracked_items()
                            lines = ["📦 **【目前追蹤商品清單】**", ""]
                            for i, it in enumerate(items, 1):
                                st = "🟢 現貨" if it.get("in_stock") else "🔴 缺貨"
                                lines.append(f"{i}. **{it.get('name')}** | {st}")
                                lines.append(f"   [前往購買]({it.get('target_url')})\n")
                            await client.post(f"{api_base}/sendMessage", json={
                                "chat_id": chat_id, "text": "\n".join(lines) if items else "📭 目前追蹤清單是空的！",
                                "parse_mode": "Markdown", "disable_web_page_preview": True
                            })
            except Exception:
                await asyncio.sleep(3)


# =====================================================================
# 5. FastAPI 路由與 API
# =====================================================================

app = FastAPI(title="商品庫存監控與智慧自動化控制台")

class AddItemReq(BaseModel):
    url: str
    preferred_option: Optional[str] = ""

class UpdateConfigReq(BaseModel):
    poll_interval_minutes: int
    oos_notify_hours: int
    enable_telegram: bool
    enable_local_notification: bool
    telegram_bot_token: Optional[str] = ""

class UpdateAutobuyReq(BaseModel):
    enabled: bool
    auto_checkout: bool
    auto_submit_payment: Optional[bool] = False
    payment_method: Optional[str] = "stored_card"  # stored_card | custom_card | cod
    card_number: Optional[str] = ""
    card_exp_month: Optional[str] = ""
    card_exp_year: Optional[str] = ""
    card_cvv: Optional[str] = ""
    card_holder_name: Optional[str] = ""
    account_email: Optional[str] = ""
    account_password: Optional[str] = ""
    cookies_json: Optional[str] = ""

class AutobuyTestReq(BaseModel):
    target_url: str
    product_name: Optional[str] = "測試商品"
    preferred_option: Optional[str] = ""

class AddScheduledTaskReq(BaseModel):
    title: str
    target_url: str
    execute_at: str
    action_type: Optional[str] = "login_and_open"

class TestCrawlReq(BaseModel):
    url: str
    selector: Optional[str] = ""
    match_text: Optional[str] = ""

class AddCustomRuleReq(BaseModel):
    name: str
    target_url: str
    selector: Optional[str] = ""
    match_text: Optional[str] = ""
    check_type: Optional[str] = "text_contains"

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scheduler())
    # 避免與專屬 telegram_bot_service 搶奪 update 衝突
    if os.getenv("ENABLE_LOCAL_TG_WORKER", "false").lower() == "true":
        asyncio.create_task(telegram_bot_worker())
    asyncio.create_task(scheduled_tasks_worker())

@app.get("/api/items")
def api_get_items():
    return {"items": load_tracked_items()}

@app.post("/api/items")
async def api_add_item(req: AddItemReq):
    url = req.url.strip()
    preferred_option = req.preferred_option.strip() if req.preferred_option else ""
    if not url.startswith("http"):
        return JSONResponse({"success": False, "message": "請輸入有效的 HTTP/HTTPS 商品網址！"}, status_code=400)
    crawl_res = await asyncio.to_thread(check_product_inventory, url)
    title = crawl_res.get("title", url)
    in_stock = crawl_res.get("in_stock", False)
    status_desc = crawl_res.get("status_description", "")
    item = add_or_update_item(name=title, target_url=url, in_stock=in_stock, status_notes=status_desc, preferred_option=preferred_option)
    # 重設通知狀態為 None，讓新增後首次檢查或即刻觸發有貨/缺貨通知
    NOTIFY_STATE[item["id"]] = {"last_notified_stock": None, "last_notified_time": 0}
    # 若有現貨，新增當下立即發出第一次推播通知！
    async with httpx.AsyncClient(timeout=30.0) as client:
        await perform_check_and_notify_single(client, item, is_manual=True)
    return {"success": True, "item": item, "in_stock": in_stock, "status_notes": status_desc}

@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: str):
    ok = delete_item(item_id)
    NOTIFY_STATE.pop(item_id, None)
    return {"success": ok}

@app.post("/api/items/refresh_all")
async def api_refresh_all():
    items = load_tracked_items()
    async with httpx.AsyncClient(timeout=30.0) as client:
        for it in items:
            if it.get("target_url"):
                try:
                    await perform_check_and_notify_single(client, it, is_manual=False)
                except Exception as e:
                    print(f"refresh_all 異常: {e}")
                await asyncio.sleep(1)
    fresh = load_tracked_items()
    return {"success": True, "items": fresh}

@app.post("/api/items/{item_id}/refresh")
async def api_refresh_single(item_id: str):
    items = load_tracked_items()
    matched = [it for it in items if it.get("id") == item_id]
    if not matched:
        return JSONResponse({"success": False, "message": "查無此商品"}, status_code=404)
    async with httpx.AsyncClient() as client:
        await perform_check_and_notify_single(client, matched[0], is_manual=True)
    return {"success": True}

@app.get("/api/scheduler/status")
def api_get_scheduler_status():
    is_on = APP_CONFIG.get("is_scheduler_running", True)
    oos_hours = APP_CONFIG.get("oos_notify_hours", 4)
    
    # 計算下一次主動通知還有多久 (以追蹤中的缺貨商品為基準)
    next_notify_seconds = None
    if is_on and oos_hours > 0:
        now_ts = time.time()
        # 尋找最快需要通報缺貨的商品
        min_rem = None
        for it_id, st in NOTIFY_STATE.items():
            if st.get("last_notified_stock") is False:
                last_time = st.get("last_notified_time", 0)
                target_notify_ts = last_time + (oos_hours * 3600)
                rem = max(0, int(target_notify_ts - now_ts))
                if min_rem is None or rem < min_rem:
                    min_rem = rem
        if min_rem is not None:
            next_notify_seconds = min_rem
        else:
            # 尚未有進入缺貨週期的通知，預設為下一次週期
            next_notify_seconds = oos_hours * 3600
    elif oos_hours == 0:
        next_notify_seconds = -1 # 代表僅補貨時通知

    return {
        "is_running": is_on,
        "poll_interval_minutes": APP_CONFIG.get("poll_interval_minutes", 5),
        "oos_notify_hours": oos_hours,
        "last_check_time": SCHEDULER_STATUS.get("last_check_time") or "尚未執行",
        "last_check_result": SCHEDULER_STATUS.get("last_check_result") or "尚未執行",
        "last_notify_time": SCHEDULER_STATUS.get("last_notify_time") or "尚未觸發通知",
        "next_check_seconds": SCHEDULER_STATUS.get("next_check_seconds", 0),
        "next_notify_seconds": next_notify_seconds
    }

@app.post("/api/scheduler/toggle")
def api_toggle_scheduler():
    current = APP_CONFIG.get("is_scheduler_running", True)
    APP_CONFIG["is_scheduler_running"] = not current
    save_config(APP_CONFIG)
    SCHEDULER_STATUS["is_running"] = APP_CONFIG["is_scheduler_running"]
    if not APP_CONFIG["is_scheduler_running"]:
        SCHEDULER_STATUS["next_check_seconds"] = 0
        SCHEDULER_STATUS["last_check_result"] = "監控排程已暫停 (OFF)"
    return {"success": True, "is_running": APP_CONFIG["is_scheduler_running"]}

@app.get("/api/config")
def api_get_config():
    return APP_CONFIG

@app.post("/api/config")
def api_update_config(req: UpdateConfigReq):
    APP_CONFIG["poll_interval_minutes"] = req.poll_interval_minutes
    APP_CONFIG["oos_notify_hours"] = req.oos_notify_hours
    APP_CONFIG["enable_telegram"] = req.enable_telegram
    APP_CONFIG["enable_local_notification"] = req.enable_local_notification
    if req.telegram_bot_token:
        APP_CONFIG["telegram_bot_token"] = req.telegram_bot_token.strip()
    save_config(APP_CONFIG)
    return {"success": True, "config": APP_CONFIG}

@app.get("/api/autobuy")
def api_get_autobuy():
    cfg = load_autobuy_config()
    cookies = load_saved_cookies()
    masked = get_masked_card_info(cfg)
    return {
        "config": {
            "enabled": cfg.get("enabled", False),
            "auto_checkout": cfg.get("auto_checkout", False),
            "auto_submit_payment": cfg.get("auto_submit_payment", False),
            "payment_method": cfg.get("payment_method", "stored_card"),
            "account_email": cfg.get("account_email", ""),
        },
        "card_info": masked,
        "has_cookies": bool(cookies),
        "cookie_count": len(cookies),
    }

@app.post("/api/autobuy")
def api_update_autobuy(req: UpdateAutobuyReq):
    cfg = load_autobuy_config()
    cfg["enabled"] = req.enabled
    cfg["auto_checkout"] = req.auto_checkout
    cfg["auto_submit_payment"] = req.auto_submit_payment or False
    if req.payment_method:
        cfg["payment_method"] = req.payment_method

    if "card_info" not in cfg or not isinstance(cfg["card_info"], dict):
        cfg["card_info"] = {}

    # 若有更新信用卡號（非空且不為掩碼）
    if req.card_number and "*" not in req.card_number:
        cfg["card_info"]["card_number"] = req.card_number.replace(" ", "").replace("-", "")
    if req.card_exp_month:
        cfg["card_info"]["card_exp_month"] = req.card_exp_month.strip()
    if req.card_exp_year:
        cfg["card_info"]["card_exp_year"] = req.card_exp_year.strip()
    if req.card_cvv and "*" not in req.card_cvv:
        cfg["card_info"]["card_cvv"] = req.card_cvv.strip()
    if req.card_holder_name:
        cfg["card_info"]["card_holder_name"] = req.card_holder_name.strip()

    if req.account_email:
        cfg["account_email"] = req.account_email.strip()
    if req.account_password:
        cfg["account_password"] = req.account_password.strip()
    save_autobuy_config(cfg)

    if req.cookies_json:
        try:
            parsed_cookies = json.loads(req.cookies_json)
            if isinstance(parsed_cookies, list):
                c_dict = {c["name"]: c["value"] for c in parsed_cookies if "name" in c and "value" in c}
            elif isinstance(parsed_cookies, dict):
                c_dict = parsed_cookies
            else:
                c_dict = {}
            if c_dict:
                save_cookies(c_dict)
        except Exception as e:
            return JSONResponse({"success": False, "message": f"Cookie 格式錯誤: {e}"}, status_code=400)

    return {"success": True, "config": cfg, "card_info": get_masked_card_info(cfg)}

# 自動加車與結帳沙盒測試 API
@app.post("/api/autobuy/test")
async def api_test_autobuy(req: AutobuyTestReq):
    url = req.target_url.strip()
    preferred_opt = req.preferred_option.strip() if req.preferred_option else ""
    if not url.startswith("http"):
        return JSONResponse({"success": False, "message": "請輸入有效商品網址！"}, status_code=400)
    res = await execute_autobuy_flow(url, req.product_name or "測試商品", is_test_run=True, preferred_option=preferred_opt)
    return {"success": True, "result": res}

# AI 除錯診斷報告導出 API
@app.get("/api/autobuy/debug_report")
def api_get_debug_report():
    report_markdown = get_last_diagnostic_export()
    return {"success": True, "report_markdown": report_markdown}

# Cookie 憑證健康度檢查 API
@app.get("/api/cookie/health")
async def api_get_cookie_health():
    from app.autobuy_module import check_cookie_health
    res = await check_cookie_health()
    return {"success": True, "health": res}

# 系統一鍵備份匯出 API (Backup Export)
@app.get("/api/backup/export")
def api_backup_export():
    settings = APP_CONFIG
    products = load_tracked_items()
    scheduled_tasks = load_scheduled_tasks()
    from app.custom_crawler_module import load_custom_rules
    custom_rules = load_custom_rules()
    
    backup_data = {
        "export_time": datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "version": "2.0-flagship",
        "settings": settings,
        "products": products,
        "scheduled_tasks": scheduled_tasks,
        "custom_rules": custom_rules
    }
    return JSONResponse(
        content=backup_data,
        headers={"Content-Disposition": "attachment; filename=monitor_system_backup.json"}
    )

# 系統一鍵備份匯入還原 API (Backup Import)
class RestoreBackupReq(BaseModel):
    backup_json: str

@app.post("/api/backup/restore")
def api_backup_restore(req: RestoreBackupReq):
    try:
        data = json.loads(req.backup_json)
        if not isinstance(data, dict):
            return JSONResponse({"success": False, "message": "備份檔案內容必須是 JSON 物件"}, status_code=400)
        
        # 還原 settings
        if "settings" in data and isinstance(data["settings"], dict):
            for k, v in data["settings"].items():
                APP_CONFIG[k] = v
            save_config(APP_CONFIG)
            
        # 還原 products
        if "products" in data and isinstance(data["products"], list):
            save_tracked_items(data["products"])
            
        # 還原 scheduled_tasks
        if "scheduled_tasks" in data and isinstance(data["scheduled_tasks"], list):
            save_scheduled_tasks(data["scheduled_tasks"])
            
        # 還原 custom_rules
        if "custom_rules" in data and isinstance(data["custom_rules"], list):
            from app.custom_crawler_module import save_custom_rules
            save_custom_rules(data["custom_rules"])
            
        return {"success": True, "message": "🎉 系統所有設定、商品與規則已成功還原！"}
    except Exception as e:
        return JSONResponse({"success": False, "message": f"還原失敗: {str(e)}"}, status_code=400)

# 預約任務 API
@app.get("/api/scheduled_tasks")
def api_get_scheduled_tasks():
    return {"tasks": load_scheduled_tasks()}

@app.post("/api/scheduled_tasks")
def api_add_scheduled_task(req: AddScheduledTaskReq):
    task = add_scheduled_task(req.title, req.target_url, req.execute_at, req.action_type or "login_and_open")
    return {"success": True, "task": task}

@app.delete("/api/scheduled_tasks/{task_id}")
def api_delete_scheduled_task(task_id: str):
    ok = delete_scheduled_task(task_id)
    return {"success": ok}

# 自訂爬蟲與測試 API
@app.post("/api/custom_crawler/test")
def api_test_crawl(req: TestCrawlReq):
    res = test_crawl_url(req.url, req.selector or "", req.match_text or "")
    return res

@app.get("/api/custom_crawler/rules")
def api_get_custom_rules():
    return {"rules": load_custom_rules()}

@app.post("/api/custom_crawler/rules")
def api_add_custom_rule(req: AddCustomRuleReq):
    rule = add_custom_rule(req.name, req.target_url, req.selector or "", req.match_text or "", req.check_type or "text_contains")
    return {"success": True, "rule": rule}

@app.delete("/api/custom_crawler/rules/{rule_id}")
def api_delete_custom_rule(rule_id: str):
    ok = delete_custom_rule(rule_id)
    return {"success": ok}

@app.get("/api/notifications/poll")
def api_poll_notifications(since_id: Optional[str] = None):
    if not since_id:
        return {"notifications": RECENT_LOCAL_NOTIFICATIONS[-5:]}
    idx = -1
    for i, notif in enumerate(RECENT_LOCAL_NOTIFICATIONS):
        if notif["id"] == since_id:
            idx = i
            break
    if idx >= 0:
        new_items = RECENT_LOCAL_NOTIFICATIONS[idx + 1:]
    else:
        new_items = RECENT_LOCAL_NOTIFICATIONS[-5:]
    return {"notifications": new_items}


# =====================================================================
# 6. Web 控制台介面 (含 F12 小白視覺教學 + 預約任務 + 自訂爬蟲)
# =====================================================================

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>智慧庫存監控、預約搶登入與視覺爬蟲工作台</title>
  <link href="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/css/bootstrap.min.css" rel="stylesheet">
  <link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.min.css">
  <style>
    body { background-color: #f4f6f9; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif; }
    .header-card { background: linear-gradient(135deg, #1e3c72 0%, #2a5298 100%); color: white; border-radius: 12px; }
    .product-card { border: none; border-radius: 10px; box-shadow: 0 2px 8px rgba(0,0,0,0.06); transition: transform 0.2s; }
    .product-card:hover { transform: translateY(-2px); box-shadow: 0 4px 14px rgba(0,0,0,0.1); }
    .badge-in-stock { background-color: #198754; font-size: 0.9rem; }
    .badge-out-of-stock { background-color: #dc3545; font-size: 0.9rem; }
    .autobuy-box { background-color: #fff9e6; border: 1px solid #ffeeba; border-radius: 8px; }
    .f12-box { background-color: #e8f4fd; border-left: 4px solid #0d6efd; border-radius: 4px; padding: 12px; }
    .nav-tabs .nav-link { font-weight: bold; color: #495057; }
    .nav-tabs .nav-link.active { color: #0d6efd; border-bottom: 3px solid #0d6efd; }
  </style>
</head>
<body class="p-3 p-md-4">
<div class="container-fluid" style="max-width: 1200px;">

  <!-- 頂部 Header -->
  <div class="p-4 mb-4 header-card shadow-sm d-flex flex-wrap justify-content-between align-items-center">
    <div>
      <h3 class="mb-1 fw-bold"><i class="bi bi-robot me-2"></i>智慧自動化工作台：庫存監控・預約搶登入・自訂爬蟲</h3>
      <p class="mb-0 text-white-50">支援電商現貨偵測、階梯式自動加車、毫秒級定時預約執行與小白視覺化 F12 抓取輔助</p>
    </div>
    <div class="mt-3 mt-md-0 d-flex gap-2">
      <button class="btn btn-outline-light btn-sm" onclick="requestNotificationPermission()">
        <i class="bi bi-bell-fill me-1"></i><span id="notifBtnText">啟用本機推播通知</span>
      </button>
      <button class="btn btn-outline-light btn-sm text-white" onclick="exportBackup()">
        <i class="bi bi-cloud-arrow-down me-1"></i>匯出備份
      </button>
      <button class="btn btn-outline-light btn-sm text-white" onclick="document.getElementById('importBackupFile').click()">
        <i class="bi bi-cloud-arrow-up me-1"></i>還原備份
      </button>
      <input type="file" id="importBackupFile" style="display:none" accept=".json" onchange="handleRestoreBackup(event)">
      <button class="btn btn-light btn-sm text-primary fw-bold" onclick="fetchAll(true)">
        <i class="bi bi-arrow-clockwise me-1"></i>全部重新爬蟲
      </button>
    </div>
  </div>

  <!-- ⏱️ 即時排程與巡檢狀態儀表板 -->
  <div class="card shadow-sm border-0 mb-4 rounded-3" style="background: white;">
    <div class="card-body p-3 p-md-4">
      <div class="row g-3 align-items-center">
        <!-- 狀態開關 -->
        <div class="col-md-3 border-end-md">
          <div class="d-flex align-items-center gap-3">
            <div id="schedulerStatusBadge" class="rounded-circle p-3 d-flex align-items-center justify-content-center bg-success-subtle text-success" style="width: 55px; height: 55px;">
              <i class="bi bi-broadcast fs-3" id="schedulerIcon"></i>
            </div>
            <div>
              <div class="text-muted small fw-bold">背景巡檢排程狀態</div>
              <div class="d-flex align-items-center gap-2 mt-1">
                <span class="badge bg-success fs-6 px-3 py-1" id="schedulerStateText">運作中 (ON)</span>
                <button class="btn btn-sm btn-outline-secondary" id="toggleSchedulerBtn" onclick="toggleScheduler()">
                  暫停監控
                </button>
              </div>
            </div>
          </div>
        </div>

        <!-- 倒數計時與下一次預估 -->
        <div class="col-md-5 border-end-md">
          <div class="row g-2">
            <div class="col-6">
              <div class="text-muted small"><i class="bi bi-hourglass-split text-primary me-1"></i>下一次檢查倒數</div>
              <h5 class="fw-bold text-primary mb-0 mt-1" id="nextCheckCountdown">--:--</h5>
              <div class="text-muted" style="font-size: 0.75rem;" id="pollCycleHint">週期: 5 分鐘 (±10% Jitter)</div>
            </div>
            <div class="col-6">
              <div class="text-muted small"><i class="bi bi-bell text-warning me-1"></i>下一次主動通知</div>
              <h5 class="fw-bold text-dark mb-0 mt-1" id="nextNotifyCountdown">--:--</h5>
              <div class="text-muted" style="font-size: 0.75rem;" id="oosCycleHint">缺貨通知: 4 小時/次</div>
            </div>
          </div>
        </div>

        <!-- 最近一次檢查時間與 Cookie 健康指標 -->
        <div class="col-md-4">
          <div class="d-flex justify-content-between align-items-center">
            <div class="text-muted small"><i class="bi bi-clock-history me-1"></i>最近檢查：<span class="fw-bold text-dark" id="lastCheckTimeText">尚未執行</span></div>
            <div id="cookieHealthBadge" class="badge bg-secondary cursor-pointer" onclick="checkCookieHealthUI()" title="點擊檢驗憑證健康度">
              🍪 檢查 Cookie
            </div>
          </div>
          <div class="mt-1 text-truncate" style="font-size: 0.85rem;">
            <span class="badge bg-light text-dark border me-1">結果</span>
            <span id="lastCheckResultText" class="text-muted">系統就緒中...</span>
          </div>
          <div class="mt-1 text-muted" style="font-size: 0.75rem;">
            <i class="bi bi-send-check me-1"></i>最近推播：<span id="lastNotifyTimeText">尚未觸發通知</span>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 分頁導覽列 -->
  <ul class="nav nav-tabs mb-4" id="mainTab" role="tablist">
    <li class="nav-item">
      <button class="nav-link active" id="tab-inventory" data-bs-toggle="tab" data-bs-target="#pane-inventory" type="button">
        <i class="bi bi-box-seam me-1"></i>商品庫存監控與搶購
      </button>
    </li>
    <li class="nav-item">
      <button class="nav-link" id="tab-schedule" data-bs-toggle="tab" data-bs-target="#pane-schedule" type="button">
        <i class="bi bi-alarm me-1"></i>預約搶登入 / 定時任務
      </button>
    </li>
    <li class="nav-item">
      <button class="nav-link" id="tab-custom" data-bs-toggle="tab" data-bs-target="#pane-custom" type="button">
        <i class="bi bi-magic me-1"></i>萬用網頁爬蟲精靈 (F12小白指引)
      </button>
    </li>
  </ul>

  <!-- 分頁內容 -->
  <div class="tab-content" id="mainTabContent">

    <!-- ======================= 分頁 1: 庫存監控與搶購 ======================= -->
    <div class="tab-pane fade show active" id="pane-inventory" role="tabpanel">
      <div class="row g-4">
        <div class="col-lg-8">
          <div class="card shadow-sm border-0 mb-4 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-plus-circle text-primary me-2"></i>新增追蹤商品</h5>
              <div class="row g-2">
                <div class="col-md-8">
                  <input type="url" id="newUrlInput" class="form-control form-control-lg" placeholder="請貼上商品完整網址 (例: https://www.costco.com.tw/.../p/123456)">
                </div>
                <div class="col-md-4">
                  <input type="text" id="newPreferredOptionInput" class="form-control form-control-lg" placeholder="指定尺寸/規格 (選填, 例: L號/黑)">
                </div>
              </div>
              <div class="d-flex justify-content-between align-items-center mt-3">
                <div class="form-text text-muted">支援指定顏色、尺寸自動點選。加入前即刻至頁面檢驗是否具備現貨與數量選單。</div>
                <button class="btn btn-primary px-4 fw-bold" id="addBtn" onclick="addProduct()">
                  <span id="addBtnSpinner" class="spinner-border spinner-border-sm d-none me-1"></span>新增追蹤
                </button>
              </div>
            </div>
          </div>

          <div class="d-flex justify-content-between align-items-center mb-3">
            <h5 class="fw-bold mb-0"><i class="bi bi-list-task me-2"></i>追蹤清單 (<span id="itemCount">0</span>)</h5>
            <small class="text-muted" id="lastUpdatedText"></small>
          </div>
          <div id="productList" class="d-flex flex-column gap-3">
            <div class="text-center py-5 text-muted">正在載入商品清單...</div>
          </div>
        </div>

        <div class="col-lg-4">
          <!-- 搶購設定 -->
          <div class="card shadow-sm border-0 rounded-3 mb-4 autobuy-box">
            <div class="card-body p-4">
              <h5 class="fw-bold text-dark mb-2"><i class="bi bi-lightning-charge-fill text-warning me-2"></i>自動加車與搶購模組</h5>
              <p class="small text-muted mb-3">階梯式保護：每一段失敗至少保留前段正常執行並即時通知！</p>

              <div class="form-check form-switch mb-2">
                <input class="form-check-input" type="checkbox" id="cfgAutobuyEnabled">
                <label class="form-check-label fw-bold" for="cfgAutobuyEnabled">⚡ 啟用第一階段：自動加入購物車</label>
              </div>
              <small class="text-muted d-block mb-3">補貨時自動把商品鎖在購物車，推播「已放入購物車！請在 15 分鐘內結帳」。</small>

              <div class="form-check form-switch mb-2">
                <input class="form-check-input" type="checkbox" id="cfgAutoCheckout">
                <label class="form-check-label fw-bold" for="cfgAutoCheckout">🚀 啟用第二階段：自動直達結帳確認</label>
              </div>
              <small class="text-muted d-block mb-3">加車後自動推進至最後付款確認頁面，為您省下所有排隊步驟。</small>

              <!-- 付款方式選擇 -->
              <div class="mb-3">
                <label class="form-label small fw-bold mb-1">💳 結帳付款方式</label>
                <select id="cfgPaymentMethod" class="form-select form-select-sm" onchange="togglePaymentInputs()">
                  <option value="stored_card">使用帳戶已存預設信用卡 (最安全快速)</option>
                  <option value="custom_card">預先輸入信用卡卡號與資訊</option>
                  <option value="cod">貨到付款 / 超商取貨付款</option>
                </select>
              </div>

              <!-- 自訂信用卡欄位區塊 (折疊式) -->
              <div id="creditCardSection" class="p-3 mb-3 bg-light rounded-3 border" style="display: none;">
                <div class="d-flex justify-content-between align-items-center mb-2">
                  <span class="small fw-bold text-dark"><i class="bi bi-credit-card-2-front text-primary me-1"></i>信用卡安全預填</span>
                  <span class="badge bg-secondary" id="cardStatusBadge">未填寫</span>
                </div>
                <div class="mb-2">
                  <label class="form-label small mb-0">卡號 (16 碼)</label>
                  <input type="text" id="cfgCardNumber" class="form-control form-control-sm" placeholder="4123 4567 8901 2345" autocomplete="off">
                </div>
                <div class="row g-2 mb-2">
                  <div class="col-4">
                    <label class="form-label small mb-0">月份 (MM)</label>
                    <input type="text" id="cfgCardExpMonth" class="form-control form-control-sm" placeholder="08" maxlength="2">
                  </div>
                  <div class="col-4">
                    <label class="form-label small mb-0">年份 (YY)</label>
                    <input type="text" id="cfgCardExpYear" class="form-control form-control-sm" placeholder="28" maxlength="4">
                  </div>
                  <div class="col-4">
                    <label class="form-label small mb-0">安全碼(CVV)</label>
                    <input type="password" id="cfgCardCvv" class="form-control form-control-sm" placeholder="123" maxlength="4" autocomplete="off">
                  </div>
                </div>
                <div class="mb-1">
                  <label class="form-label small mb-0">持卡人姓名 (英文拼音/中文)</label>
                  <input type="text" id="cfgCardHolder" class="form-control form-control-sm" placeholder="例: WANG XIAO MING">
                </div>
                <small class="text-muted d-block" style="font-size: 0.72rem;">🔒 僅保存在您本機設定檔並受 .gitignore 保護，絕不推送到雲端或外流。</small>
              </div>

              <div class="form-check form-switch mb-3">
                <input class="form-check-input" type="checkbox" id="cfgAutoSubmitPayment">
                <label class="form-check-label small fw-bold text-danger" for="cfgAutoSubmitPayment">⚡ 自動確認最終扣款下單 (高敏感)</label>
                <small class="text-muted d-block" style="font-size: 0.75rem;">關閉時將停在最後授權頁等您確認；開啟則直接代您按下完成購買。</small>
              </div>

              <!-- 電商會員登入帳密 (小白友善：免抓 Cookie，輸入帳密自動登入搶購) -->
              <div class="p-3 mb-3 bg-light rounded-3 border">
                <div class="d-flex justify-content-between align-items-center mb-2">
                  <span class="small fw-bold text-dark"><i class="bi bi-person-badge text-primary me-1"></i>電商會員帳號綁定 (自動代登入)</span>
                  <span class="badge bg-success" id="accountStatusBadge">已就緒</span>
                </div>
                <div class="mb-2">
                  <label class="form-label small mb-0">會員登入 Email / 帳號</label>
                  <input type="email" id="cfgAccountEmail" class="form-control form-control-sm" placeholder="example@email.com" autocomplete="off">
                </div>
                <div class="mb-2">
                  <label class="form-label small mb-0">會員密碼 (點擊儲存自動加密存於本地)</label>
                  <input type="password" id="cfgAccountPassword" class="form-control form-control-sm" placeholder="••••••••" autocomplete="off">
                </div>
                <div class="d-flex justify-content-between align-items-center mt-2">
                  <small class="text-muted" style="font-size: 0.72rem;">✨ 免手動抓 Cookie！搶購引擎會自動代您登入加車。</small>
                  <button type="button" class="btn btn-sm btn-outline-primary" style="font-size: 0.75rem;" onclick="testAccountLoginUI()">
                    🔑 測試登入
                  </button>
                </div>
              </div>

              <!-- 進階 Cookie 選填區 (預設折疊) -->
              <div class="mb-3">
                <a class="small text-muted text-decoration-none" data-bs-toggle="collapse" href="#advancedCookieCollapse" role="button">
                  <i class="bi bi-chevron-down me-1"></i>進階工程師選項：手動貼 Cookie JSON (選填)
                </a>
                <div class="collapse mt-2" id="advancedCookieCollapse">
                  <textarea id="cfgCookiesJson" rows="2" class="form-control form-control-sm" placeholder='若有現成 Cookie JSON 可選填貼於此處'></textarea>
                  <div class="small text-success mt-1" id="cookieStatusText"></div>
                </div>
              </div>

              <div class="d-flex gap-2 mb-3">
                <button class="btn btn-warning flex-grow-1 fw-bold py-2" onclick="saveAutobuyConfig()">
                  <i class="bi bi-save me-1"></i>儲存搶購與帳號設定
                </button>
              </div>

              <!-- 測試試跑按鈕與 AI 除錯報告導出 -->
              <div class="border-top pt-3">
                <div class="d-flex gap-2">
                  <button class="btn btn-outline-primary btn-sm flex-grow-1 fw-bold" onclick="openTestModal()">
                    <i class="bi bi-play-circle me-1"></i>🧪 試跑加車與結帳
                  </button>
                  <button class="btn btn-outline-secondary btn-sm fw-bold" onclick="copyAiDebugReport()">
                    <i class="bi bi-robot me-1"></i>📋 複製 AI 除錯報告
                  </button>
                </div>
              </div>
            </div>
          </div>

          <!-- 一般偏好設定 -->
          <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-sliders text-primary me-2"></i>巡檢與通知偏好設定</h5>
              <div class="mb-3">
                <label class="form-label fw-bold">🔄 背景掃描頻率 (爬蟲週期)</label>
                <select id="cfgPollInterval" class="form-select">
                  <option value="1">每 1 分鐘</option>
                  <option value="3">每 3 分鐘</option>
                  <option value="5" selected>每 5 分鐘 (建議)</option>
                  <option value="10">每 10 分鐘</option>
                </select>
              </div>
              <div class="mb-3">
                <label class="form-label fw-bold">🔔 持續缺貨通知頻率</label>
                <select id="cfgOosHours" class="form-select">
                  <option value="1">每 1 小時</option>
                  <option value="2">每 2 小時</option>
                  <option value="4" selected>每 4 小時 (建議)</option>
                  <option value="8">每 8 小時</option>
                  <option value="12">每 12 小時</option>
                  <option value="24">每 24 小時</option>
                  <option value="0">🔕 僅補貨時通知 (關閉定期缺貨回報)</option>
                </select>
              </div>
              <hr class="my-3">
              <div class="form-check form-switch mb-2">
                <input class="form-check-input" type="checkbox" id="cfgLocalNotif" checked>
                <label class="form-check-label fw-bold" for="cfgLocalNotif">啟用本機桌面推播通知</label>
              </div>
              <div class="form-check form-switch mb-3">
                <input class="form-check-input" type="checkbox" id="cfgTelegram" checked>
                <label class="form-check-label fw-bold" for="cfgTelegram">啟用 Telegram 提醒通知</label>
              </div>
              <button class="btn btn-success w-100 fw-bold py-2" onclick="saveConfig()">
                <i class="bi bi-check-lg me-1"></i>儲存設定
              </button>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ======================= 分頁 2: 預約搶登入 / 定時任務 ======================= -->
    <div class="tab-pane fade" id="pane-schedule" role="tabpanel">
      <div class="row g-4">
        <div class="col-lg-5">
          <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-calendar-check text-primary me-2"></i>新增預約執行任務</h5>
              <p class="small text-muted">只要設定目標時間（例如搶登入開放瞬間），系統會在時間一到自動載入並為您執行！</p>

              <div class="mb-3">
                <label class="form-label fw-bold">任務名稱</label>
                <input type="text" id="schTitle" class="form-control" placeholder="例: 搶好市多特價品 / 搶預約名額">
              </div>

              <div class="mb-3">
                <label class="form-label fw-bold">目標網址 (登入或活動頁面)</label>
                <input type="url" id="schUrl" class="form-control" placeholder="https://...">
              </div>

              <div class="mb-3">
                <label class="form-label fw-bold">預約執行時間</label>
                <div class="input-group mb-2">
                  <input type="datetime-local" id="schTimeInput" class="form-control">
                  <button class="btn btn-outline-secondary" type="button" onclick="setScheduleImmediate()">立即執行</button>
                </div>
                <small class="text-muted">支援指定特定年月日時分秒，或選擇立即執行測試。</small>
              </div>

              <button class="btn btn-primary w-100 fw-bold py-2" onclick="createScheduledTask()">
                <i class="bi bi-clock me-1"></i>建立預約任務
              </button>
            </div>
          </div>
        </div>

        <div class="col-lg-7">
          <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-list-check text-primary me-2"></i>預約任務列表</h5>
              <div id="scheduledTaskList" class="d-flex flex-column gap-2">
                <div class="text-center py-4 text-muted">目前尚無預約任務。</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- ======================= 分頁 3: 萬用網頁爬蟲精靈 (含小白 F12 指南) ======================= -->
    <div class="tab-pane fade" id="pane-custom" role="tabpanel">
      <div class="row g-4">
        <!-- 左側：F12 視覺化新手教學 -->
        <div class="col-lg-6">
          <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold text-primary mb-3">
                <i class="bi bi-lightbulb-fill text-warning me-2"></i>新手小白 F12 抓取教學：如何告訴程式要看哪裡？
              </h5>
              <p class="text-muted small">不用懂寫程式！只要在你想監控的網頁上跟著這 4 個動作，就能讓程式知道該抓哪個欄位：</p>

              <div class="f12-box mb-3">
                <strong class="text-primary">步驟 1：打開瀏覽器開發者工具 (F12)</strong>
                <p class="small text-muted mb-0">在任何網頁按下鍵盤的 <kbd>F12</kbd>（或在畫面上點滑鼠右鍵 -> 點「檢查」）。</p>
              </div>

              <div class="f12-box mb-3">
                <strong class="text-primary">步驟 2：點擊左上角的「小箭頭」選擇工具</strong>
                <p class="small text-muted mb-0">在彈出的視窗左上角，有一個箭頭游標圖示（或按快捷鍵 <kbd>Ctrl+Shift+C</kbd>）。點擊它，游標會變成選取模式。</p>
              </div>

              <div class="f12-box mb-3">
                <strong class="text-primary">步驟 3：用滑鼠點擊網頁上你想監控的欄位</strong>
                <p class="small text-muted mb-0">例如點擊「剩餘庫存數量」或「加入購物車」按鈕，右邊的程式碼會自動跳到對應的那一行！</p>
              </div>

              <div class="f12-box mb-3">
                <strong class="text-primary">步驟 4：複製選擇器 (Copy Selector)</strong>
                <p class="small text-muted mb-0">在反藍的那行程式碼點滑鼠右鍵 -> 選擇 <strong>Copy (複製)</strong> -> 點 <strong>Copy selector (複製選擇器)</strong>，然後貼到右邊的輸入框即可！</p>
              </div>

              <div class="alert alert-info py-2 small mb-0">
                <i class="bi bi-info-circle me-1"></i><strong>提示</strong>：若您連選擇器都懶得抓，也可以只輸入想尋找的<strong>關鍵字</strong>（例如：<code>有現貨</code> 或 <code>已額滿</code>），程式會自動全網頁搜尋！
              </div>
            </div>
          </div>
        </div>

        <!-- 右側：自訂規則建立與即時測試 -->
        <div class="col-lg-6">
          <div class="card shadow-sm border-0 rounded-3 mb-4">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-gear-wide-connected text-primary me-2"></i>建立與測試自訂爬蟲規則</h5>

              <div class="mb-3">
                <label class="form-label fw-bold">規則名稱</label>
                <input type="text" id="custRuleName" class="form-control" placeholder="例: 監控羽球場地預約 / 特賣會狀態">
              </div>

              <div class="mb-3">
                <label class="form-label fw-bold">目標網頁網址</label>
                <input type="url" id="custRuleUrl" class="form-control" placeholder="https://...">
              </div>

              <div class="mb-3">
                <label class="form-label fw-bold">CSS 選擇器 (貼上步驟 4 複製的內容，選填)</label>
                <input type="text" id="custRuleSelector" class="form-control" placeholder="例: #stock-count 或 .btn-buy">
              </div>

              <div class="mb-3">
                <label class="form-label fw-bold">欲監控的文字或關鍵字 (選填)</label>
                <input type="text" id="custRuleMatchText" class="form-control" placeholder="例: 可預約 / 有名額 / 補貨中">
              </div>

              <div class="d-flex gap-2">
                <button class="btn btn-outline-primary w-50 fw-bold" onclick="testCustomCrawl()">
                  <span id="testBtnSpinner" class="spinner-border spinner-border-sm d-none me-1"></span>🔍 即時測試抓取
                </button>
                <button class="btn btn-primary w-50 fw-bold" onclick="saveCustomRule()">
                  <i class="bi bi-plus-lg me-1"></i>儲存為監控規則
                </button>
              </div>

              <!-- 測試結果呈現區塊 -->
              <div id="testResultBox" class="mt-3 p-3 bg-light rounded d-none">
                <h6 class="fw-bold mb-2">測試抓取結果預覽：</h6>
                <div id="testResultContent" class="small text-muted"></div>
              </div>
            </div>
          </div>

          <!-- 已建立的自訂規則清單 -->
          <div class="card shadow-sm border-0 rounded-3">
            <div class="card-body p-4">
              <h5 class="fw-bold mb-3"><i class="bi bi-card-checklist text-primary me-2"></i>已建立的自訂監控規則</h5>
              <div id="customRuleList" class="d-flex flex-column gap-2">
                <div class="text-center py-3 text-muted">尚無自訂規則。</div>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>

  </div>
</div>

<!-- ======================= 測試試跑視窗 (Modal) ======================= -->
<div class="modal fade" id="modalAutobuyTest" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content rounded-4 border-0 shadow">
      <div class="modal-header bg-primary text-white">
        <h5 class="modal-title fw-bold"><i class="bi bi-play-circle-fill me-2"></i>🧪 自動加車與結帳流水線沙盒試跑</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body p-4">
        <p class="text-muted small">
          此工具將為您模擬搶購引擎的每一個動作（Stage 1 載入 ➔ Stage 2 點擊加車 ➔ Stage 3 推進結帳 ➔ Stage 3.5 填入付款方式）。
          <strong>預設為安全試跑模式，不會進行最後實際扣款！</strong>
        </p>
        <div class="mb-3">
          <label class="form-label fw-bold">測試目標商品網址：</label>
          <input type="url" id="testTargetUrl" class="form-control" placeholder="https://www.costco.com.tw/.../p/123456">
        </div>
        <div class="d-flex gap-2 mb-3">
          <button class="btn btn-primary fw-bold px-4" id="btnRunTest" onclick="startAutobuyTest()">
            <span id="testSpinner" class="spinner-border spinner-border-sm d-none me-1"></span>開始試跑測試
          </button>
          <button class="btn btn-outline-secondary" onclick="useFirstProductForTest()">
            帶入清單第 1 件商品
          </button>
        </div>

        <!-- 試跑日誌即時輸出面板 -->
        <div class="card bg-dark text-light border-0 rounded-3 mb-3">
          <div class="card-header bg-secondary bg-opacity-25 d-flex justify-content-between align-items-center py-2">
            <span class="small fw-bold"><i class="bi bi-terminal-fill me-1 text-info"></i>執行日誌視窗 (Execution Logs)</span>
            <span class="badge bg-primary" id="testStageBadge">等待開始</span>
          </div>
          <div class="card-body p-3" style="max-height: 280px; overflow-y: auto; font-family: monospace; font-size: 0.85rem;" id="testLogContainer">
            <div class="text-muted">點擊「開始試跑測試」以觀察完整流水線執行...</div>
          </div>
        </div>

        <div id="testResultAlert" class="alert d-none py-2 mb-0"></div>
      </div>
      <div class="modal-footer bg-light">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">關閉</button>
        <button type="button" class="btn btn-outline-success fw-bold" onclick="copyAiDebugReport()">
          <i class="bi bi-robot me-1"></i>📋 複製 AI 除錯報告
        </button>
      </div>
    </div>
  </div>
</div>

<!-- ======================= AI 除錯診斷報告視窗 (Modal) ======================= -->
<div class="modal fade" id="modalAiDebug" tabindex="-1">
  <div class="modal-dialog modal-lg">
    <div class="modal-content rounded-4 border-0 shadow">
      <div class="modal-header bg-dark text-white">
        <h5 class="modal-title fw-bold"><i class="bi bi-robot me-2 text-warning"></i>📋 AI 友善除錯診斷報告</h5>
        <button type="button" class="btn-close btn-close-white" data-bs-dismiss="modal"></button>
      </div>
      <div class="modal-body p-4">
        <p class="text-muted small">
          遇上加車失敗、找不到按鈕、或登入過期時，點擊下方「複製報告」並直接貼給任何 AI (如 ChatGPT, Claude, Gemini)，AI 能立即針對日誌與 DOM 結構為您快速排除問題！
        </p>
        <div class="mb-3">
          <textarea id="aiDebugReportText" class="form-control font-monospace small" rows="12" readonly></textarea>
        </div>
      </div>
      <div class="modal-footer bg-light">
        <button type="button" class="btn btn-secondary" data-bs-dismiss="modal">關閉</button>
        <button type="button" class="btn btn-primary fw-bold" onclick="doCopyAiText()">
          <i class="bi bi-clipboard-check me-1"></i>點擊一鍵複製
        </button>
      </div>
    </div>
  </div>
</div>

<script src="https://cdn.jsdelivr.net/npm/bootstrap@5.3.3/dist/js/bootstrap.bundle.min.js"></script>
<script>
let lastNotificationId = null;

// 桌面通知權限
async function requestNotificationPermission() {
  if (!("Notification" in window)) {
    alert("此瀏覽器不支援桌面推播通知功能。");
    return;
  }
  const perm = await Notification.requestPermission();
  updateNotifButton(perm);
}

function updateNotifButton(perm) {
  const btn = document.getElementById("notifBtnText");
  if (perm === "granted") {
    btn.innerText = "本機推播通知：已啟用 ✅";
  } else if (perm === "denied") {
    btn.innerText = "本機推播通知：已被拒絕 ❌";
  } else {
    btn.innerText = "啟用本機推播通知";
  }
}

// 載入偏好設定
async function loadConfig() {
  try {
    const res = await fetch("/api/config");
    const cfg = await res.json();
    document.getElementById("cfgPollInterval").value = cfg.poll_interval_minutes || 5;
    document.getElementById("cfgOosHours").value = cfg.oos_notify_hours !== undefined ? cfg.oos_notify_hours : 4;
    document.getElementById("cfgLocalNotif").checked = cfg.enable_local_notification !== false;
    document.getElementById("cfgTelegram").checked = cfg.enable_telegram !== false;
  } catch (e) {
    console.error("載入設定失敗", e);
  }
}

// 切換自訂信用卡欄位展開/收合
function togglePaymentInputs() {
  const method = document.getElementById("cfgPaymentMethod").value;
  const section = document.getElementById("creditCardSection");
  section.style.display = (method === "custom_card") ? "block" : "none";
}

// 載入搶購與付款設定
async function loadAutobuyConfig() {
  try {
    const res = await fetch("/api/autobuy");
    const data = await res.json();
    const cfg = data.config || {};
    const card = data.card_info || {};

    document.getElementById("cfgAutobuyEnabled").checked = !!cfg.enabled;
    document.getElementById("cfgAutoCheckout").checked = !!cfg.auto_checkout;
    document.getElementById("cfgAutoSubmitPayment").checked = !!cfg.auto_submit_payment;
    
    if (cfg.payment_method) {
      document.getElementById("cfgPaymentMethod").value = cfg.payment_method;
    }
    togglePaymentInputs();

    // 填入帳號登入資訊
    const accEmail = (cfg.account_email || "").trim();
    document.getElementById("cfgAccountEmail").value = accEmail;
    const accBadge = document.getElementById("accountStatusBadge");
    if (accEmail) {
      accBadge.innerText = "已綁定 ✅";
      accBadge.className = "badge bg-success";
      document.getElementById("cfgAccountPassword").placeholder = "•••••••• (已保存，若不變更請留空)";
    } else {
      accBadge.innerText = "未綁定";
      accBadge.className = "badge bg-secondary";
      document.getElementById("cfgAccountPassword").placeholder = "••••••••";
    }

    // 填入信用卡遮罩資訊
    const cardBadge = document.getElementById("cardStatusBadge");
    if (card.has_card_number) {
      document.getElementById("cfgCardNumber").value = card.masked_card_number || "";
      document.getElementById("cfgCardExpMonth").value = card.card_exp_month || "";
      document.getElementById("cfgCardExpYear").value = card.card_exp_year || "";
      document.getElementById("cfgCardHolder").value = card.card_holder_name || "";
      if (card.has_cvv) {
        document.getElementById("cfgCardCvv").value = "***";
      }
      cardBadge.innerText = "已設定 ✅";
      cardBadge.className = "badge bg-success";
    } else {
      cardBadge.innerText = "未填寫";
      cardBadge.className = "badge bg-secondary";
    }

    const cookieText = document.getElementById("cookieStatusText");
    if (data.has_cookies) {
      cookieText.innerText = "✅ 本地已載入 " + data.cookie_count + " 個 Cookie 憑證！";
    } else {
      cookieText.innerText = "ℹ️ 尚未匯入 Cookie，若補貨將使用帳號密碼自動登入加車。";
    }
  } catch (e) {
    console.error("載入搶購設定失敗", e);
  }
}

async function saveAutobuyConfig() {
  const payload = {
    enabled: document.getElementById("cfgAutobuyEnabled").checked,
    auto_checkout: document.getElementById("cfgAutoCheckout").checked,
    auto_submit_payment: document.getElementById("cfgAutoSubmitPayment").checked,
    payment_method: document.getElementById("cfgPaymentMethod").value,
    account_email: document.getElementById("cfgAccountEmail").value.trim(),
    account_password: document.getElementById("cfgAccountPassword").value.trim(),
    card_number: document.getElementById("cfgCardNumber").value.trim(),
    card_exp_month: document.getElementById("cfgCardExpMonth").value.trim(),
    card_exp_year: document.getElementById("cfgCardExpYear").value.trim(),
    card_cvv: document.getElementById("cfgCardCvv").value.trim(),
    card_holder_name: document.getElementById("cfgCardHolder").value.trim(),
    cookies_json: document.getElementById("cfgCookiesJson").value.trim()
  };
  try {
    const res = await fetch("/api/autobuy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      alert("✅ 電商會員帳密、付款與搶購模組設定已成功保存！");
      document.getElementById("cfgCookiesJson").value = "";
      document.getElementById("cfgAccountPassword").value = "";
      loadAutobuyConfig();
    } else {
      const err = await res.json();
      alert("❌ 儲存失敗: " + (err.message || "未知錯誤"));
    }
  } catch (e) {
    alert("❌ 儲存失敗: " + e);
  }
}

async function testAccountLoginUI() {
  const email = document.getElementById("cfgAccountEmail").value.trim();
  if (!email) {
    alert("請先輸入您的電商會員 Email / 帳號！");
    return;
  }
  alert("🔑 已啟動擬真瀏覽器自動登入測試，請於彈出的試跑視窗中觀察登入日誌！");
  openTestModal();
  startAutobuyTest();
}

// ======================== 測試試跑與 AI 除錯 ========================
function openTestModal() {
  const modal = new bootstrap.Modal(document.getElementById("modalAutobuyTest"));
  modal.show();
}

function useFirstProductForTest() {
  const firstItem = document.querySelector("#productList a[href]");
  if (firstItem && firstItem.href) {
    document.getElementById("testTargetUrl").value = firstItem.href;
  } else {
    alert("目前清單中尚無商品，請在輸入框貼上商品網址。");
  }
}

async function startAutobuyTest() {
  const url = document.getElementById("testTargetUrl").value.trim();
  if (!url) {
    alert("請輸入欲測試之商品網址！");
    return;
  }
  const btn = document.getElementById("btnRunTest");
  const spinner = document.getElementById("testSpinner");
  const logBox = document.getElementById("testLogContainer");
  const badge = document.getElementById("testStageBadge");
  const alertBox = document.getElementById("testResultAlert");

  btn.disabled = true;
  spinner.classList.remove("d-none");
  badge.className = "badge bg-warning text-dark";
  badge.innerText = "測試進行中...";
  logBox.innerHTML = '<div class="text-info">⏳ 正在啟動沙盒測試引擎 (載入 Cookie / 瀏覽器模擬)...</div>';
  alertBox.className = "alert d-none";

  try {
    const res = await fetch("/api/autobuy/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_url: url, product_name: "沙盒測試商品" })
    });
    const data = await res.json();
    if (res.ok && data.result) {
      const r = data.result;
      const diag = r.diagnostic || {};
      const logs = diag.logs || [];

      // 渲染即時日誌
      let logHtml = "";
      logs.forEach(l => {
        let colorClass = "text-light";
        if (l.status === "SUCCESS" || l.status === "APPLIED") colorClass = "text-success";
        else if (l.status === "START" || l.status === "CLICK") colorClass = "text-info";
        else if (l.status === "FAILED" || l.status === "ERROR") colorClass = "text-danger";
        else if (l.status === "WARNING" || l.status === "PROTECTED") colorClass = "text-warning";

        logHtml += `<div><span class="text-muted">[${l.time}]</span> <span class="fw-bold">[${l.step}]</span> <span class="${colorClass}">${l.status}</span>: ${l.detail}</div>`;
      });
      logBox.innerHTML = logHtml || '<div class="text-muted">測試完畢，無詳細日誌。</div>';

      badge.innerText = "Stage " + (r.stage_reached || 0) + " 完成";
      badge.className = r.stage_reached >= 3 ? "badge bg-success" : "badge bg-warning text-dark";

      alertBox.className = r.stage_reached >= 3 ? "alert alert-success py-2 mb-0" : "alert alert-warning py-2 mb-0";
      alertBox.innerHTML = `<strong>測試成果：</strong> ${r.status_title || ''} <br><small>${r.status_message || ''}</small>`;
      alertBox.classList.remove("d-none");
    } else {
      logBox.innerHTML += `<div class="text-danger mt-2">❌ 測試失敗: ${(data.message || '未知錯誤')}</div>`;
      badge.innerText = "測試失敗";
      badge.className = "badge bg-danger";
    }
  } catch (e) {
    logBox.innerHTML += `<div class="text-danger mt-2">❌ 請求異常: ${e}</div>`;
    badge.innerText = "請求異常";
    badge.className = "badge bg-danger";
  } finally {
    btn.disabled = false;
    spinner.classList.add("d-none");
  }
}

async function copyAiDebugReport() {
  try {
    const res = await fetch("/api/autobuy/debug_report");
    const data = await res.json();
    if (data.report_markdown) {
      document.getElementById("aiDebugReportText").value = data.report_markdown;
      const modal = new bootstrap.Modal(document.getElementById("modalAiDebug"));
      modal.show();
    }
  } catch (e) {
    alert("讀取除錯報告失敗: " + e);
  }
}

function doCopyAiText() {
  const textarea = document.getElementById("aiDebugReportText");
  textarea.select();
  navigator.clipboard.writeText(textarea.value).then(() => {
    alert("✅ AI 除錯報告已複製到剪貼簿！您可以直接貼給 AI 進行分析！");
  }).catch(e => {
    alert("複製失敗，請手動複製文字框內容。");
  });
}

async function saveConfig() {
  const payload = {
    poll_interval_minutes: parseInt(document.getElementById("cfgPollInterval").value),
    oos_notify_hours: parseInt(document.getElementById("cfgOosHours").value),
    enable_local_notification: document.getElementById("cfgLocalNotif").checked,
    enable_telegram: document.getElementById("cfgTelegram").checked
  };
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      alert("✅ 偏好設定已成功更新！");
    }
  } catch (e) {
    alert("❌ 儲存失敗：" + e);
  }
}

// 載入商品清單
async function fetchAll(isRefreshAll = false) {
  const container = document.getElementById("productList");
  if (isRefreshAll) {
    container.innerHTML = '<div class="text-center py-5"><div class="spinner-border text-primary me-2"></div>正在全面重新爬蟲最新庫存，請稍候...</div>';
  }
  try {
    const url = isRefreshAll ? "/api/items/refresh_all" : "/api/items";
    const method = isRefreshAll ? "POST" : "GET";
    const res = await fetch(url, { method });
    const data = await res.json();
    renderProducts(data.items || []);
  } catch (e) {
    container.innerHTML = '<div class="alert alert-danger">載入失敗: ' + e + '</div>';
  }
}

function renderProducts(items) {
  const container = document.getElementById("productList");
  document.getElementById("itemCount").innerText = items.length;
  document.getElementById("lastUpdatedText").innerText = "最後更新：" + new Date().toLocaleTimeString();

  if (items.length === 0) {
    container.innerHTML = '<div class="card p-5 text-center text-muted border-0 shadow-sm rounded-3">📭 目前尚無追蹤商品。請在上方的輸入框貼入電商網址開始監控！</div>';
    return;
  }

  let html = "";
  items.forEach(it => {
    const isStock = it.in_stock;
    const badge = isStock 
      ? '<span class="badge badge-in-stock px-3 py-2"><i class="bi bi-check-circle-fill me-1"></i>有現貨</span>'
      : '<span class="badge badge-out-of-stock px-3 py-2"><i class="bi bi-x-circle-fill me-1"></i>缺貨中</span>';

    const optBadge = it.preferred_option ? `<span class="badge bg-info text-dark ms-2"><i class="bi bi-tag-fill me-1"></i>規格: ${it.preferred_option}</span>` : '';
    const priceBadge = it.current_price ? `<span class="badge bg-warning text-dark ms-2 fs-6"><i class="bi bi-currency-dollar me-0"></i>${it.current_price}</span>` : '';

    html += `
      <div class="card product-card p-3">
        <div class="card-body p-2">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <div>
              <h6 class="fw-bold mb-1 text-truncate d-inline-block" style="max-width: 550px;">${it.name || "未命名商品"} ${optBadge}</h6>
              ${priceBadge}
            </div>
            ${badge}
          </div>
          <div class="small text-muted mb-2"><i class="bi bi-info-circle me-1"></i>狀態詳情：${it.status_notes || "無"}</div>
          <div class="small text-muted mb-3"><i class="bi bi-clock-history me-1"></i>最後檢查：${it.last_checked || "未記錄"}</div>
          <div class="d-flex justify-content-between align-items-center">
            <div class="d-flex gap-2">
              <a href="${it.target_url}" target="_blank" class="btn btn-outline-primary btn-sm">
                <i class="bi bi-box-arrow-up-right me-1"></i>開啟賣場
              </a>
              <button class="btn btn-outline-secondary btn-sm" onclick="refreshSingle('${it.id}')">
                <i class="bi bi-arrow-repeat me-1"></i>立即重新檢查
              </button>
              ${it.price_history && it.price_history.length > 1 ? `<button class="btn btn-outline-info btn-sm text-dark" onclick="showPriceHistory('${encodeURIComponent(JSON.stringify(it))}')"><i class="bi bi-graph-up me-1"></i>價格趨勢</button>` : ''}
            </div>
            <button class="btn btn-outline-danger btn-sm" onclick="deleteProduct('${it.id}')">
              <i class="bi bi-trash3 me-1"></i>移除
            </button>
          </div>
        </div>
      </div>
    `;
  });
  container.innerHTML = html;
}

async function addProduct() {
  const input = document.getElementById("newUrlInput");
  const optInput = document.getElementById("newPreferredOptionInput");
  const url = input.value.trim();
  const preferred_option = optInput ? optInput.value.trim() : "";
  if (!url) {
    alert("請貼上商品網址！");
    return;
  }
  const btn = document.getElementById("addBtn");
  const spinner = document.getElementById("addBtnSpinner");
  btn.disabled = true;
  spinner.classList.remove("d-none");

  try {
    const res = await fetch("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, preferred_option })
    });
    const data = await res.json();
    if (res.ok) {
      input.value = "";
      if (optInput) optInput.value = "";
      fetchAll();
    } else {
      alert("❌ " + (data.message || "新增失敗"));
    }
  } catch (e) {
    alert("❌ 錯誤: " + e);
  } finally {
    btn.disabled = false;
    spinner.classList.add("d-none");
  }
}

// 匯出完整設定與規則備份 (Backup Export)
function exportBackup() {
  window.open("/api/backup/export", "_blank");
}

// 還原備份檔 (Backup Restore)
async function handleRestoreBackup(event) {
  const file = event.target.files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = async (e) => {
    try {
      const content = e.target.result;
      const res = await fetch("/api/backup/restore", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ backup_json: content })
      });
      const data = await res.json();
      if (res.ok) {
        alert("🎉 " + data.message);
        fetchAll();
        loadConfig();
        loadAutobuyConfig();
      } else {
        alert("❌ 還原失敗：" + data.message);
      }
    } catch (err) {
      alert("❌ 解析備份檔案異常：" + err);
    }
  };
  reader.readAsText(file);
  event.target.value = "";
}

// 檢驗 Cookie 健康狀態
async function checkCookieHealthUI() {
  const badge = document.getElementById("cookieHealthBadge");
  badge.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>檢測中...';
  try {
    const res = await fetch("/api/cookie/health");
    const data = await res.json();
    const h = data.health || {};
    if (h.is_valid) {
      badge.className = "badge bg-success cursor-pointer";
      badge.innerText = `🍪 Cookie 有效 (${h.cookie_count}筆)`;
      alert(`✅ 電商登入憑證有效中！\n狀態：${h.message}\n有效 Cookie 數量：${h.cookie_count} 筆`);
    } else {
      badge.className = "badge bg-danger cursor-pointer";
      badge.innerText = `🍪 Cookie 失效`;
      alert(`⚠️ 憑證警報：\n${h.message}`);
    }
  } catch (err) {
    badge.className = "badge bg-warning text-dark cursor-pointer";
    badge.innerText = `🍪 連線異常`;
  }
}

async function deleteProduct(id) {
  if (!confirm("確定要取消追蹤並刪除此商品嗎？")) return;
  try {
    await fetch("/api/items/" + id, { method: "DELETE" });
    fetchAll();
  } catch (e) {
    alert("刪除失敗: " + e);
  }
}

async function refreshSingle(id) {
  try {
    await fetch("/api/items/" + id + "/refresh", { method: "POST" });
    fetchAll();
  } catch (e) {
    alert("檢查失敗: " + e);
  }
}

function showPriceHistory(itemJsonEncoded) {
  try {
    const item = JSON.parse(decodeURIComponent(itemJsonEncoded));
    const history = item.price_history || [];
    if (history.length === 0) {
      alert("此商品目前尚未記錄到價格波動。");
      return;
    }
    let msg = `📈【${item.name}】歷史價格變動趨勢：\n\n`;
    history.forEach(h => {
      const stock = h.in_stock ? "🟢 有現貨" : "🔴 缺貨";
      msg += `🕒 ${h.time} ➔ 價格: ${h.price || "未標示"} (${stock})\n`;
    });
    alert(msg);
  } catch (err) {
    alert("解析價格歷史失敗: " + err);
  }
}

// ======================== 預約任務管理 ========================
function setScheduleImmediate() {
  document.getElementById("schTimeInput").value = "";
}

async function loadScheduledTasks() {
  try {
    const res = await fetch("/api/scheduled_tasks");
    const data = await res.json();
    const list = data.tasks || [];
    const container = document.getElementById("scheduledTaskList");
    if (list.length === 0) {
      container.innerHTML = '<div class="text-center py-4 text-muted">目前尚無預約任務。</div>';
      return;
    }
    let html = "";
    list.forEach(t => {
      const isPending = t.status === "PENDING";
      const statusBadge = isPending ? '<span class="badge bg-warning text-dark">等待觸發</span>' : '<span class="badge bg-success">已執行</span>';
      html += `
        <div class="card p-3 border-0 shadow-sm rounded-3">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="fw-bold mb-1">${t.title} ${statusBadge}</h6>
              <div class="small text-muted">目標：<a href="${t.target_url}" target="_blank">${t.target_url}</a></div>
              <div class="small text-muted">預約時間：<code>${t.execute_at || "立即執行"}</code> | 建立於：${t.created_at}</div>
              ${t.result_message ? `<div class="small text-primary mt-1">結果：${t.result_message}</div>` : ''}
            </div>
            <button class="btn btn-outline-danger btn-sm" onclick="deleteScheduledTask('${t.id}')">
              <i class="bi bi-trash3"></i>
            </button>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
  } catch (e) {
    console.error("載入預約任務失敗", e);
  }
}

async function createScheduledTask() {
  const title = document.getElementById("schTitle").value.trim();
  const url = document.getElementById("schUrl").value.trim();
  const timeVal = document.getElementById("schTimeInput").value;
  if (!url) {
    alert("請輸入目標網址！");
    return;
  }
  let execAt = "IMMEDIATE";
  if (timeVal) {
    // 轉換成 "YYYY-MM-DD HH:MM:SS"
    execAt = timeVal.replace("T", " ") + ":00";
  }
  try {
    const res = await fetch("/api/scheduled_tasks", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ title, target_url: url, execute_at: execAt })
    });
    if (res.ok) {
      alert("✅ 預約任務已成功建立！");
      document.getElementById("schTitle").value = "";
      document.getElementById("schUrl").value = "";
      loadScheduledTasks();
    }
  } catch (e) {
    alert("❌ 建立失敗: " + e);
  }
}

async function deleteScheduledTask(id) {
  try {
    await fetch("/api/scheduled_tasks/" + id, { method: "DELETE" });
    loadScheduledTasks();
  } catch (e) {
    alert("刪除失敗: " + e);
  }
}

// ======================== 自訂爬蟲與測試 ========================
async function testCustomCrawl() {
  const url = document.getElementById("custRuleUrl").value.trim();
  const selector = document.getElementById("custRuleSelector").value.trim();
  const match_text = document.getElementById("custRuleMatchText").value.trim();
  if (!url) {
    alert("請填入欲測試的目標網頁網址！");
    return;
  }
  const spinner = document.getElementById("testBtnSpinner");
  spinner.classList.remove("d-none");
  const resultBox = document.getElementById("testResultBox");
  const resultContent = document.getElementById("testResultContent");
  resultBox.classList.remove("d-none");
  resultContent.innerHTML = '<div class="spinner-border spinner-border-sm me-1"></div>正在抓取與解析網頁中...';

  try {
    const res = await fetch("/api/custom_crawler/test", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ url, selector, match_text })
    });
    const data = await res.json();
    if (!data.success) {
      resultContent.innerHTML = `<span class="text-danger fw-bold">❌ 抓取失敗：${data.error}</span>`;
      return;
    }
    let html = `<div><strong>網頁標題</strong>：${data.page_title}</div>`;
    if (data.match_text_found !== null) {
      html += `<div><strong>關鍵字 [${match_text}] 檢查</strong>：${data.match_text_found ? '<span class="text-success fw-bold">✅ 成功找到！</span>' : '<span class="text-danger fw-bold">❌ 未在此頁發現</span>'}</div>`;
    }
    html += `<div><strong>找到的元素數量</strong>：${data.found_count} 個</div>`;
    if (data.elements && data.elements.length > 0) {
      html += '<ul class="mt-2 mb-0 ps-3">';
      data.elements.forEach(el => {
        html += `<li><strong>&lt;${el.tag}&gt;</strong>: <code>${el.text || '(空文字)'}</code></li>`;
      });
      html += '</ul>';
    }
    resultContent.innerHTML = html;
  } catch (e) {
    resultContent.innerHTML = `<span class="text-danger">❌ 測試異常: ${e}</span>`;
  } finally {
    spinner.classList.add("d-none");
  }
}

async function saveCustomRule() {
  const name = document.getElementById("custRuleName").value.trim();
  const target_url = document.getElementById("custRuleUrl").value.trim();
  const selector = document.getElementById("custRuleSelector").value.trim();
  const match_text = document.getElementById("custRuleMatchText").value.trim();
  if (!target_url) {
    alert("請輸入目標網址！");
    return;
  }
  try {
    const res = await fetch("/api/custom_crawler/rules", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, target_url, selector, match_text })
    });
    if (res.ok) {
      alert("✅ 自訂爬蟲規則已成功保存！");
      loadCustomRules();
    }
  } catch (e) {
    alert("❌ 儲存失敗: " + e);
  }
}

async function loadCustomRules() {
  try {
    const res = await fetch("/api/custom_crawler/rules");
    const data = await res.json();
    const list = data.rules || [];
    const container = document.getElementById("customRuleList");
    if (list.length === 0) {
      container.innerHTML = '<div class="text-center py-3 text-muted">尚無自訂規則。</div>';
      return;
    }
    let html = "";
    list.forEach(r => {
      html += `
        <div class="card p-3 border-0 shadow-sm rounded-3">
          <div class="d-flex justify-content-between align-items-center">
            <div>
              <h6 class="fw-bold mb-1">${r.name}</h6>
              <div class="small text-muted">網址：<a href="${r.target_url}" target="_blank">${r.target_url}</a></div>
              <div class="small text-muted">選擇器：<code>${r.selector || '無 (全頁搜尋)'}</code> | 關鍵字：<code>${r.match_text || '無'}</code></div>
            </div>
            <button class="btn btn-outline-danger btn-sm" onclick="deleteCustomRule('${r.id}')">
              <i class="bi bi-trash3"></i>
            </button>
          </div>
        </div>
      `;
    });
    container.innerHTML = html;
  } catch (e) {
    console.error("載入自訂規則失敗", e);
  }
}

async function deleteCustomRule(id) {
  try {
    await fetch("/api/custom_crawler/rules/" + id, { method: "DELETE" });
    loadCustomRules();
  } catch (e) {
    alert("刪除失敗: " + e);
  }
}

// 輪詢本機桌面推播
async function pollLocalNotifications() {
  try {
    const url = "/api/notifications/poll" + (lastNotificationId ? "?since_id=" + lastNotificationId : "");
    const res = await fetch(url);
    if (res.ok) {
      const data = await res.json();
      const list = data.notifications || [];
      if (list.length > 0) {
        lastNotificationId = list[list.length - 1].id;
        list.forEach(n => {
          if (Notification.permission === "granted") {
            const notif = new Notification(n.title, {
              body: n.body,
              icon: "https://cdn-icons-png.flaticon.com/512/891/891462.png"
            });
            if (n.url) {
              notif.onclick = () => { window.open(n.url, "_blank"); };
            }
          }
        });
      }
    }
  } catch (e) {}
  setTimeout(pollLocalNotifications, 3000);
}

// ======================== 巡檢即時狀態與倒數計時 ========================
let schedulerTimerSeconds = 0;
let schedulerNotifySeconds = null;
let isSchedulerActive = true;

function formatCountdown(sec) {
  if (sec === null || sec === undefined) return "--:--";
  if (sec < 0) return "僅補貨時通知";
  if (sec === 0) return "正在檢查中...";
  const m = Math.floor(sec / 60);
  const s = sec % 60;
  if (m >= 60) {
    const h = Math.floor(m / 60);
    const remM = m % 60;
    return h + " 小時 " + remM + " 分";
  }
  return (m < 10 ? "0" + m : m) + ":" + (s < 10 ? "0" + s : s);
}

async function fetchSchedulerStatus() {
  try {
    const res = await fetch("/api/scheduler/status");
    if (!res.ok) return;
    const data = await res.json();
    isSchedulerActive = data.is_running;

    // 更新 ON / OFF 狀態
    const badge = document.getElementById("schedulerStateText");
    const iconBox = document.getElementById("schedulerStatusBadge");
    const toggleBtn = document.getElementById("toggleSchedulerBtn");
    if (isSchedulerActive) {
      badge.className = "badge bg-success fs-6 px-3 py-1";
      badge.innerText = "運作中 (ON)";
      iconBox.className = "rounded-circle p-3 d-flex align-items-center justify-content-center bg-success-subtle text-success";
      toggleBtn.className = "btn btn-sm btn-outline-danger";
      toggleBtn.innerText = "暫停監控";
    } else {
      badge.className = "badge bg-secondary fs-6 px-3 py-1";
      badge.innerText = "已暫停 (OFF)";
      iconBox.className = "rounded-circle p-3 d-flex align-items-center justify-content-center bg-secondary-subtle text-secondary";
      toggleBtn.className = "btn btn-sm btn-outline-success";
      toggleBtn.innerText = "啟動監控";
    }

    schedulerTimerSeconds = data.next_check_seconds;
    schedulerNotifySeconds = data.next_notify_seconds;

    document.getElementById("pollCycleHint").innerText = "週期: 每 " + data.poll_interval_minutes + " 分鐘";
    document.getElementById("oosCycleHint").innerText = data.oos_notify_hours > 0 ? ("缺貨回報: 每 " + data.oos_notify_hours + " 小時") : "缺貨回報: 僅補貨通知";

    document.getElementById("lastCheckTimeText").innerText = data.last_check_time || "尚未執行";
    document.getElementById("lastCheckResultText").innerText = data.last_check_result || "尚未執行";
    document.getElementById("lastNotifyTimeText").innerText = data.last_notify_time || "尚未觸發通知";

    updateCountdownDisplay();
  } catch (e) {
    console.error("讀取排程狀態失敗", e);
  }
}

function updateCountdownDisplay() {
  const checkElem = document.getElementById("nextCheckCountdown");
  const notifyElem = document.getElementById("nextNotifyCountdown");

  if (!isSchedulerActive) {
    checkElem.innerText = "已暫停";
    checkElem.className = "fw-bold text-muted mb-0 mt-1";
    notifyElem.innerText = "已暫停";
    notifyElem.className = "fw-bold text-muted mb-0 mt-1";
    return;
  }

  checkElem.className = "fw-bold text-primary mb-0 mt-1";
  checkElem.innerText = formatCountdown(schedulerTimerSeconds);

  notifyElem.className = "fw-bold text-dark mb-0 mt-1";
  notifyElem.innerText = formatCountdown(schedulerNotifySeconds);
}

// 每秒本地平滑倒數
setInterval(() => {
  if (isSchedulerActive && schedulerTimerSeconds > 0) {
    schedulerTimerSeconds--;
    if (schedulerNotifySeconds && schedulerNotifySeconds > 0) {
      schedulerNotifySeconds--;
    }
    updateCountdownDisplay();
  }
}, 1000);

// 每 3 秒同步一次後端真實排程數據
setInterval(fetchSchedulerStatus, 3000);

async function toggleScheduler() {
  try {
    const res = await fetch("/api/scheduler/toggle", { method: "POST" });
    if (res.ok) {
      await fetchSchedulerStatus();
    }
  } catch (e) {
    alert("切換狀態失敗: " + e);
  }
}

window.addEventListener("DOMContentLoaded", () => {
  if ("Notification" in window) {
    updateNotifButton(Notification.permission);
  }
  loadConfig();
  loadAutobuyConfig();
  fetchAll();
  loadScheduledTasks();
  loadCustomRules();
  fetchSchedulerStatus();
  pollLocalNotifications();
});
</script>
</body>
</html>
"""

@app.get("/", response_class=HTMLResponse)
def index_page():
    return HTML_DASHBOARD


if __name__ == "__main__":
    import uvicorn
    print("=" * 65)
    print("🚀 啟動【本地 Web 旗艦工作台 + 預約搶登入 + 萬用視覺爬蟲 + 桌面推播】")
    print("🌐 控制台網址: http://localhost:8000")
    print("📁 資料檔案: " + str(DATA_FILE))
    print("=" * 65)
    uvicorn.run(app, host="0.0.0.0", port=8000)

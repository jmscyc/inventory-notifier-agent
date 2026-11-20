#!/usr/bin/env python3
"""
【本地全功能整合版 - Web 控制台 + 自動加車/自動結帳 + 桌面推播 + Telegram 雙軌庫存監控系統】

功能特點：
1. 階梯式自動加車與自動結帳 (Auto-Cart & Auto-Checkout):
   - 第一階段：補貨時自動以 Cookie / 帳號加入購物車，推播「已為您自動放入購物車！請在 15 分鐘內點此完成結帳」
   - 第二階段：支援一鍵直達結帳確認頁面，或自動推進至付款確認頁
   - 容錯保護：每一段失敗至少保留前段正常執行，並提供即時推播通知
2. 現代化 Web UI 控制台 (http://localhost:8000)
3. 支援 HTML5 桌面推播通知與 Telegram 雙軌通知
4. 單一檔案直接在本地端執行，零雲端依賴！
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
from typing import Optional

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
)

load_dotenv()

DATA_FILE = Path(__file__).parent / "tracked_products.json"
CONFIG_FILE = Path(__file__).parent / "app_config.json"
TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")

# =====================================================================
# 1. 組態設定與本地資料庫持久化
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
NOTIFY_STATE = {}
RECENT_LOCAL_NOTIFICATIONS = []

def load_tracked_items() -> list:
    if not DATA_FILE.exists():
        return []
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_tracked_items(items: list):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(items, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存商品清單失敗: {e}")

def add_or_update_item(name: str, target_url: str, in_stock: bool, status_notes: str, item_id: str = "") -> dict:
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
# 2. 核心爬蟲檢測模組 (精準判定 Costco / 一般電商)
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
# 3. 雙向通知發送器 (本機推播佇列 + Telegram)
# =====================================================================

def is_quiet_hours() -> bool:
    now_taipei = datetime.datetime.now(TAIPEI_TZ)
    return 1 <= now_taipei.hour < 5

async def broadcast_notification(client: Optional[httpx.AsyncClient], title: str, body: str, target_url: str = "", button_text: str = "🛒 前往商品頁面"):
    # 1. 登記到本機推播佇列
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

    # 2. 發送 Telegram 通知
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
            # 觸發搶購流水線
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

        # 補貨上架：缺貨 -> 有貨 (觸發階梯式自動加車與結帳流水線！)
        if in_stock and (last_notified_stock is not True):
            should_send = True
            flow_res = await execute_autobuy_flow(target_url, name)
            notify_title = flow_res["status_title"]
            notify_body = flow_res["status_message"]
            action_url = flow_res["action_url"]
            btn_text = flow_res["action_button_text"]

        # 初次缺貨
        elif (not in_stock) and (last_notified_stock is None or last_notified_stock is True):
            should_send = True
            notify_title = "📢 **【商品目前缺貨提醒】**"
            notify_body = f"📦 **商品**：{name}\n🔴 **狀態**：{status_desc}\n系統正每 {poll_interval} 分鐘自動巡檢！"

        # 持續缺貨例行回報
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
# 4. 背景定時巡檢排程 Task
# =====================================================================

async def background_scheduler():
    print("⏰ 全功能背景庫存巡檢排程已啟動...")
    async with httpx.AsyncClient(timeout=30.0) as client:
        while True:
            try:
                items = load_tracked_items()
                for item in items:
                    if item.get("target_url"):
                        try:
                            await perform_check_and_notify_single(client, item, is_manual=False)
                        except Exception as e:
                            print(f"巡檢異常 ({item.get('id')}): {e}")
                        await asyncio.sleep(2)
            except Exception as e:
                print(f"排程迴圈例外: {e}")

            interval = APP_CONFIG.get("poll_interval_minutes", 5)
            await asyncio.sleep(interval * 60)


# =====================================================================
# 5. Telegram Bot 輪詢 Task (可選開關)
# =====================================================================

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
# 6. FastAPI Web 控制台與 API 路由
# =====================================================================

app = FastAPI(title="商品庫存追蹤與通知控制台")

class AddItemReq(BaseModel):
    url: str

class UpdateConfigReq(BaseModel):
    poll_interval_minutes: int
    oos_notify_hours: int
    enable_telegram: bool
    enable_local_notification: bool
    telegram_bot_token: Optional[str] = ""

class UpdateAutobuyReq(BaseModel):
    enabled: bool
    auto_checkout: bool
    account_email: Optional[str] = ""
    account_password: Optional[str] = ""
    cookies_json: Optional[str] = ""

@app.on_event("startup")
async def startup_event():
    asyncio.create_task(background_scheduler())
    asyncio.create_task(telegram_bot_worker())

@app.get("/api/items")
def api_get_items():
    return {"items": load_tracked_items()}

@app.post("/api/items")
async def api_add_item(req: AddItemReq):
    url = req.url.strip()
    if not url.startswith("http"):
        return JSONResponse({"success": False, "message": "請輸入有效的 HTTP/HTTPS 商品網址！"}, status_code=400)
    crawl_res = await asyncio.to_thread(check_product_inventory, url)
    title = crawl_res.get("title", url)
    in_stock = crawl_res.get("in_stock", False)
    status_desc = crawl_res.get("status_description", "")
    item = add_or_update_item(name=title, target_url=url, in_stock=in_stock, status_notes=status_desc)
    NOTIFY_STATE[item["id"]] = {"last_notified_stock": in_stock, "last_notified_time": time.time()}
    return {"success": True, "item": item, "in_stock": in_stock, "status_notes": status_desc}

@app.delete("/api/items/{item_id}")
def api_delete_item(item_id: str):
    ok = delete_item(item_id)
    NOTIFY_STATE.pop(item_id, None)
    return {"success": ok}

@app.post("/api/items/refresh_all")
async def api_refresh_all():
    items = load_tracked_items()
    async def refresh_one(it):
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
    fresh = await asyncio.gather(*(refresh_one(it) for it in items))
    save_tracked_items(list(fresh))
    return {"success": True, "items": list(fresh)}

@app.post("/api/items/{item_id}/refresh")
async def api_refresh_single(item_id: str):
    items = load_tracked_items()
    matched = [it for it in items if it.get("id") == item_id]
    if not matched:
        return JSONResponse({"success": False, "message": "查無此商品"}, status_code=404)
    async with httpx.AsyncClient() as client:
        await perform_check_and_notify_single(client, matched[0], is_manual=True)
    return {"success": True}

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
    return {
        "config": cfg,
        "has_cookies": bool(cookies),
        "cookie_count": len(cookies),
    }

@app.post("/api/autobuy")
def api_update_autobuy(req: UpdateAutobuyReq):
    cfg = load_autobuy_config()
    cfg["enabled"] = req.enabled
    cfg["auto_checkout"] = req.auto_checkout
    if req.account_email:
        cfg["account_email"] = req.account_email.strip()
    if req.account_password:
        cfg["account_password"] = req.account_password.strip()
    save_autobuy_config(cfg)

    # 若有貼上 Cookie JSON 則保存
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

    return {"success": True, "config": cfg}

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
# 7. 現代化 Web 前端頁面 (繁體中文 Single Page Application)
# =====================================================================

HTML_DASHBOARD = """<!DOCTYPE html>
<html lang="zh-TW">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>商品庫存監控與智慧提醒控制台</title>
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
  </style>
</head>
<body class="p-3 p-md-4">
<div class="container-fluid" style="max-width: 1100px;">

  <!-- 頂部 Header -->
  <div class="p-4 mb-4 header-card shadow-sm d-flex flex-wrap justify-content-between align-items-center">
    <div>
      <h3 class="mb-1 fw-bold"><i class="bi bi-box-seam me-2"></i>商品庫存監控與自動搶購控制台</h3>
      <p class="mb-0 text-white-50">支援好市多 Costco、PChome 等電商庫存即時偵測・自動加購物車・本機桌面推播與 Telegram 雙向通報</p>
    </div>
    <div class="mt-3 mt-md-0 d-flex gap-2">
      <button class="btn btn-outline-light btn-sm" onclick="requestNotificationPermission()">
        <i class="bi bi-bell-fill me-1"></i><span id="notifBtnText">啟用瀏覽器本機通知</span>
      </button>
      <button class="btn btn-light btn-sm text-primary fw-bold" onclick="fetchAll(true)">
        <i class="bi bi-arrow-clockwise me-1"></i>全部重新爬蟲
      </button>
    </div>
  </div>

  <div class="row g-4">
    <!-- 左側：新增商品與清單 -->
    <div class="col-lg-8">
      <!-- 新增商品卡片 -->
      <div class="card shadow-sm border-0 mb-4 rounded-3">
        <div class="card-body p-4">
          <h5 class="fw-bold mb-3"><i class="bi bi-plus-circle text-primary me-2"></i>新增追蹤商品</h5>
          <div class="input-group">
            <input type="url" id="newUrlInput" class="form-control form-control-lg" placeholder="請貼上商品完整網址 (例: https://www.costco.com.tw/.../p/123456)">
            <button class="btn btn-primary px-4 fw-bold" id="addBtn" onclick="addProduct()">
              <span id="addBtnSpinner" class="spinner-border spinner-border-sm d-none me-1"></span>新增追蹤
            </button>
          </div>
          <div class="form-text text-muted mt-2">系統將在加入前即刻至電商頁面檢驗是否具備現貨與購物車按鈕。</div>
        </div>
      </div>

      <!-- 追蹤商品列表 -->
      <div class="d-flex justify-content-between align-items-center mb-3">
        <h5 class="fw-bold mb-0"><i class="bi bi-list-task me-2"></i>追蹤清單 (<span id="itemCount">0</span>)</h5>
        <small class="text-muted" id="lastUpdatedText"></small>
      </div>

      <div id="productList" class="d-flex flex-column gap-3">
        <div class="text-center py-5 text-muted">正在載入商品清單...</div>
      </div>
    </div>

    <!-- 右側：巡檢與通知、自動搶購偏好設定 -->
    <div class="col-lg-4">
      <!-- 自動搶購設定卡片 -->
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

          <div class="mb-3">
            <label class="form-label small fw-bold mb-1">電商登入 Session / Cookie (推薦貼上 Cookie JSON)</label>
            <textarea id="cfgCookiesJson" rows="2" class="form-control form-control-sm" placeholder='可透過瀏覽器擴充套件導出 Cookie JSON 貼於此處'></textarea>
            <div class="small text-success mt-1" id="cookieStatusText"></div>
          </div>

          <button class="btn btn-warning w-100 fw-bold py-2" onclick="saveAutobuyConfig()">
            <i class="bi bi-save me-1"></i>儲存搶購模組設定
          </button>
        </div>
      </div>

      <!-- 巡檢與通知偏好設定 -->
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
            <label class="form-check-label fw-bold" for="cfgLocalNotif">啟用本機/瀏覽器推播通知</label>
          </div>
          <small class="text-muted d-block mb-3">當庫存狀態變化或補貨時，在螢幕右下角彈出桌面推播通知與音效。</small>

          <div class="form-check form-switch mb-2">
            <input class="form-check-input" type="checkbox" id="cfgTelegram" checked>
            <label class="form-check-label fw-bold" for="cfgTelegram">啟用 Telegram 提醒通知</label>
          </div>
          <small class="text-muted d-block mb-3">若您不想使用 Telegram，取消勾選此處即可完全只在本機運作。</small>

          <button class="btn btn-success w-100 fw-bold py-2 mt-2" onclick="saveConfig()">
            <i class="bi bi-check-lg me-1"></i>儲存一般偏好設定
          </button>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let lastNotificationId = null;

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
    btn.innerText = "啟用瀏覽器本機通知";
  }
}

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

async function loadAutobuyConfig() {
  try {
    const res = await fetch("/api/autobuy");
    const data = await res.json();
    const cfg = data.config || {};
    document.getElementById("cfgAutobuyEnabled").checked = !!cfg.enabled;
    document.getElementById("cfgAutoCheckout").checked = !!cfg.auto_checkout;
    const cookieText = document.getElementById("cookieStatusText");
    if (data.has_cookies) {
      cookieText.innerText = "✅ 本地已載入 " + data.cookie_count + " 個 Cookie 憑證！";
    } else {
      cookieText.innerText = "ℹ️ 尚未匯入 Cookie，若補貨將降級為手動一鍵搶購。";
    }
  } catch (e) {
    console.error("載入搶購設定失敗", e);
  }
}

async function saveAutobuyConfig() {
  const payload = {
    enabled: document.getElementById("cfgAutobuyEnabled").checked,
    auto_checkout: document.getElementById("cfgAutoCheckout").checked,
    cookies_json: document.getElementById("cfgCookiesJson").value.trim()
  };
  try {
    const res = await fetch("/api/autobuy", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });
    if (res.ok) {
      alert("✅ 自動搶購與加車模組設定已更新！");
      document.getElementById("cfgCookiesJson").value = "";
      loadAutobuyConfig();
    } else {
      const err = await res.json();
      alert("❌ 儲存失敗: " + (err.message || "未知錯誤"));
    }
  } catch (e) {
    alert("❌ 儲存失敗: " + e);
  }
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

    html += `
      <div class="card product-card p-3">
        <div class="card-body p-2">
          <div class="d-flex justify-content-between align-items-start mb-2">
            <h6 class="fw-bold mb-1 text-truncate" style="max-width: 75%;">${it.name || "未命名商品"}</h6>
            ${badge}
          </div>
          <div class="small text-muted mb-2">
            <i class="bi bi-info-circle me-1"></i>狀態詳情：${it.status_notes || "無"}
          </div>
          <div class="small text-muted mb-3">
            <i class="bi bi-clock-history me-1"></i>最後檢查：${it.last_checked || "未記錄"}
          </div>
          <div class="d-flex justify-content-between align-items-center">
            <div class="d-flex gap-2">
              <a href="${it.target_url}" target="_blank" class="btn btn-outline-primary btn-sm">
                <i class="bi bi-box-arrow-up-right me-1"></i>開啟賣場頁面
              </a>
              <button class="btn btn-outline-secondary btn-sm" onclick="refreshSingle('${it.id}')">
                <i class="bi bi-arrow-repeat me-1"></i>立即重新檢查
              </button>
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
  const url = input.value.trim();
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
      body: JSON.stringify({ url })
    });
    const data = await res.json();
    if (res.ok) {
      input.value = "";
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

window.addEventListener("DOMContentLoaded", () => {
  if ("Notification" in window) {
    updateNotifButton(Notification.permission);
  }
  loadConfig();
  loadAutobuyConfig();
  fetchAll();
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
    print("🚀 啟動【本地 Web 控制台 + 階梯式自動加車/結帳 + 桌面推播系統】")
    print("🌐 控制台網址: http://localhost:8000")
    print("📁 資料檔案: " + str(DATA_FILE))
    print("=" * 65)
    uvicorn.run(app, host="0.0.0.0", port=8000)

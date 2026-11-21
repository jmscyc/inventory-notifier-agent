#!/usr/bin/env python3
"""
GitHub Actions 專用無伺服器雲端監控核心腳本 (Telegram 視覺化互動按鈕旗艦版)
- 100% 免費、免開電腦、免 CMD、免信用卡
- 全視覺化按鈕 (Inline Keyboards & Reply Keyboards)，小白直接點擊免背指令
- 整合 settings / products / scheduled_tasks 於單一 tracked_products.json
"""

import asyncio
import datetime
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional, Tuple, Dict, Any, List

import httpx
from bs4 import BeautifulSoup

try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

BASE_DIR = Path(__file__).parent
DATA_FILE = BASE_DIR / "tracked_products.json"
LAST_STATE_FILE = BASE_DIR / "last_check_status.json"

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "").strip()
GOOGLE_SHEET_API_URL = os.getenv("GOOGLE_SHEET_API_URL", "").strip()

def sync_from_google_sheet() -> Optional[List[Dict[str, Any]]]:
    """若設定了 GOOGLE_SHEET_API_URL，從 Google Sheet 優先同步最新商品清單"""
    if not GOOGLE_SHEET_API_URL:
        return None
    try:
        resp = httpx.get(f"{GOOGLE_SHEET_API_URL}?action=getItems&t={int(time.time())}", timeout=10.0, follow_redirects=True)
        if resp.status_code == 200:
            res_json = resp.json()
            if res_json.get("success") and isinstance(res_json.get("items"), list):
                print(f"✅ 成功自 Google Sheet 同步 {len(res_json['items'])} 件監控商品！")
                return res_json["items"]
    except Exception as e:
        print(f"⚠️ 自 Google Sheet 同步失敗，降級使用本地 JSON: {e}")
    return None

def sync_to_google_sheet(product: Dict[str, Any], in_stock: bool, status_notes: str, current_price: str = ""):
    """若設定了 GOOGLE_SHEET_API_URL，回寫單一商品最新狀態至 Google Sheet"""
    if not GOOGLE_SHEET_API_URL:
        return
    try:
        payload = {
            "action": "updateStatus",
            "id": product.get("id", ""),
            "url": product.get("target_url") or product.get("url", ""),
            "name": product.get("name", ""),
            "in_stock": in_stock,
            "status_notes": status_notes,
            "current_price": current_price
        }
        httpx.post(GOOGLE_SHEET_API_URL, json=payload, timeout=8.0, follow_redirects=True)
    except Exception as e:
        print(f"⚠️ 回寫 Google Sheet 失敗: {e}")

def load_config_and_products() -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], List[Dict[str, Any]]]:
    default_config = {
        "is_active": True,
        "quiet_hours_enabled": True,
        "quiet_hours_start": 1,
        "quiet_hours_end": 5,
        "oos_periodic_notify_hours": 4,
        "telegram": {"enabled": True}
    }
    
    settings = default_config
    products = []
    scheduled_tasks = []
    custom_rules = []

    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, list):
                    products = data
                else:
                    settings = data.get("settings", default_config)
                    products = data.get("products", [])
                    scheduled_tasks = data.get("scheduled_tasks", [])
                    custom_rules = data.get("custom_rules", [])
        except Exception as e:
            print(f"⚠️ 讀取 {DATA_FILE} 失敗: {e}")

    # 優先嘗試從 Google Sheet 同步最新商品
    remote_products = sync_from_google_sheet()
    if remote_products is not None:
        products = remote_products

    return settings, products, scheduled_tasks, custom_rules

def save_config_and_products(settings: Dict[str, Any], products: List[Dict[str, Any]], scheduled_tasks: List[Dict[str, Any]] = None, custom_rules: List[Dict[str, Any]] = None):
    try:
        data = {
            "_說明": "【好市多智慧監控設定檔】直接在此修改或透過 Telegram 隨時調整！",
            "settings": settings,
            "products": products,
            "scheduled_tasks": scheduled_tasks or [],
            "custom_rules": custom_rules or []
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存 {DATA_FILE} 失敗: {e}")

def is_in_quiet_hours(settings: Dict[str, Any]) -> bool:
    if not settings.get("quiet_hours_enabled", True):
        return False
    start_h = settings.get("quiet_hours_start", 1)
    end_h = settings.get("quiet_hours_end", 5)
    current_hour = datetime.datetime.now(TAIPEI_TZ).hour
    if start_h <= end_h:
        return start_h <= current_hour < end_h
    else:
        return current_hour >= start_h or current_hour < end_h

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:123.0) Gecko/20100101 Firefox/123.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_3_1) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36 Edg/121.0.0.0"
]

def get_random_ua() -> str:
    import random
    return random.choice(USER_AGENTS)

def check_product_inventory(url: str) -> dict:
    headers = {
        "User-Agent": get_random_ua(),
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
            status_desc = "缺貨 (顯示缺貨中)"
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

    return {
        "url": url,
        "title": title,
        "in_stock": False,
        "status_description": "非好市多網址或未知電商",
        "checked_at": now_str,
    }

def get_main_menu_markup():
    """返回 Telegram 訊息下方的視覺化互動按鈕選單 (點一下就觸發)"""
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

async def send_telegram_alert(client: httpx.AsyncClient, title: str, body: str, target_url: str = "", buttons: list = None):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return

    api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": f"{title}\n\n{body}",
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }
    if buttons:
        payload["reply_markup"] = {"inline_keyboard": buttons}
    elif target_url:
        payload["reply_markup"] = {
            "inline_keyboard": [
                [{"text": "🛒 立刻前往搶購 / 結帳", "url": target_url}],
                [{"text": "📱 開啟主控制台", "callback_data": "menu_main"}]
            ]
        }
    else:
        payload["reply_markup"] = get_main_menu_markup()

    try:
        await client.post(api_url, json=payload, timeout=15.0)
    except Exception as e:
        print(f"❌ Telegram 推播失敗: {e}")

async def process_telegram_incoming_messages(client: httpx.AsyncClient, settings: Dict[str, Any], products: List[Dict[str, Any]], scheduled_tasks: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], bool, bool]:
    """處理 Telegram 點擊按鈕 (Callback Queries) 與文字輸入"""
    if not TELEGRAM_BOT_TOKEN:
        return settings, products, scheduled_tasks, False, False

    updated = False
    trigger_immediate_check = False

    try:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = await client.get(api_url, params={"limit": 30, "timeout": 2})
        if resp.status_code != 200:
            return settings, products, scheduled_tasks, False, False
        data = resp.json()
        if not data.get("ok"):
            return settings, products, scheduled_tasks, False, False

        updates = data.get("result", [])
        last_update_id = 0
        for upd in updates:
            upd_id = upd.get("update_id", 0)
            if upd_id > last_update_id:
                last_update_id = upd_id

            # -------------------------------------------------------------
            # A. 處理按鈕點擊 (Callback Query) ➡️ 免背指令！點一下就觸發！
            # -------------------------------------------------------------
            cb = upd.get("callback_query")
            if cb:
                cb_id = cb.get("id")
                cb_data = cb.get("data", "")
                from_chat_id = str(cb.get("message", {}).get("chat", {}).get("id", ""))
                msg_id = cb.get("message", {}).get("message_id")

                # 回應 Telegram 按鈕點擊狀態，消除讀取轉圈圈
                try:
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/answerCallbackQuery", json={"callback_query_id": cb_id})
                except Exception:
                    pass

                if TELEGRAM_CHAT_ID and from_chat_id != TELEGRAM_CHAT_ID:
                    continue

                if cb_data == "menu_main" or cb_data == "menu_help":
                    help_text = (
                        "🎛️ **【好市多雲端智慧監控控制台】**\n\n"
                        "點選下方任一按鈕即可操作，**完全免背指令**：\n\n"
                        "💡 **想新增商品？**\n"
                        "只要在對話框直接「貼上好市多商品網址」送出，機器人就會自動加入！"
                    )
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": help_text,
                        "parse_mode": "Markdown",
                        "reply_markup": get_main_menu_markup()
                    })

                elif cb_data == "menu_list":
                    if not products:
                        reply = "📋 目前尚未追蹤任何商品。直接貼上好市多商品網址即可新增！"
                        kb = get_main_menu_markup()
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

                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": reply,
                        "parse_mode": "Markdown",
                        "reply_markup": kb
                    })

                elif cb_data.startswith("del_"):
                    del_id = cb_data.replace("del_", "").strip()
                    before_cnt = len(products)
                    products = [p for p in products if str(p.get("id")) != del_id]
                    if len(products) < before_cnt:
                        updated = True
                        del_reply = f"🗑️ 已成功將商品 (編號 `{del_id}`) 從清單中刪除！"
                    else:
                        del_reply = f"⚠️ 找不到該商品。"
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": del_reply,
                        "parse_mode": "Markdown",
                        "reply_markup": get_main_menu_markup()
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
                        f"🕒 **目前伺服器時間**：`{now_str}`"
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
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": status_reply,
                        "parse_mode": "Markdown",
                        "reply_markup": kb
                    })

                elif cb_data == "toggle_active":
                    settings["is_active"] = not settings.get("is_active", True)
                    updated = True
                    act_state = "🟢 運作中 (ON)" if settings["is_active"] else "🔴 已暫停 (OFF)"
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": f"⚡ **總監控狀態已切換為**：{act_state}",
                        "parse_mode": "Markdown",
                        "reply_markup": get_main_menu_markup()
                    })

                elif cb_data == "toggle_quiet":
                    settings["quiet_hours_enabled"] = not settings.get("quiet_hours_enabled", True)
                    updated = True
                    q_state = "🟢 已開啟夜間防打擾" if settings["quiet_hours_enabled"] else "☀️ 已關閉夜間防打擾 (24H通知)"
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": f"🌙 **夜間靜音設定已切換**：{q_state}",
                        "parse_mode": "Markdown",
                        "reply_markup": get_main_menu_markup()
                    })

                elif cb_data == "menu_check":
                    trigger_immediate_check = True
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": from_chat_id,
                        "text": "🔍 **收到立即巡檢要求！** 正在即時抓取所有商品最新庫存...",
                        "parse_mode": "Markdown"
                    })

            # -------------------------------------------------------------
            # B. 處理純文字訊息 (貼網址自動加入 / 任何文字叫出主選單按鈕)
            # -------------------------------------------------------------
            msg = upd.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
                continue

            if not text:
                continue

            # 1. 貼網址直接新增
            url_match = re.search(r"https?://[^\s]+costco\.com[^\s]+", text)
            if url_match:
                new_url = url_match.group(0)
                code_match = re.search(r"/p/(\d+)", new_url)
                p_code = code_match.group(1) if code_match else str(int(time.time()))
                
                exists = any(p.get("url") == new_url or p.get("target_url") == new_url for p in products)
                if not exists:
                    c_res = check_product_inventory(new_url)
                    p_name = c_res.get("title") or f"Costco 商品 ({p_code})"
                    new_item = {
                        "id": p_code,
                        "name": p_name,
                        "url": new_url,
                        "enabled": True,
                        "last_status": "🟢 有現貨" if c_res.get("in_stock") else f"🔴 {c_res.get('status_description')}",
                        "last_checked": datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                    }
                    products.append(new_item)
                    updated = True
                    reply = (
                        f"✅ **【成功新增商品監控！】**\n\n"
                        f"📦 **名稱**：{p_name}\n"
                        f"🔗 **網址**：{new_url}\n"
                        f"📊 **目前狀態**：`{new_item['last_status']}`\n\n"
                        f"🤖 雲端將自動每 15 分鐘監控，有貨第一時間推播通知！"
                    )
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": reply,
                        "parse_mode": "Markdown",
                        "reply_markup": get_main_menu_markup()
                    })

            # 2. 任何輸入文字或 /start 都送出全視覺化選單
            else:
                main_msg = (
                    "🎛️ **【好市多雲端智慧監控控制台】**\n\n"
                    "歡迎使用！請直接點擊下方按鈕進行操作：\n\n"
                    "• 點擊 **【📋 檢視商品清單】** 可查看商品與單鍵刪除\n"
                    "• 點擊 **【🔍 立即觸發全檢】** 立即爬蟲更新最新狀態\n"
                    "• 點擊 **【⚙️ 系統設定儀表板】** 查看各項週期與開關\n"
                    "• 想新增商品？**直接貼上好市多商品網址**即可！"
                )
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": main_msg,
                    "parse_mode": "Markdown",
                    "reply_markup": get_main_menu_markup()
                })

        if last_update_id > 0:
            await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", params={"offset": last_update_id + 1})

    except Exception as e:
        print(f"處理 Telegram 互動失敗: {e}")

    return settings, products, scheduled_tasks, updated, trigger_immediate_check

async def main():
    print("=" * 60)
    print("🚀 啟動 GitHub Actions 雲端巡檢作業 (Telegram 全按鈕旗艦版)...")
    now_taipei = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"🕒 執行時間 (台灣時間): {now_taipei}")
    print("=" * 60)

    settings, products, scheduled_tasks, custom_rules = load_config_and_products()

    async with httpx.AsyncClient(timeout=30.0) as client:
        settings, products, scheduled_tasks, tg_updated, trigger_immediate = await process_telegram_incoming_messages(
            client, settings, products, scheduled_tasks
        )

        if not settings.get("is_active", True) and not trigger_immediate:
            print("⏸️ 系統設定為停用中 (is_active = false)，跳過本次巡檢。")
            if tg_updated:
                save_config_and_products(settings, products, scheduled_tasks, custom_rules)
            sys.exit(0)

        in_quiet = is_in_quiet_hours(settings)
        if in_quiet and not trigger_immediate:
            print("🌙 目前處於夜間靜音時段，略過訊息推播以防打擾。")

        in_stock_found = []
        oos_found = []

        # 1. 巡檢標準電商 / 好市多商品
        for p in products:
            if not p.get("enabled", True):
                continue
            target_url = p.get("url") or p.get("target_url")
            if not target_url:
                continue

            p_name = p.get("name") or "好市多商品"
            print(f"🔍 正在檢查: {p_name} ...")
            c_res = check_product_inventory(target_url)

            in_stock = c_res.get("in_stock", False)
            status_desc = c_res.get("status_description", "")
            if c_res.get("title") and c_res.get("title") != target_url:
                p["name"] = c_res.get("title")
            p["last_status"] = "🟢 有現貨" if in_stock else f"🔴 {status_desc}"
            p["last_checked"] = now_taipei

            if in_stock:
                in_stock_found.append(p)
                print(f"🎉【發現有現貨！】{p['name']}")
                if (not in_quiet or trigger_immediate) and settings.get("telegram", {}).get("enabled", True):
                    await send_telegram_alert(
                        client,
                        "🚨 **【GitHub 雲端緊急補貨通知！】**",
                        f"📦 **商品**：{p['name']}\n🟢 **狀態**：{status_desc}\n⏰ **時間**：`{now_taipei}`\n\n⚡ 請在 10 分鐘內點擊下方按鈕結帳！",
                        target_url
                    )
            else:
                oos_found.append(p)
                print(f"🔴【缺貨中】{p['name']} ({status_desc})")

            # 雲端同步回寫 Google Sheet
            sync_to_google_sheet(p, in_stock, status_desc, c_res.get("price", ""))

        # 2. 巡檢自訂萬用網頁爬蟲規則 (新購物網站 / 預約規則)
        for r in custom_rules:
            r_name = r.get("name", "自訂規則")
            r_url = r.get("target_url", "")
            r_sel = r.get("selector", "")
            r_match = r.get("match_text", "")
            if not r_url:
                continue

            print(f"🎯 正在雲端巡檢自訂規則: {r_name} ...")
            try:
                headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
                resp = await client.get(r_url, headers=headers, timeout=12.0)
                page_html = resp.text
                soup = BeautifulSoup(page_html, "html.parser")

                is_triggered = False
                desc = "未觸發條件"
                if r_sel:
                    matched_nodes = soup.select(r_sel)
                    if matched_nodes:
                        txt = matched_nodes[0].get_text(strip=True)
                        if r_match:
                            if r_match.lower() in txt.lower():
                                is_triggered = True
                                desc = f"選擇器吻合且文字包含『{r_match}』: {txt[:40]}"
                        else:
                            is_triggered = True
                            desc = f"選擇器元素存在: {txt[:40]}"
                elif r_match:
                    if r_match.lower() in page_html.lower():
                        is_triggered = True
                        desc = f"網頁中出現目標關鍵字『{r_match}』"

                r["last_status"] = "🟢 觸發條件 (有貨/開放)" if is_triggered else f"⚪ 巡檢正常 ({desc})"
                r["last_checked"] = now_taipei

                if is_triggered:
                    in_stock_found.append({"name": r_name, "url": r_url})
                    if (not in_quiet or trigger_immediate) and settings.get("telegram", {}).get("enabled", True):
                        await send_telegram_alert(
                            client,
                            f"🚨 **【新購物網站自動爬蟲觸發通知！】**",
                            f"🎯 **規則名稱**：{r_name}\n🟢 **狀態**：{desc}\n⏰ **時間**：`{now_taipei}`",
                            r_url
                        )
            except Exception as e:
                print(f"自訂規則 {r_name} 爬蟲異常: {e}")

        # 若使用者按了【立即巡檢】，回傳詳細清單與按鈕
        if trigger_immediate:
            res_msg = f"🏁 **【立即巡檢報告完成】**\n\n"
            for it in products:
                res_msg += f"• **{it.get('name')}**：`{it.get('last_status')}`\n"
            for it in custom_rules:
                res_msg += f"• **[自訂] {it.get('name')}**：`{it.get('last_status')}`\n"
            res_msg += f"\n⏰ 檢查時間：`{now_taipei}`"
            await send_telegram_alert(client, "📋 **巡檢報告**", res_msg)

        save_config_and_products(settings, products, scheduled_tasks, custom_rules)

        summary = f"共檢查 {len(products)} 件 (現貨: {len(in_stock_found)} / 缺貨: {len(oos_found)})"
        status_data = {
            "last_checked_taipei": now_taipei,
            "total_items": len(products),
            "in_stock_count": len(in_stock_found),
            "out_of_stock_count": len(oos_found),
            "summary": summary
        }
        with open(LAST_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(status_data, f, ensure_ascii=False, indent=2)

    print("=" * 60)
    print(f"🏁 巡檢完成！{summary}")
    print("=" * 60)

if __name__ == "__main__":
    asyncio.run(main())

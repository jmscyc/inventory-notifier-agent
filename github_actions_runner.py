#!/usr/bin/env python3
"""
GitHub Actions 專用無伺服器雲端監控核心腳本 (Telegram 全功能控制台旗艦版)
- 100% 免費、免開電腦、免 CMD、免信用卡
- 整合 settings 與 products 於單一 tracked_products.json
- Telegram 支援全功能指令 (開關/靜音時段/缺貨週期/預約搶登/商品管理/即時檢查)
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

def load_config_and_products() -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]]]:
    default_config = {
        "is_active": True,
        "quiet_hours_enabled": True,
        "quiet_hours_start": 1,
        "quiet_hours_end": 5,
        "oos_periodic_notify_hours": 4,
        "telegram": {"enabled": True}
    }
    if not DATA_FILE.exists():
        return default_config, [], []

    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return default_config, data, []
            settings = data.get("settings", default_config)
            products = data.get("products", [])
            scheduled_tasks = data.get("scheduled_tasks", [])
            return settings, products, scheduled_tasks
    except Exception as e:
        print(f"⚠️ 讀取 {DATA_FILE} 失敗: {e}")
        return default_config, [], []

def save_config_and_products(settings: Dict[str, Any], products: List[Dict[str, Any]], scheduled_tasks: List[Dict[str, Any]] = None):
    try:
        if scheduled_tasks is None:
            scheduled_tasks = []
        content = {
            "_說明": "【好市多智慧監控設定檔】直接在此修改或透過 Telegram 隨時調整！",
            "settings": settings,
            "products": products,
            "scheduled_tasks": scheduled_tasks
        }
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(content, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存失敗: {e}")

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
                [{"text": "🛒 立刻前往搶購 / 結帳", "url": target_url}]
            ]
        }

    try:
        await client.post(api_url, json=payload, timeout=15.0)
    except Exception as e:
        print(f"❌ Telegram 推播失敗: {e}")

async def process_telegram_incoming_messages(client: httpx.AsyncClient, settings: Dict[str, Any], products: List[Dict[str, Any]], scheduled_tasks: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], List[Dict[str, Any]], List[Dict[str, Any]], bool, bool]:
    """處理 Telegram 指令：支援網頁所有功能的指令化操作與設定！"""
    if not TELEGRAM_BOT_TOKEN:
        return settings, products, scheduled_tasks, False, False

    updated = False
    trigger_immediate_check = False

    try:
        api_url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates"
        resp = await client.get(api_url, params={"limit": 20, "timeout": 2})
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
            msg = upd.get("message", {})
            text = msg.get("text", "").strip()
            chat_id = str(msg.get("chat", {}).get("id", ""))

            if TELEGRAM_CHAT_ID and chat_id != TELEGRAM_CHAT_ID:
                continue

            if not text:
                continue

            # -------------------------------------------------------------
            # 1. 貼商品網址直接新增
            # -------------------------------------------------------------
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
                        "parse_mode": "Markdown"
                    })

            # -------------------------------------------------------------
            # 2. /help 或 /start - 完整控制台指令選單
            # -------------------------------------------------------------
            elif text in ["/help", "/start", "說明", "幫助", "功能"]:
                help_text = (
                    "🛠️ **【好市多雲端智慧監控控制台】**\n\n"
                    "您可以在此直接控制所有網頁功能：\n\n"
                    "📋 **商品管理**：\n"
                    "• 直接貼好市多網址 ➡️ 立即加入監控\n"
                    "• `/list` ➡️ 檢視目前所有商品與庫存現況\n"
                    "• `/del <編號>` ➡️ 移除指定商品 (如 `/del 142997`)\n\n"
                    "⚙️ **系統開關與時間週期**：\n"
                    "• `/status` ➡️ 查看目前所有時間週期與開關設定\n"
                    "• `/on` 啟用總監控 / `/off` 暫停總監控\n"
                    "• `/quiet <開關>` ➡️ 夜間靜音 (如 `/quiet on` 或 `/quiet off`)\n"
                    "• `/quiet_time <開始> <結束>` ➡️ 設定靜音時段 (如 `/quiet_time 1 5`)\n"
                    "• `/oos_hours <小時>` ➡️ 設定缺貨每幾小時提醒 (如 `/oos_hours 4`)\n\n"
                    "⏰ **預約定時與檢查**：\n"
                    "• `/check` ➡️ 立即觸發一次全商品雲端巡檢\n"
                    "• `/reserve <時間> <網址>` ➡️ 預約搶購通知 (如 `/reserve 2026-11-21 10:00 https://...`)\n"
                    "• `/task_list` ➡️ 查看預約搶購排程清單"
                )
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": help_text,
                    "parse_mode": "Markdown"
                })

            # -------------------------------------------------------------
            # 3. /status - 查看完整時間設定與系統儀表板
            # -------------------------------------------------------------
            elif text in ["/status", "狀態", "設定"]:
                now_str = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
                is_act = "🟢 運作中 (ON)" if settings.get("is_active", True) else "🔴 已暫停 (OFF)"
                q_on = "🟢 已啟用" if settings.get("quiet_hours_enabled", True) else "⚪ 已停用"
                q_range = f"{settings.get('quiet_hours_start', 1):02d}:00 ~ {settings.get('quiet_hours_end', 5):02d}:00"
                oos_h = f"{settings.get('oos_periodic_notify_hours', 4)} 小時"
                
                status_reply = (
                    f"📊 **【雲端監控儀表板與時間設定】**\n\n"
                    f"⚡ **總監控狀態**：{is_act}\n"
                    f"⏰ **雲端巡檢週期**：每 `15 分鐘` (GitHub Actions 24/7)\n"
                    f"🌙 **夜間防打擾靜音**：{q_on} (`{q_range}`)\n"
                    f"📢 **缺貨重複提醒週期**：每 `{oos_h}` 一次\n"
                    f"📦 **目前追蹤商品**：共 `{len(products)}` 件\n"
                    f"🕒 **目前伺服器時間**：`{now_str}`\n\n"
                    f"💡 調整設定範例：\n"
                    f"• 更改靜音時間：`/quiet_time 2 6`\n"
                    f"• 更改提醒週期：`/oos_hours 2`\n"
                    f"• 立即全檢：`/check`"
                )
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": status_reply,
                    "parse_mode": "Markdown"
                })

            # -------------------------------------------------------------
            # 4. /on 與 /off - 總監控開關
            # -------------------------------------------------------------
            elif text in ["/on", "開啟"]:
                settings["is_active"] = True
                updated = True
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "🟢 **【總監控已啟用！】** 雲端將持續每 15 分鐘巡檢好市多商品。",
                    "parse_mode": "Markdown"
                })

            elif text in ["/off", "關閉", "暫停"]:
                settings["is_active"] = False
                updated = True
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "🔴 **【總監控已暫停！】** 雲端將暫停所有商品庫存巡檢與推播。",
                    "parse_mode": "Markdown"
                })

            # -------------------------------------------------------------
            # 5. /quiet - 靜音開關與時間時段設定
            # -------------------------------------------------------------
            elif text.startswith("/quiet_time"):
                parts = text.split()
                if len(parts) >= 3 and parts[1].isdigit() and parts[2].isdigit():
                    s_h, e_h = int(parts[1]), int(parts[2])
                    if 0 <= s_h <= 23 and 0 <= e_h <= 23:
                        settings["quiet_hours_start"] = s_h
                        settings["quiet_hours_end"] = e_h
                        settings["quiet_hours_enabled"] = True
                        updated = True
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"🌙 **【夜間靜音時段已更新】**：`{s_h:02d}:00 ~ {e_h:02d}:00` 期間不推播通知。",
                            "parse_mode": "Markdown"
                        })
                else:
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "⚠️ 格式錯誤！範例：`/quiet_time 1 5` (代表凌晨 1 點到 5 點靜音)",
                        "parse_mode": "Markdown"
                    })

            elif text.startswith("/quiet"):
                parts = text.split()
                if len(parts) > 1:
                    arg = parts[1].lower()
                    if arg in ["on", "true", "開"]:
                        settings["quiet_hours_enabled"] = True
                        updated = True
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"🌙 **【夜間防打擾已開啟】** (時段: `{settings.get('quiet_hours_start', 1):02d}:00 ~ {settings.get('quiet_hours_end', 5):02d}:00`)",
                            "parse_mode": "Markdown"
                        })
                    elif arg in ["off", "false", "關"]:
                        settings["quiet_hours_enabled"] = False
                        updated = True
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": "☀️ **【夜間防打擾已停用】** 24 小時有補貨皆立即推播。",
                            "parse_mode": "Markdown"
                        })

            # -------------------------------------------------------------
            # 6. /oos_hours - 缺貨提醒間隔 (小時)
            # -------------------------------------------------------------
            elif text.startswith("/oos_hours"):
                parts = text.split()
                if len(parts) > 1 and parts[1].replace(".", "").isdigit():
                    hours = float(parts[1])
                    settings["oos_periodic_notify_hours"] = hours
                    updated = True
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"📢 **【缺貨提醒週期已更新】**：每隔 `{hours}` 小時報告一次缺貨商品狀態。",
                        "parse_mode": "Markdown"
                    })
                else:
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "⚠️ 請輸入小時數字！範例：`/oos_hours 4` 或 `/oos_hours 2`",
                        "parse_mode": "Markdown"
                    })

            # -------------------------------------------------------------
            # 7. /list - 查詢商品清單
            # -------------------------------------------------------------
            elif text in ["/list", "清單", "商品"]:
                if not products:
                    reply = "📋 目前尚未追蹤任何商品。直接在此傳送好市多商品網址即可新增！"
                else:
                    reply = "📋 **【目前監控中的商品清單】**\n\n"
                    for idx, p in enumerate(products, 1):
                        p_name = p.get("name") or p.get("url")
                        p_status = p.get("last_status", "巡檢中")
                        p_id = p.get("id")
                        reply += f"{idx}. **{p_name}**\n   狀態：`{p_status}`\n   編號：`{p_id}`\n\n"
                    reply += "💡 想停止監控請輸入：`/del 商品編號`\n想新增直接貼上好市多網址即可！"
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "Markdown"
                })

            # -------------------------------------------------------------
            # 8. /del - 刪除商品
            # -------------------------------------------------------------
            elif text.startswith("/del") or text.startswith("/delete"):
                parts = text.split()
                if len(parts) > 1:
                    del_id = parts[1].strip()
                    before_cnt = len(products)
                    products = [p for p in products if str(p.get("id")) != del_id]
                    if len(products) < before_cnt:
                        updated = True
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"🗑️ 已成功移除編號 `{del_id}` 的商品監控！",
                            "parse_mode": "Markdown"
                        })
                    else:
                        await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                            "chat_id": chat_id,
                            "text": f"⚠️ 找不到編號 `{del_id}` 的商品，請輸入 `/list` 查詢編號。",
                            "parse_mode": "Markdown"
                        })

            # -------------------------------------------------------------
            # 9. /check - 立即觸發雲端全商品巡檢
            # -------------------------------------------------------------
            elif text in ["/check", "檢查", "巡檢"]:
                trigger_immediate_check = True
                await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                    "chat_id": chat_id,
                    "text": "🔍 **收到立即巡檢指令！** 正在即時抓取所有商品最新庫存...",
                    "parse_mode": "Markdown"
                })

            # -------------------------------------------------------------
            # 10. /reserve - 預約定時搶購/提醒任務
            # -------------------------------------------------------------
            elif text.startswith("/reserve") or text.startswith("/預約"):
                parts = text.split(maxsplit=2)
                # 格式: /reserve 2026-11-21 10:00:00 https://...
                if len(parts) >= 3:
                    exec_time = parts[1].strip()
                    res_url = parts[2].strip()
                    task_id = f"task_{int(time.time())}"
                    task = {
                        "id": task_id,
                        "execute_at": exec_time,
                        "url": res_url,
                        "status": "pending"
                    }
                    scheduled_tasks.append(task)
                    updated = True
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": f"⏰ **【預約排程已建立】**\n\n時間：`{exec_time}`\n目標：{res_url}\n屆時雲端將即刻發送搶登推播！",
                        "parse_mode": "Markdown"
                    })
                else:
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "⚠️ 格式：`/reserve <預約時間> <商品網址>`\n範例：`/reserve 10:00 https://www.costco.com.tw/p/142997`",
                        "parse_mode": "Markdown"
                    })

            elif text in ["/task_list", "預約清單"]:
                if not scheduled_tasks:
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": "📅 目前無任何預約排程任務。",
                        "parse_mode": "Markdown"
                    })
                else:
                    t_reply = "📅 **【預約排程任務清單】**\n\n"
                    for t in scheduled_tasks:
                        t_reply += f"• 時間：`{t.get('execute_at')}`\n  目標：{t.get('url')}\n  狀態：`{t.get('status')}`\n\n"
                    await client.post(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage", json={
                        "chat_id": chat_id,
                        "text": t_reply,
                        "parse_mode": "Markdown"
                    })

        if last_update_id > 0:
            await client.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates", params={"offset": last_update_id + 1})

    except Exception as e:
        print(f"處理 Telegram 互動失敗: {e}")

    return settings, products, scheduled_tasks, updated, trigger_immediate_check

async def main():
    print("=" * 60)
    print("🚀 啟動 GitHub Actions 雲端巡檢作業 (Telegram 全功能旗艦版)...")
    now_taipei = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    print(f"🕒 執行時間 (台灣時間): {now_taipei}")
    print("=" * 60)

    settings, products, scheduled_tasks = load_config_and_products()

    async with httpx.AsyncClient(timeout=30.0) as client:
        # 1. 處理 Telegram 傳來的各項設定與指令
        settings, products, scheduled_tasks, tg_updated, trigger_immediate = await process_telegram_incoming_messages(
            client, settings, products, scheduled_tasks
        )

        # 2. 檢查總開關 (若為手動 /check 則強制執行)
        if not settings.get("is_active", True) and not trigger_immediate:
            print("⏸️ 系統設定為停用中 (is_active = false)，跳過本次巡檢。")
            if tg_updated:
                save_config_and_products(settings, products, scheduled_tasks)
            sys.exit(0)

        in_quiet = is_in_quiet_hours(settings)
        if in_quiet and not trigger_immediate:
            print("🌙 目前處於夜間靜音時段，略過訊息推播以防打擾。")

        # 3. 逐一巡檢商品
        in_stock_found = []
        oos_found = []

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

        # 若使用者敲 /check，主動回傳巡檢結果總覽
        if trigger_immediate:
            res_msg = f"🏁 **【立即巡檢完成報告】**\n\n"
            for it in products:
                res_msg += f"• **{it.get('name')}**：`{it.get('last_status')}`\n"
            res_msg += f"\n⏰ 檢查時間：`{now_taipei}`"
            await send_telegram_alert(client, "📋 **巡檢報告**", res_msg)

        # 4. 存檔回寫
        save_config_and_products(settings, products, scheduled_tasks)

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

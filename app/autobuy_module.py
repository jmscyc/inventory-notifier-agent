#!/usr/bin/env python3
"""
【自動登入、自動加購物車與結帳模組 (Auto-Cart & Auto-Checkout)】

架構原則：
1. 分階段降級容錯（每一段失敗至少保留前段正常執行與即時通知）：
   - Stage 1: 庫存偵測成功
   - Stage 2: 自動登入與 Session/Cookie 持久化（若失敗 -> 降級為純通知）
   - Stage 3: 自動加入購物車（若失敗 -> 降級為通知有現貨，附帶商品頁手動加車）
   - Stage 4: 自動導向並推進至結帳確認頁（若失敗 -> 降級為通知已加入購物車，提供一鍵結帳直達連結）
2. 支援 Cookie 匯入 (Session 持久化) 與帳密登入雙軌模式。
3. 支援 Playwright 瀏覽器自動化以及輕量 HTTP Session 雙引擎。
"""

import asyncio
import datetime
import json
import os
import re
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

import httpx

DATA_DIR = Path(__file__).parent.parent
COOKIES_FILE = DATA_DIR / "costco_cookies.json"
AUTOBUY_CONFIG_FILE = DATA_DIR / "autobuy_config.json"

DEFAULT_AUTOBUY_CONFIG = {
    "enabled": False,                  # 是否開啟自動搶購/加車
    "auto_checkout": False,            # 是否嘗試自動推進至最後結帳頁 (若 False 則只加購物車)
    "account_email": "",               # 電商登入帳號 (選填，若有 Cookie 則優先使用 Cookie)
    "account_password": "",            # 電商登入密碼 (選填)
    "use_cookie_session": True,        # 是否優先使用已儲存的 Cookie / Session
    "cart_url": "https://www.costco.com.tw/cart",
    "checkout_url": "https://www.costco.com.tw/checkout",
}

def load_autobuy_config() -> dict:
    if not AUTOBUY_CONFIG_FILE.exists():
        save_autobuy_config(DEFAULT_AUTOBUY_CONFIG)
        return dict(DEFAULT_AUTOBUY_CONFIG)
    try:
        with open(AUTOBUY_CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            for k, v in DEFAULT_AUTOBUY_CONFIG.items():
                if k not in data:
                    data[k] = v
            return data
    except Exception:
        return dict(DEFAULT_AUTOBUY_CONFIG)

def save_autobuy_config(cfg: dict):
    try:
        with open(AUTOBUY_CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存搶購設定失敗: {e}")

def load_saved_cookies() -> dict:
    if not COOKIES_FILE.exists():
        return {}
    try:
        with open(COOKIES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}

def save_cookies(cookies: dict):
    try:
        with open(COOKIES_FILE, "w", encoding="utf-8") as f:
            json.dump(cookies, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存 Cookie 失敗: {e}")


# =====================================================================
# 核心執行引擎：逐步推進、階段式降級與精確回報
# =====================================================================

async def execute_autobuy_flow(target_url: str, product_name: str = "商品") -> Dict:
    """執行自動搶購流水線：
    Returns:
        {
            "stage_reached": 1 | 2 | 3 | 4,
            "status_title": str,
            "status_message": str,
            "action_url": str,
            "action_button_text": str,
            "error_detail": Optional[str]
        }
    """
    cfg = load_autobuy_config()
    if not cfg.get("enabled", False):
        # 尚未啟用自動加車功能，保持 Stage 1 純通知
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【好消息！商品補貨上架】**",
            "status_message": f"📦 **商品**：{product_name}\n🟢 庫存偵測有現貨，請立即點擊下方連結下單！",
            "action_url": target_url,
            "action_button_text": "🛒 前往商品頁面",
            "error_detail": None,
        }

    # 檢查是否具備登入憑證 (Cookie 或 帳號密碼)
    cookies = load_saved_cookies()
    has_credentials = bool(cookies) or bool(cfg.get("account_email") and cfg.get("account_password"))

    if not has_credentials:
        # 降級 Stage 1: 未設定憑證，通知補貨並提醒可設定 Cookie 啟用秒搶
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【商品補貨上架 (尚未設定登入憑證)】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n🟢 偵測到現貨！\n"
                f"⚠️ *提示：您尚未在控制台設定 Costco Cookie 或帳密，因此本次未自動加入購物車，請立即手動點擊購買！*"
            ),
            "action_url": target_url,
            "action_button_text": "🛒 前往搶購",
            "error_detail": "缺少登入 Cookie 或帳號密碼",
        }

    # =================================================================
    # 嘗試 Stage 2 & 3: 加入購物車
    # =================================================================
    cart_success = False
    cart_error = ""

    # 1. 嘗試使用 Playwright 模擬真實瀏覽器點擊加車（若環境支援）
    try:
        from playwright.async_api import async_playwright
        playwright_available = True
    except ImportError:
        playwright_available = False

    if playwright_available:
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    )
                )
                # 載入已存 Cookie
                if cookies:
                    formatted_cookies = []
                    for k, v in cookies.items():
                        formatted_cookies.append({"name": k, "value": str(v), "domain": ".costco.com.tw", "path": "/"})
                    await context.add_cookies(formatted_cookies)

                page = await context.new_page()
                await page.goto(target_url, timeout=25000, wait_until="domcontentloaded")

                # 若未登入且有帳密，嘗試快速登入
                if cfg.get("account_email") and cfg.get("account_password"):
                    login_btn = await page.query_selector("a[href*='login'], .header-login")
                    if login_btn:
                        await login_btn.click()
                        await page.fill("input[type='email'], input#j_username", cfg.get("account_email"))
                        await page.fill("input[type='password'], input#j_password", cfg.get("account_password"))
                        await page.click("button[type='submit'], #loginSubmit")
                        await page.wait_for_timeout(3000)
                        # 儲存登入後 Cookie
                        new_cookies = await context.cookies()
                        save_cookies({c["name"]: c["value"] for c in new_cookies})
                        await page.goto(target_url, timeout=25000, wait_until="domcontentloaded")

                # 尋找並點擊「加入購物車」按鈕
                add_btn = await page.query_selector("#add-to-cart-button, button.add-to-cart, button:has-text('加入購物車')")
                if add_btn and await add_btn.is_enabled():
                    await add_btn.click()
                    await page.wait_for_timeout(2500)
                    cart_success = True

                    # 檢查是否要求進一步推進到結帳 (Stage 4)
                    if cfg.get("auto_checkout", False):
                        checkout_btn = await page.query_selector("a[href*='checkout'], button:has-text('前往結帳')")
                        if checkout_btn:
                            await checkout_btn.click()
                            await page.wait_for_timeout(3000)
                            await browser.close()
                            return {
                                "stage_reached": 4,
                                "status_title": "🚀 **【已自動加入購物車並抵達結帳頁！】**",
                                "status_message": (
                                    f"📦 **商品**：{product_name}\n"
                                    f"✅ **已為您自動將商品加入購物車，並已為您切換至結帳確認頁面！**\n\n"
                                    f"⚡ 請點擊下方按鈕確認付款金額與授權扣款！"
                                ),
                                "action_url": cfg.get("checkout_url", "https://www.costco.com.tw/checkout"),
                                "action_button_text": "💳 前往授權結帳付款",
                                "error_detail": None,
                            }
                else:
                    cart_error = "頁面上未找到可點擊的加入購物車按鈕"
                await browser.close()
        except Exception as e:
            cart_error = f"瀏覽器自動化例外: {str(e)}"

    # 2. 若 Playwright 不可用或執行失敗，嘗試輕量級 HTTP API /cart/add
    if not cart_success:
        try:
            match_code = re.search(r'/p/(\d+)', target_url)
            product_code = match_code.group(1) if match_code else ""
            if product_code:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                    "Referer": target_url,
                    "X-Requested-With": "XMLHttpRequest",
                }
                async with httpx.AsyncClient(headers=headers, cookies=cookies, timeout=12.0) as client:
                    api_resp = await client.post(
                        "https://www.costco.com.tw/cart/add",
                        data={"productCodePost": product_code, "qty": "1"},
                    )
                    if api_resp.status_code in [200, 302] and "cart" in api_resp.text.lower():
                        cart_success = True
                    else:
                        cart_error = f"HTTP 加車回應狀態 {api_resp.status_code}"
        except Exception as e:
            cart_error = f"HTTP 加車例外: {str(e)}"

    # =================================================================
    # 依執行成果降級回報
    # =================================================================
    if cart_success:
        # 成功 Stage 3: 已加入購物車，但未做最後自動刷卡（安全模式），指引 15 分鐘內一鍵結帳
        return {
            "stage_reached": 3,
            "status_title": "⚡ **【已自動放入購物車！請把握時間結帳】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n"
                f"🛒 **系統已成功將此商品自動鎖入您的購物車！**\n\n"
                f"⏰ **請在 15 分鐘內點擊下方按鈕完成結帳確認**，以免庫存被系統釋出！"
            ),
            "action_url": cfg.get("cart_url", "https://www.costco.com.tw/cart"),
            "action_button_text": "🛍️ 直達購物車結帳",
            "error_detail": None,
        }
    else:
        # 降級 Stage 1: 自動加購物車失敗，保留商品補貨通知，附上手動直達連結
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【商品補貨上架 (自動加車未果，請手動搶購)】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n"
                f"🟢 **確認目前有現貨！**\n\n"
                f"⚠️ *自動加入購物車未果 ({cart_error or '電商防護或需重新登入'})，已為您降級為快速手動通道，請立即點擊搶購！*"
            ),
            "action_url": target_url,
            "action_button_text": "🛒 前往商品頁面手動搶購",
            "error_detail": cart_error,
        }

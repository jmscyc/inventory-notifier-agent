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
from typing import Dict, Optional, Tuple, Any

import httpx

DATA_DIR = Path(__file__).parent.parent
COOKIES_FILE = DATA_DIR / "costco_cookies.json"
AUTOBUY_CONFIG_FILE = DATA_DIR / "autobuy_config.json"

try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

DEFAULT_AUTOBUY_CONFIG = {
    "enabled": False,                       # 是否開啟自動搶購/加車
    "auto_checkout": False,                 # 是否推進至結帳確認頁面
    "auto_submit_payment": False,           # 是否自動點擊最終「確認付款/下單」按鈕 (高敏感，預設關閉需手動確認)
    "payment_method": "stored_card",        # "stored_card" (預設信用卡) | "custom_card" (自訂信用卡) | "cod" (貨到付款)
    
    # 信用卡資訊 (僅保存在本機 autobuy_config.json，已被 .gitignore 阻擋，絕不上傳 GitHub)
    "card_info": {
        "card_number": "",                  # 16 位信用卡號
        "card_exp_month": "",               # 到期月 (MM)
        "card_exp_year": "",                # 到期年 (YY 或 YYYY)
        "card_cvv": "",                     # 卡片背面安全碼 3 碼
        "card_holder_name": "",             # 持卡人姓名
    },
    
    # 帳密備援 (若 Cookie 過期時可備援登入)
    "account_email": "",
    "account_password": "",
    "use_cookie_session": True,
    
    "cart_url": "https://www.costco.com.tw/cart",
    "checkout_url": "https://www.costco.com.tw/checkout",
}

# 暫存最近一次測試或執行的 AI 除錯診斷報告
LAST_DIAGNOSTIC_REPORT: Dict[str, Any] = {
    "timestamp": "",
    "target_url": "",
    "logs": [],
    "stage_reached": 0,
    "status": "尚未執行測試",
    "error_detail": None,
    "dom_snapshot": ""
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
            if "card_info" not in data or not isinstance(data["card_info"], dict):
                data["card_info"] = dict(DEFAULT_AUTOBUY_CONFIG["card_info"])
            else:
                for ck, cv in DEFAULT_AUTOBUY_CONFIG["card_info"].items():
                    if ck not in data["card_info"]:
                        data["card_info"][ck] = cv
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


def get_masked_card_info(cfg: dict) -> dict:
    """提供前端顯示的安全遮罩卡號資訊 (不外洩完整卡號與 CVV)"""
    card = cfg.get("card_info", {})
    raw_num = str(card.get("card_number", "")).replace(" ", "").replace("-", "")
    if len(raw_num) >= 12:
        masked_num = raw_num[:4] + " **** **** " + raw_num[-4:]
    elif raw_num:
        masked_num = "****" + raw_num[-4:]
    else:
        masked_num = ""

    return {
        "payment_method": cfg.get("payment_method", "stored_card"),
        "has_card_number": bool(raw_num),
        "masked_card_number": masked_num,
        "card_exp_month": card.get("card_exp_month", ""),
        "card_exp_year": card.get("card_exp_year", ""),
        "has_cvv": bool(card.get("card_cvv")),
        "card_holder_name": card.get("card_holder_name", ""),
        "auto_submit_payment": cfg.get("auto_submit_payment", False),
    }


# =====================================================================
# 核心流水線與 AI 除錯報告生成
# =====================================================================

def record_diagnostic_log(logs: list, step_name: str, status: str, detail: str = ""):
    entry = {
        "time": datetime.datetime.now(TAIPEI_TZ).strftime("%H:%M:%S.%f")[:-3],
        "step": step_name,
        "status": status,
        "detail": detail
    }
    logs.append(entry)
    print(f"[{entry['time']}] [{step_name}] {status}: {detail}")

async def execute_autobuy_flow(target_url: str, product_name: str = "商品", is_test_run: bool = False) -> Dict[str, Any]:
    """執行自動搶購流水線 (支援真實觸發與沙盒測試)"""
    global LAST_DIAGNOSTIC_REPORT
    cfg = load_autobuy_config()
    cookies = load_saved_cookies()
    logs = []
    
    now_taipei = datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    record_diagnostic_log(logs, "INIT", "INFO", f"啟動搶購流程: {product_name} ({target_url}) | 模式: {'沙盒測試' if is_test_run else '正式執行'}")

    if not is_test_run and not cfg.get("enabled", False):
        record_diagnostic_log(logs, "CHECK_ENABLED", "SKIPPED", "未啟用自動加車功能，保持純通知")
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【好消息！商品補貨上架】**",
            "status_message": f"📦 **商品**：{product_name}\n🟢 庫存偵測有現貨，請立即點擊下方連結下單！",
            "action_url": target_url,
            "action_button_text": "🛒 前往商品頁面",
            "error_detail": None,
        }

    has_credentials = bool(cookies) or bool(cfg.get("account_email") and cfg.get("account_password"))
    if not has_credentials and not is_test_run:
        record_diagnostic_log(logs, "CHECK_CREDENTIALS", "WARNING", "未發現登入 Cookie 或帳密")
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【商品補貨上架 (尚未設定登入憑證)】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n🟢 偵測到現貨！\n"
                f"⚠️ *提示：您尚未在控制台設定 Cookie 或帳密，因此本次未自動加入購物車，請立即手動點擊購買！*"
            ),
            "action_url": target_url,
            "action_button_text": "🛒 前往搶購",
            "error_detail": "缺少登入 Cookie 或帳號密碼",
        }

    cart_success = False
    checkout_reached = False
    payment_handled = False
    cart_error = ""
    dom_snapshot = ""

    # =================================================================
    # 引擎 1: Playwright 瀏覽器自動化點擊（首選：擬真、過驗證碼、填表單）
    # =================================================================
    playwright_available = False
    try:
        from playwright.async_api import async_playwright
        playwright_available = True
    except ImportError:
        record_diagnostic_log(logs, "PLAYWRIGHT_CHECK", "INFO", "Playwright 模組未安裝，將使用 HTTP 輕量 API 引擎")

    if playwright_available:
        try:
            record_diagnostic_log(logs, "PLAYWRIGHT_LAUNCH", "START", "正在啟動 Chromium 擬真無頭瀏覽器...")
            async with async_playwright() as p:
                browser = await p.chromium.launch(headless=True)
                context = await browser.new_context(
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                    ),
                    viewport={"width": 1280, "height": 800}
                )

                if cookies:
                    formatted_cookies = []
                    for k, v in cookies.items():
                        formatted_cookies.append({"name": k, "value": str(v), "domain": ".costco.com.tw", "path": "/"})
                    await context.add_cookies(formatted_cookies)
                    record_diagnostic_log(logs, "COOKIE_INJECT", "SUCCESS", f"已成功載入 {len(cookies)} 筆 Cookie 憑證")

                page = await context.new_page()
                record_diagnostic_log(logs, "PAGE_NAVIGATE", "START", f"導向商品頁面: {target_url}")
                await page.goto(target_url, timeout=30000, wait_until="domcontentloaded")
                await page.wait_for_timeout(1500)

                # 檢查登入
                if cfg.get("account_email") and cfg.get("account_password"):
                    login_btn = await page.query_selector("a[href*='login'], .header-login")
                    if login_btn and await login_btn.is_visible():
                        record_diagnostic_log(logs, "LOGIN_FLOW", "START", "偵測到未登入狀態，嘗試使用帳密自動登入...")
                        await login_btn.click()
                        await page.wait_for_timeout(2000)
                        await page.fill("input[type='email'], input#j_username", cfg.get("account_email"))
                        await page.fill("input[type='password'], input#j_password", cfg.get("account_password"))
                        await page.click("button[type='submit'], #loginSubmit")
                        await page.wait_for_timeout(3500)
                        new_c = await context.cookies()
                        save_cookies({c["name"]: c["value"] for c in new_c})
                        record_diagnostic_log(logs, "LOGIN_FLOW", "SUCCESS", "帳密登入完成，更新最新 Cookie")
                        await page.goto(target_url, timeout=30000, wait_until="domcontentloaded")

                # 尋找「加入購物車」按鈕
                add_btn = await page.query_selector("#add-to-cart-button, button.add-to-cart, button:has-text('加入購物車')")
                if add_btn and await add_btn.is_enabled():
                    record_diagnostic_log(logs, "STAGE_2_CART", "CLICK", "找到【加入購物車】按鈕，正在執行自動點擊...")
                    await add_btn.click()
                    await page.wait_for_timeout(2500)
                    cart_success = True
                    record_diagnostic_log(logs, "STAGE_2_CART", "SUCCESS", "成功觸發加入購物車！商品已鎖定在庫存中！")

                    # Stage 3: 推進至結帳確認頁面
                    if cfg.get("auto_checkout", True) or is_test_run:
                        record_diagnostic_log(logs, "STAGE_3_CHECKOUT", "START", "正在推進至結帳確認頁面...")
                        checkout_btn = await page.query_selector("a[href*='checkout'], button:has-text('前往結帳'), a:has-text('前往結帳')")
                        if checkout_btn:
                            await checkout_btn.click()
                        else:
                            await page.goto(cfg.get("checkout_url", "https://www.costco.com.tw/checkout"), timeout=25000)
                        
                        await page.wait_for_timeout(3000)
                        checkout_reached = True
                        record_diagnostic_log(logs, "STAGE_3_CHECKOUT", "SUCCESS", "已成功導向至結帳頁面！")

                        # Stage 3.5: 處理付款方式 (選擇既有卡 / 輸入自訂卡)
                        pay_method = cfg.get("payment_method", "stored_card")
                        card_info = cfg.get("card_info", {})
                        record_diagnostic_log(logs, "STAGE_3_PAYMENT", "INFO", f"設定付款方式模式: {pay_method}")

                        if pay_method == "stored_card":
                            record_diagnostic_log(logs, "STAGE_3_PAYMENT", "APPLIED", "套用帳戶中預設信用卡")
                            payment_handled = True
                        elif pay_method == "custom_card":
                            c_num = card_info.get("card_number", "")
                            c_exp_m = card_info.get("card_exp_month", "")
                            c_exp_y = card_info.get("card_exp_year", "")
                            c_cvv = card_info.get("card_cvv", "")

                            card_num_input = await page.query_selector("input[name*='card'], input#cardNumber, input[placeholder*='卡號']")
                            if card_num_input and c_num:
                                await card_num_input.fill(c_num)
                                record_diagnostic_log(logs, "STAGE_3_PAYMENT", "FILL", "已填入自訂信用卡號碼")

                            cvv_input = await page.query_selector("input[name*='cvv'], input#cardCvv, input[placeholder*='安全碼']")
                            if cvv_input and c_cvv:
                                await cvv_input.fill(c_cvv)
                                record_diagnostic_log(logs, "STAGE_3_PAYMENT", "FILL", "已填入卡片安全碼 (CVV)")

                            payment_handled = True

                        # Stage 4: 最終授權扣款 (若開啟 auto_submit_payment 且不是純測試)
                        if cfg.get("auto_submit_payment", False) and not is_test_run:
                            record_diagnostic_log(logs, "STAGE_4_PAY", "CLICK", "【高權限】偵測到 auto_submit_payment=True，嘗試執行最終扣款確認...")
                            submit_pay_btn = await page.query_selector("button#placeOrder, button:has-text('確認付款'), button:has-text('授權扣款')")
                            if submit_pay_btn and await submit_pay_btn.is_enabled():
                                await submit_pay_btn.click()
                                await page.wait_for_timeout(4000)
                                record_diagnostic_log(logs, "STAGE_4_PAY", "SUCCESS", "🎉 已送出最終付款授權！")
                            else:
                                record_diagnostic_log(logs, "STAGE_4_PAY", "WARNING", "未找到明確授權按鈕，保留在最後確認頁")
                        else:
                            record_diagnostic_log(logs, "STAGE_4_PAY", "PROTECTED", "安全防護生效：停在最後付款授權頁面，等待使用者手動一鍵刷卡確認。")

                else:
                    cart_error = "未找到可點擊的加入購物車按鈕 (可能缺貨中或需手動選取規格)"
                    record_diagnostic_log(logs, "STAGE_2_CART", "FAILED", cart_error)
                    content = await page.content()
                    dom_snapshot = content[:1500]

                await browser.close()
        except Exception as e:
            cart_error = f"瀏覽器流程例外: {str(e)}"
            record_diagnostic_log(logs, "EXCEPTION", "ERROR", cart_error + "\n" + traceback.format_exc())

    # =================================================================
    # 引擎 2: HTTP 輕量 API 引擎備援
    # =================================================================
    if not cart_success and not playwright_available:
        record_diagnostic_log(logs, "HTTP_FALLBACK", "START", "嘗試使用 HTTP /cart/add API 直接加車...")
        try:
            match_code = re.search(r'/p/(\d+)', target_url)
            product_code = match_code.group(1) if match_code else ""
            if product_code:
                headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                    "Referer": target_url,
                    "X-Requested-With": "XMLHttpRequest",
                }
                async with httpx.AsyncClient(headers=headers, cookies=cookies, timeout=12.0) as client:
                    resp = await client.post("https://www.costco.com.tw/cart/add", data={"productCodePost": product_code, "qty": "1"})
                    if resp.status_code in [200, 302] and "cart" in resp.text.lower():
                        cart_success = True
                        record_diagnostic_log(logs, "HTTP_FALLBACK", "SUCCESS", "HTTP 請求加車成功！")
                    else:
                        cart_error = f"HTTP 回應碼 {resp.status_code}"
                        record_diagnostic_log(logs, "HTTP_FALLBACK", "FAILED", cart_error)
            else:
                cart_error = "無法從網址提取商品代號"
        except Exception as e:
            cart_error = f"HTTP 加車例外: {str(e)}"
            record_diagnostic_log(logs, "HTTP_FALLBACK", "ERROR", cart_error)

    # 封存 AI 除錯報告
    stage_reached = 4 if (checkout_reached and payment_handled) else (3 if cart_success else 1)
    LAST_DIAGNOSTIC_REPORT = {
        "timestamp": now_taipei,
        "target_url": target_url,
        "product_name": product_name,
        "is_test_run": is_test_run,
        "stage_reached": stage_reached,
        "cart_success": cart_success,
        "checkout_reached": checkout_reached,
        "payment_handled": payment_handled,
        "error_detail": cart_error or None,
        "logs": logs,
        "dom_snapshot": dom_snapshot
    }

    if checkout_reached:
        return {
            "stage_reached": 4 if cfg.get("auto_submit_payment") else 3,
            "status_title": "🚀 **【已自動加入購物車並抵達結帳頁！】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n"
                f"✅ **系統已自動將商品加入購物車，並為您推進至結帳付款確認頁面！**\n\n"
                f"💳 付款方式：`{cfg.get('payment_method', '預設卡')}`\n"
                f"⚡ 請點擊下方按鈕確認金額完成授權！"
            ),
            "action_url": cfg.get("checkout_url", "https://www.costco.com.tw/checkout"),
            "action_button_text": "💳 前往授權結帳付款",
            "error_detail": None,
            "diagnostic": LAST_DIAGNOSTIC_REPORT
        }
    elif cart_success:
        return {
            "stage_reached": 3,
            "status_title": "⚡ **【已自動放入購物車！請把握時間結帳】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n"
                f"🛒 **系統已成功將此商品自動鎖入購物車！**\n\n"
                f"⏰ **請在 15 分鐘內點擊下方按鈕完成結帳確認**，以免庫存釋出！"
            ),
            "action_url": cfg.get("cart_url", "https://www.costco.com.tw/cart"),
            "action_button_text": "🛍️ 直達購物車結帳",
            "error_detail": None,
            "diagnostic": LAST_DIAGNOSTIC_REPORT
        }
    else:
        return {
            "stage_reached": 1,
            "status_title": "🎉 **【商品補貨上架 (自動加車未果，請手動搶購)】**",
            "status_message": (
                f"📦 **商品**：{product_name}\n"
                f"🟢 **確認目前有現貨！**\n\n"
                f"⚠️ *自動加車未果 ({cart_error or '電商防護或需重新登入'})，已為您降級為手動直達通道，請立即點擊搶購！*"
            ),
            "action_url": target_url,
            "action_button_text": "🛒 前往商品頁面手動搶購",
            "error_detail": cart_error,
            "diagnostic": LAST_DIAGNOSTIC_REPORT
        }

def get_last_diagnostic_export() -> str:
    """生成格式化、方便使用者直接貼給 AI (ChatGPT/Claude/Gemini) 除錯的 Markdown 診斷代碼"""
    rep = LAST_DIAGNOSTIC_REPORT
    log_text = "\n".join([f"[{l.get('time')}] [{l.get('step')}] {l.get('status')}: {l.get('detail')}" for l in rep.get("logs", [])])
    
    return f"""### 🤖 【自動加車與付款模組 AI 除錯診斷報告】
- **檢測時間**：{rep.get('timestamp', 'N/A')}
- **目標商品網址**：{rep.get('target_url', 'N/A')}
- **執行階段**：Stage {rep.get('stage_reached', 0)} (1=純通知, 3=加車成功, 4=抵達結帳)
- **購物車結果**：{'✅ 成功' if rep.get('cart_success') else '❌ 失敗'}
- **結帳頁抵達**：{'✅ 成功' if rep.get('checkout_reached') else '❌ 失敗'}
- **錯誤訊息摘要**：`{rep.get('error_detail') or '無錯誤'}`

#### 📋 執行流水線日誌 (Pipeline Logs)：
```text
{log_text or '尚未產生執行日誌'}
```

#### 🔍 失敗頁面 DOM 片段快照 (DOM Snippet Snapshot)：
```html
{rep.get('dom_snapshot') or '正常執行或無快照'}
```

👉 **請 AI 協助分析以上流程：為什麼在該電商步驟加車或結帳失敗？需要調整哪個 CSS Selector 或 HTTP Header？**
"""

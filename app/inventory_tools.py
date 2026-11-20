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

import datetime
import os
import re
from bs4 import BeautifulSoup
import httpx


def check_product_inventory(url: str) -> dict:
    """檢查指定電商或產品頁面的庫存狀態。

    支援抓取網頁內容，自動辨識常見庫存狀態（有貨/缺貨/售完）及商品名稱與標題。

    Args:
        url: 欲檢查的產品網址（例如 https://example.com/product/123）。

    Returns:
        包含 url、title、in_stock (True/False/None)、status_description、checked_at 的字典。
    """
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-TW,zh;q=0.9,en-US;q=0.8,en;q=0.7",
    }

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with httpx.Client(follow_redirects=True, timeout=15.0, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            html_content = response.text
    except Exception as e:
        return {
            "url": url,
            "title": "無法取得頁面",
            "in_stock": None,
            "status_description": f"抓取頁面失敗: {str(e)}",
            "checked_at": now_str,
        }

    soup = BeautifulSoup(html_content, "html.parser")

    # 提取頁面標題
    title = ""
    if soup.title and soup.title.string:
        title = soup.title.string.strip()
    elif soup.h1:
        title = soup.h1.get_text().strip()
    else:
        title = url

    # 移除 script, style 避免誤判
    for tag in soup(["script", "style", "noscript"]):
        tag.decompose()

    page_text = soup.get_text(separator=" ", strip=True).lower()

    # 特殊電商規則優化（例如 Costco 好市多）
    if "costco.com" in url:
        # 1. 檢查是否存在明確的 PDP 缺貨按鈕：<button class="... outOfStock" disabled="disabled">缺貨</button>
        costco_oos_btn = soup.find(
            lambda t: t.name == "button"
            and ("outOfStock" in t.get("class", []) or "缺貨" in t.get_text())
        )
        # 2. 檢查主商品加入購物車按鈕：<button id="add-to-cart-button" ...>加入購物車</button>
        costco_pdp_add_btn = soup.find("button", id="add-to-cart-button")

        # 3. 檢查主商品數量選擇器 (具備 pdp-input class)
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
            status_description = "目前缺貨 (商品頁面顯示『缺貨』按鈕且已停用加入購物車)"
        elif costco_pdp_add_btn and has_qty_selector:
            in_stock = True
            status_description = "有現貨 (確認具備數量增減選擇器及加入購物車按鈕)"
        elif costco_pdp_add_btn:
            in_stock = True
            status_description = "有現貨 (商品頁具備加入購物車按鈕)"
        else:
            in_stock = False
            status_description = "目前缺貨 (未發現此商品專屬之加入購物車與數量選單)"

        return {
            "url": url,
            "title": title,
            "in_stock": in_stock,
            "status_description": status_description,
            "checked_at": now_str,
        }

    # 一般電商通用比對邏輯
    # 常見缺貨關鍵詞
    out_of_stock_patterns = [
        r"缺貨中",
        r"已售完",
        r"售完",
        r"已售罄",
        r"補貨中",
        r"暫無庫存",
        r"庫存不足",
        r"目前無庫存",
        r"out of stock",
        r"sold out",
        r"unavailable",
        r"backorder",
        r"currently unavailable",
    ]

    # 常見有庫存關鍵詞 / 加入購物車
    in_stock_patterns = [
        r"加入購物車",
        r"立即購買",
        r"尚有庫存",
        r"有現貨",
        r"現貨",
        r"庫存充裕",
        r"in stock",
        r"add to cart",
        r"buy now",
        r"available now",
    ]

    # 檢查是否有被 disabled 的購物車或購買按鈕
    disabled_cart = bool(
        soup.find(
            lambda tag: tag.name in ["button", "input", "a"]
            and ("disabled" in tag.attrs or "outOfStock" in tag.get("class", []))
            and any(k in tag.get_text().lower() for k in ["cart", "buy", "購買", "購物車", "缺貨"])
        )
    )

    has_out_of_stock = any(re.search(pat, page_text) for pat in out_of_stock_patterns)
    has_in_stock = any(re.search(pat, page_text) for pat in in_stock_patterns)

    # 綜合研判
    if disabled_cart or (has_out_of_stock and not has_in_stock):
        in_stock = False
        status_description = "目前缺貨 / 售完中 (Out of stock，購買按鈕停用或顯示缺貨)"
    elif has_in_stock and not has_out_of_stock:
        in_stock = True
        status_description = "目前有現貨 / 可加入購物車 (In stock)"
    elif has_out_of_stock and has_in_stock:
        if disabled_cart:
            in_stock = False
            status_description = "缺貨 (購買按鈕處於停用或缺貨狀態)"
        else:
            in_stock = False
            status_description = "可能缺貨 (頁面偵測到缺貨字樣)"
    else:
        in_stock = True
        status_description = "頁面正常載入，未發現缺貨字樣"

    return {
        "url": url,
        "title": title,
        "in_stock": in_stock,
        "status_description": status_description,
        "checked_at": now_str,
    }


def send_telegram_notification(
    message: str,
    bot_token: str = "",
    chat_id: str = "",
) -> dict:
    """透過 Telegram Bot 發送即時通報訊息。

    Args:
        message: 要發送的訊息文字內容（支援 Markdown）。
        bot_token: Telegram Bot Token（若未填則自動讀取環境變數 TELEGRAM_BOT_TOKEN）。
        chat_id: 目標聊天室或使用者 ID（若未填則自動讀取環境變數 TELEGRAM_CHAT_ID）。

    Returns:
        包含 success (bool) 及 detail (str) 的字典。
    """
    token = bot_token.strip() or os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    target_chat_id = chat_id.strip() or os.getenv("TELEGRAM_CHAT_ID", "").strip()

    # 檢查是否為佔位符或空值
    is_placeholder_token = not token or token.startswith("your-") or "BOT_TOKEN" in token
    is_placeholder_chat_id = not target_chat_id or target_chat_id.startswith("your-") or "CHAT_ID" in target_chat_id

    # 防呆校驗：Telegram Bot Token 格式通常為 <數字>:<一串字元>
    token_format_valid = bool(re.match(r"^\d+:[A-Za-z0-9_-]{30,}$", token)) if token else False

    # 若未設定或使用預設佔位符，啟動【模擬模式 (Mock Mode)】
    if is_placeholder_token or is_placeholder_chat_id or not token_format_valid:
        guide_steps = []
        if is_placeholder_token or not token_format_valid:
            guide_steps.append("1. 前往 Telegram 搜尋 `@BotFather`，發送 `/newbot` 建立機器人並獲取 Bot Token。")
        if is_placeholder_chat_id:
            guide_steps.append("2. 搜尋 `@userinfobot` 獲取您個人的數字 Chat ID。")
        guide_steps.append("3. 將兩者填入 `simple-agent/.env` (或直接在對話中提供)，即可無縫切換為真實發送！")

        guide_msg = "\n".join(guide_steps)
        return {
            "success": True,
            "mode": "mock",
            "is_simulation": True,
            "simulated_message": message,
            "detail": (
                "【模擬發送成功 (Mock Mode)】\n"
                "目前處於安全模擬模式，訊息已成功生成並驗證。\n\n"
                f"📋 準備發送的內容預覽：\n{message}\n\n"
                f"⚙️ 啟用真實推播設定引導：\n{guide_msg}"
            ),
        }

    api_url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": message,
        "parse_mode": "Markdown",
        "disable_web_page_preview": False,
    }

    try:
        with httpx.Client(timeout=10.0) as client:
            resp = client.post(api_url, json=payload)
            resp.raise_for_status()
            data = resp.json()
            if data.get("ok"):
                return {
                    "success": True,
                    "mode": "live",
                    "detail": "Telegram 訊息已成功發送！",
                }
            else:
                return {
                    "success": False,
                    "mode": "live",
                    "detail": f"Telegram API 回傳錯誤: {data.get('description')}",
                }
    except Exception as e:
        return {
            "success": False,
            "mode": "live",
            "detail": f"發送 Telegram 訊息時發生連線異常: {str(e)}",
        }

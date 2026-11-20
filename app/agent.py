# ruff: noqa
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
from zoneinfo import ZoneInfo

from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.models import Gemini
from google.genai import types


from app.inventory_tools import check_product_inventory, send_telegram_notification
from app.firestore_tools import (
    list_tracked_products,
    get_tracked_product,
    add_or_update_tracked_product,
    remove_tracked_product,
)
from app.product_api_tools import search_online_catalog


MODEL = "gemini-3.8-flash"


def get_weather(query: str) -> str:
    """Simulates a web search. Use it get information on weather.

    Args:
        query: A string containing the location to get weather information for.

    Returns:
        A string with the simulated weather information for the queried location.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        return "It's 60 degrees and foggy."
    return "It's 90 degrees and sunny."


def get_current_time(query: str) -> str:
    """Simulates getting the current time for a city.

    Args:
        city: The name of the city to get the current time for.

    Returns:
        A string with the current time information.
    """
    if "sf" in query.lower() or "san francisco" in query.lower():
        tz_identifier = "America/Los_Angeles"
    else:
        return f"Sorry, I don't have timezone information for query: {query}."

    tz = ZoneInfo(tz_identifier)
    now = datetime.datetime.now(tz)
    return f"The current time for query {query} is {now.strftime('%Y-%m-%d %H:%M:%S %Z%z')}"


INSTRUCTION = """你是專業的「商品庫存查詢與 Telegram 提醒助理」。
你的主要任務是：
1. 【自動建立追蹤與庫存檢查】：
   當使用者輸入或提供任何商品/電商頁面網址時：
   - 立即使用 `check_product_inventory` 工具檢查該頁面目前的即時庫存。
   - 自動調用 `add_or_update_tracked_product` 將此商品加入資料庫追蹤清單中（商品名稱取檢查到的品名，並設定 `notify_telegram=True`）。
   - 向使用者回報即時庫存狀況與追蹤建立結果。
   - 若當前即有現貨 (in_stock=True)，同時調用 `send_telegram_notification` 工具立即發送 Telegram 到貨通知！
2. 【查詢追蹤清單】：
   當使用者詢問「目前追蹤的商品」、「查看庫存清單」、「追蹤清單」時，使用 `list_tracked_products` 查詢 Firestore 並清楚條列各商品的名稱、ID、庫存狀態與網址。
3. 【取消追蹤】：
   當使用者要求「取消追蹤」、「刪除追蹤」某商品或網址時，使用 `remove_tracked_product` 工具並告知結果。
4. 【線上型錄搜尋】：
   當使用者想搜尋商品市場規格或比價時，可調用 `search_online_catalog`。
5. 請始終以繁體中文回答，語氣親切專業且條理分明。
"""

root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model=MODEL,
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=INSTRUCTION,
    tools=[
        check_product_inventory,
        send_telegram_notification,
        list_tracked_products,
        get_tracked_product,
        add_or_update_tracked_product,
        remove_tracked_product,
        search_online_catalog,
    ],
)

app = App(
    root_agent=root_agent,
    name="app",
)

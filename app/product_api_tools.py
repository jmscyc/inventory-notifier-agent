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

import os
import httpx


def search_online_catalog(query: str, limit: int = 5) -> list:
    """透過免費公開的電商產品資料庫 API (DummyJSON Products API) 搜尋產品即時庫存與規格資訊。

    當使用者想搜尋特定產品、比較價格或查詢現貨數量 (Stock count) 與可用狀態 (Availability status) 時使用。

    Args:
        query: 搜尋關鍵字（例如 'phone', 'laptop', 'airpods', 'perfume'）。
        limit: 回傳最多商品數量，預設為 5。

    Returns:
        包含商品名稱、類別、價格、庫存數量、現貨狀態、品牌與縮圖的清單。
    """
    # 支援透過環境變數自訂 API 端點（預設使用公開免費的 DummyJSON Products API，無需 API Key）
    base_url = os.getenv("PRODUCT_CATALOG_API_URL", "https://dummyjson.com/products/search")
    api_key = os.getenv("PRODUCT_CATALOG_API_KEY", "").strip()

    headers = {"User-Agent": "InventoryNotifierAgent/1.0"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    params = {"q": query.strip(), "limit": min(limit, 10)}

    try:
        with httpx.Client(timeout=10.0, headers=headers) as client:
            resp = client.get(base_url, params=params)
            resp.raise_for_status()
            data = resp.json()
            products = data.get("products", [])

            results = []
            for item in products:
                results.append({
                    "id": item.get("id"),
                    "title": item.get("title"),
                    "brand": item.get("brand", "N/A"),
                    "category": item.get("category"),
                    "price": f"${item.get('price', 0):.2f}",
                    "stock_quantity": item.get("stock", 0),
                    "availability_status": item.get("availabilityStatus", "Unknown"),
                    "in_stock": item.get("stock", 0) > 0,
                    "thumbnail": item.get("thumbnail"),
                })
            return results
    except Exception as e:
        return [{"error": f"呼叫產品資料庫 API 失敗: {str(e)}"}]

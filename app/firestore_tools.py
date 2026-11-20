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
from google.cloud import firestore

# 重要：寫死專案 ID 字串，避免在 Agent Platform 上取到專案編號導致錯誤
PROJECT_ID = "qwiklabs-gcp-03-9012a80ce4ee"
COLLECTION_NAME = "tracked_products"


def _get_db() -> firestore.Client:
    return firestore.Client(project=PROJECT_ID)


def list_tracked_products(category: str = "") -> list:
    """查詢資料庫中目前正在監控的商品清單。

    Args:
        category: (選填) 商品分類篩選，例如 'Gaming' 或 'Electronics'。留空則查詢全部。

    Returns:
        包含所有監控商品資料的清單。
    """
    db = _get_db()
    col_ref = db.collection(COLLECTION_NAME)

    if category:
        query = col_ref.where("category", "==", category.strip())
        docs = query.stream()
    else:
        docs = col_ref.stream()

    results = []
    for doc in docs:
        data = doc.to_dict()
        data["id"] = doc.id
        results.append(data)

    return results


def get_tracked_product(product_id: str) -> dict:
    """根據商品 ID 取得單一監控商品的詳細資訊。

    Args:
        product_id: 商品的文件 ID (例如 'item-ps5-pro')。

    Returns:
        商品詳細資料字典；若未找到則回傳錯誤提示。
    """
    db = _get_db()
    doc_ref = db.collection(COLLECTION_NAME).document(product_id.strip())
    doc = doc_ref.get()

    if not doc.exists:
        return {"error": f"找不到 ID 為 '{product_id}' 的商品資料。"}

    data = doc.to_dict()
    data["id"] = doc.id
    return data


def add_or_update_tracked_product(
    product_id: str,
    name: str,
    target_url: str,
    category: str = "General",
    price: str = "",
    in_stock: bool = False,
    notify_telegram: bool = True,
    status_notes: str = "",
) -> dict:
    """新增或更新要監控的商品資訊至 Firestore。

    Args:
        product_id: 唯一識別碼（例如 'item-airpods-pro' 或自訂 ID）。
        name: 商品名稱。
        target_url: 產品的目標網頁網址。
        category: (選填) 商品類別，預設為 'General'。
        price: (選填) 參考售價或目前價格。
        in_stock: 目前是否有庫存，預設為 False。
        notify_telegram: 有貨時是否發送 Telegram 通知，預設為 True。
        status_notes: 狀態說明或備註。

    Returns:
        包含執行結果狀態與寫入資料的字典。
    """
    db = _get_db()
    doc_ref = db.collection(COLLECTION_NAME).document(product_id.strip())

    now_str = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    data = {
        "id": product_id.strip(),
        "name": name.strip(),
        "target_url": target_url.strip(),
        "category": category.strip(),
        "price": price.strip(),
        "in_stock": in_stock,
        "notify_telegram": notify_telegram,
        "last_checked": now_str,
        "status_notes": status_notes.strip(),
    }

    doc_ref.set(data, merge=True)
    return {
        "success": True,
        "message": f"商品 '{name}' 已成功保存至追蹤清單！",
        "product": data,
    }


def remove_tracked_product(identifier: str) -> dict:
    """從追蹤清單中取消監控並刪除商品。

    Args:
        identifier: 要取消追蹤的商品 ID (例如 'item-ps5-pro') 或商品網址。

    Returns:
        包含 success (bool) 與 message (str) 的執行結果字典。
    """
    db = _get_db()
    col_ref = db.collection(COLLECTION_NAME)

    ident = identifier.strip()
    # 先嘗試直接以 document ID 刪除
    doc_ref = col_ref.document(ident)
    doc = doc_ref.get()
    if doc.exists:
        name = doc.to_dict().get("name", ident)
        doc_ref.delete()
        return {"success": True, "message": f"已成功取消追蹤並刪除商品：{name} ({ident})"}

    # 若未找到，嘗試比對 target_url 或 name
    docs = col_ref.where("target_url", "==", ident).stream()
    deleted_names = []
    for d in docs:
        d_name = d.to_dict().get("name", d.id)
        d.reference.delete()
        deleted_names.append(d_name)

    if deleted_names:
        return {"success": True, "message": f"已成功取消追蹤：{', '.join(deleted_names)}"}

    return {"success": False, "message": f"未在追蹤清單中找到符合 '{identifier}' 的商品。"}

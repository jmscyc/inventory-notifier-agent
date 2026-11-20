#!/usr/bin/env python3
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


def seed_data():
    print(f"Connecting to Firestore for project: {PROJECT_ID}...")
    db = firestore.Client(project=PROJECT_ID)

    items = [
        {
            "id": "item-ps5-pro",
            "name": "PlayStation 5 Pro",
            "category": "Gaming",
            "target_url": "https://www.playstation.com/ps5-pro",
            "in_stock": False,
            "price": "$699.99",
            "notify_telegram": True,
            "target_chat_id": "",
            "last_checked": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status_notes": "缺貨中，等待官方補貨通知",
        },
        {
            "id": "item-switch-oled",
            "name": "Nintendo Switch OLED - White",
            "category": "Gaming",
            "target_url": "https://www.nintendo.com/switch-oled",
            "in_stock": True,
            "price": "$349.99",
            "notify_telegram": True,
            "target_chat_id": "",
            "last_checked": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status_notes": "目前有現貨可供購買",
        },
        {
            "id": "item-pixel-9-pro",
            "name": "Google Pixel 9 Pro 128GB Obsidian",
            "category": "Electronics",
            "target_url": "https://store.google.com/product/pixel_9_pro",
            "in_stock": True,
            "price": "$999.00",
            "notify_telegram": False,
            "target_chat_id": "",
            "last_checked": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "status_notes": "現貨供應中",
        },
    ]

    col_ref = db.collection(COLLECTION_NAME)
    for item in items:
        doc_id = item["id"]
        col_ref.document(doc_id).set(item)
        print(f"✓ Seeded item: {doc_id} -> {item['name']}")

    print(f"\n🎉 Successfully seeded {len(items)} items to '{COLLECTION_NAME}' collection in Firestore!")


if __name__ == "__main__":
    seed_data()

#!/usr/bin/env python3
"""
【預約定時搶登入 / 搶名額 / 搶購任務模組 (Scheduled Trigger Module)】

特色：
1. 支援「指定預約時間 (精準到秒)」或「立即執行」。
2. 支援登入憑證 (Cookie / 帳密) 自動預載。
3. 執行後透過本地推播與 Telegram 即時回報結果。
"""

import asyncio
import datetime
import json
import time
import zoneinfo
from pathlib import Path
from typing import Dict, List, Optional

import httpx

DATA_DIR = Path(__file__).parent.parent
TASKS_FILE = DATA_DIR / "scheduled_tasks.json"
try:
    import zoneinfo
    TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")
except Exception:
    # 支援 Windows 預設未安裝 tzdata 之環境
    TAIPEI_TZ = datetime.timezone(datetime.timedelta(hours=8))

def load_scheduled_tasks() -> List[dict]:
    if not TASKS_FILE.exists():
        return []
    try:
        with open(TASKS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_scheduled_tasks(tasks: List[dict]):
    try:
        with open(TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(tasks, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存預約任務失敗: {e}")

def add_scheduled_task(title: str, target_url: str, execute_at: str, action_type: str = "login_and_open") -> dict:
    tasks = load_scheduled_tasks()
    task_id = f"task_{int(time.time() * 1000)}"
    new_task = {
        "id": task_id,
        "title": title or "未命名預約任務",
        "target_url": target_url,
        "execute_at": execute_at,       # "YYYY-MM-DD HH:MM:SS" 或 "IMMEDIATE"
        "action_type": action_type,     # "login_and_open" | "auto_click"
        "status": "PENDING",            # PENDING | RUNNING | COMPLETED | FAILED
        "result_message": "",
        "created_at": datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }
    tasks.append(new_task)
    save_scheduled_tasks(tasks)
    return new_task

def delete_scheduled_task(task_id: str) -> bool:
    tasks = load_scheduled_tasks()
    new_tasks = [t for t in tasks if t.get("id") != task_id]
    if len(new_tasks) != len(tasks):
        save_scheduled_tasks(new_tasks)
        return True
    return False

#!/usr/bin/env python3
"""
【自訂萬用網頁爬蟲與視覺化助手 (Custom Web Scraper & Rule Engine)】

特色：
1. 支援任意網頁的自訂 CSS 選擇器 / 關鍵字監控。
2. 小白友善的 F12 教學指引解析器：
   - 支援貼入 F12 的「CSS 選擇器 (Copy selector)」
   - 支援貼入 F12 的「OuterHTML 代碼 (Copy outerHTML)」自動提取特徵
3. 支援即時測試預覽抓取結果。
"""

import datetime
import json
import re
import zoneinfo
from pathlib import Path
from typing import Dict, List, Optional

import httpx
from bs4 import BeautifulSoup

DATA_DIR = Path(__file__).parent.parent
RULES_FILE = DATA_DIR / "custom_crawler_rules.json"
TAIPEI_TZ = zoneinfo.ZoneInfo("Asia/Taipei")

def load_custom_rules() -> List[dict]:
    if not RULES_FILE.exists():
        return []
    try:
        with open(RULES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

def save_custom_rules(rules: List[dict]):
    try:
        with open(RULES_FILE, "w", encoding="utf-8") as f:
            json.dump(rules, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"⚠️ 儲存自訂爬蟲規則失敗: {e}")

def add_custom_rule(name: str, target_url: str, selector: str = "", match_text: str = "", check_type: str = "text_contains") -> dict:
    rules = load_custom_rules()
    rule_id = f"rule_{int(datetime.datetime.now().timestamp() * 1000)}"
    new_rule = {
        "id": rule_id,
        "name": name or "自訂監控規則",
        "target_url": target_url.strip(),
        "selector": selector.strip(),
        "match_text": match_text.strip(),
        "check_type": check_type,      # text_contains (文字包含) | element_exists (元素存在)
        "last_status": "未執行",
        "last_checked": "",
        "created_at": datetime.datetime.now(TAIPEI_TZ).strftime("%Y-%m-%d %H:%M:%S")
    }
    rules.append(new_rule)
    save_custom_rules(rules)
    return new_rule

def delete_custom_rule(rule_id: str) -> bool:
    rules = load_custom_rules()
    new_rules = [r for r in rules if r.get("id") != rule_id]
    if len(new_rules) != len(rules):
        save_custom_rules(new_rules)
        return True
    return False

def test_crawl_url(url: str, selector: str = "", match_text: str = "") -> dict:
    """即時測試抓取目標網頁元素，幫助小白確認是否抓對"""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }
    try:
        with httpx.Client(follow_redirects=True, timeout=12.0, headers=headers) as client:
            resp = client.get(url)
            resp.raise_for_status()
            html = resp.text
    except Exception as e:
        return {"success": False, "error": f"連線至網址失敗: {str(e)}"}

    soup = BeautifulSoup(html, "html.parser")
    found_elements = []

    if selector:
        try:
            elements = soup.select(selector)
            for el in elements[:5]:  # 最多取前 5 個
                found_elements.append({
                    "tag": el.name,
                    "text": el.get_text(strip=True)[:100],
                    "html_snippet": str(el)[:150] + ("..." if len(str(el)) > 150 else "")
                })
        except Exception as e:
            return {"success": False, "error": f"CSS 選擇器語法無效: {str(e)}"}
    else:
        # 若未提供 selector，抓取標題與前段文字作為預覽
        found_elements.append({
            "tag": "title",
            "text": soup.title.string.strip() if soup.title else "無標題",
            "html_snippet": "整頁抓取"
        })

    is_matched = False
    if match_text:
        is_matched = match_text.lower() in html.lower()

    return {
        "success": True,
        "page_title": soup.title.string.strip() if soup.title else url,
        "found_count": len(found_elements),
        "elements": found_elements,
        "match_text_found": is_matched if match_text else None
    }

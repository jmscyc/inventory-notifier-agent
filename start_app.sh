#!/usr/bin/env bash
# macOS / Linux 一鍵雙擊啟動腳本

cd "$(dirname "$0")"

echo "======================================================="
echo "  正在啟動【庫存監控與智慧自動化工作台】..."
echo "======================================================="

# 自動安裝依賴套件
pip install -q fastapi uvicorn httpx beautifulsoup4 python-dotenv 2>/dev/null || pip3 install -q fastapi uvicorn httpx beautifulsoup4 python-dotenv

# 自動在預設瀏覽器開啟網頁
if command -v open >/dev/null 2>&1; then
    (sleep 2 && open http://localhost:8000) &
elif command -v xdg-open >/dev/null 2>&1; then
    (sleep 2 && xdg-open http://localhost:8000) &
fi

# 啟動主程式
python3 run_local_app.py || python run_local_app.py

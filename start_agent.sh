#!/usr/bin/env bash
# ==============================================================================
# 🚀 一鍵啟動腳本 (One-Click Start All Services)
# 同時拉起：
#   1. Web 旗艦控制台 + 背景巡檢與搶購排程 (run_local_app.py -> http://localhost:8000)
#   2. Telegram 常駐實體鍵盤與遠端代搶機器人 (telegram_bot_service.py)
# ==============================================================================

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "================================================================="
echo "🛒 正在啟動【電商自動監控、萬用爬蟲與自動代搶旗艦系統】..."
echo "================================================================="

# 檢查是否已在虛擬環境或具備 uv
if command -v uv &> /dev/null; then
    PY_CMD="uv run python3"
else
    PY_CMD="python3"
fi

# 1. 清理舊有重複進程，確保連接埠 8000 與 TG Long Polling 乾淨無衝突
echo "🧹 檢查並釋放歷史背景進程..."
pkill -f "python3 run_local_app.py" 2>/dev/null || true
pkill -f "python3 telegram_bot_service.py" 2>/dev/null || true
sleep 1

# 2. 啟動 Telegram Bot 服務 (背景常駐)
echo "🤖 [1/2] 啟動 Telegram Bot 實體鍵盤與遠端搶購服務..."
nohup $PY_CMD telegram_bot_service.py > tg_bot.log 2>&1 &
TG_PID=$!
echo "   ➔ Telegram Bot 已啟動 (PID: $TG_PID)，日誌記錄於 tg_bot.log"

# 3. 啟動 Web 控制台與排程引擎 (前台/主進程)
echo "🌐 [2/2] 啟動 Web 控制台與核心巡檢引擎..."
echo "   ➔ 瀏覽器請造訪: http://localhost:8000"
echo "================================================================="
echo "💡 提示：按 Ctrl + C 可同時安全停止所有服務。"
echo "================================================================="

trap 'echo ""; echo "🛑 正在安全停止所有服務..."; kill $TG_PID 2>/dev/null || true; exit 0' SIGINT SIGTERM

$PY_CMD run_local_app.py

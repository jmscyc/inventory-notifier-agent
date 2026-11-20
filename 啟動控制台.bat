@echo off
chcp 65001 >nul
title 庫存監控與智慧自動化工作台
cd /d "%~dp0"

echo =======================================================
echo   正在啟動【庫存監控與智慧自動化工作台】...
echo =======================================================
echo.

where python >nul 2>nul
if %errorlevel% neq 0 (
    echo [錯誤] 找不到 Python！請先安裝 Python 並勾選 "Add Python to PATH"。
    echo 下載網址: https://www.python.org/downloads/
    pause
    exit /b
)

echo [1/3] 正在檢查並更新依賴套件...
python -m pip install -q fastapi uvicorn httpx beautifulsoup4 python-dotenv

echo [2/3] 正在啟動後台服務與自動打開瀏覽器...
start "" "http://localhost:8000"

echo [3/3] 服務已就緒！請保持此視窗開啟 (縮小即可)。
echo -------------------------------------------------------
python run_local_app.py

pause

#!/usr/bin/env python3
"""
【打包成單一獨立 EXE 執行檔腳本】
執行本腳本即可透過 PyInstaller 將 run_local_app 打包成 .exe
使用者電腦甚至不需要安裝 Python 即可雙擊執行！
"""

import subprocess
import sys

def build():
    print("正在安裝 PyInstaller...")
    subprocess.run([sys.executable, "-m", "pip", "install", "pyinstaller"], check=True)

    print("開始打包成單一 EXE 執行檔 (包含所有資源與自動開啟瀏覽器)...")
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--name=庫存監控與自動化工作台",
        "--onefile",
        "--clean",
        "run_local_app.py"
    ]
    subprocess.run(cmd, check=True)
    print("==================================================")
    print("✅ 打包完成！請至 dist/ 資料夾取得『庫存監控與自動化工作台.exe』")
    print("==================================================")

if __name__ == "__main__":
    build()

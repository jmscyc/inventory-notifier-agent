# 🛒 電商自動監控、萬用爬蟲與自動代搶旗艦系統 (Inventory & Automation Agent)

支援電商（Costco 好市多、各大購物網站等）商品庫存即時偵測、價格趨勢追蹤、階梯式自動加車與付款結帳、**預約定時搶登入/搶名額**、以及**小白友善的萬用視覺化網頁爬蟲精靈（含 F12 圖文導引）**之智慧代理系統。

---

## ⚡ 核心開發與未來改版維護指南 (必讀！)

> [!IMPORTANT]
> ### 💡 以後如果要下載檔案餵給 AI 改版，要下載哪一個檔案？
> 本系統已實施**「單一檔案全功能高度聚合架構 (Monolithic Architecture)」**，重點功能絕不散落在繁瑣的子資料夾中！
> 
> * **👉 主要改版核心（90% 的功能都在這）：請下載根目錄的 [`run_local_app.py`](run_local_app.py)**
>   - **包含全部功能**：FastAPI Web 控制台、前端 SPA 介面、商品庫存巡檢、自訂爬蟲規則引擎、Playwright 擬真自動搶購與加車、信用卡/帳密處理、防系統休眠鎖 (WakeLock)、斷線自癒 (Self-Healing)、價格歷史時序記錄與預約定時任務。
>   - **給 AI 指令**：「這是我全部的核心程式碼 `run_local_app.py`，請幫我修改/增加 ... 功能。」AI 只要看這單一檔案就能全面掌握並直接改好！
> 
> * **👉 Telegram 機器人相關改版：請下載根目錄的 [`telegram_bot_service.py`](telegram_bot_service.py)**
>   - 負責 Telegram 打字框底部 4 鍵常駐鍵盤、貼網址帶規格解析、遠端代搶按鈕等對話交互邏輯。
> 
> * **👉 一鍵啟動腳本：根目錄的 [`start_agent.sh`](start_agent.sh)**
>   - 一鍵啟動 Web 與 Telegram 背景守護，自動平滑重啟。

---

## 🚀 快速啟動指南 (Quick Start)

### 1. 方式 A：一鍵啟動（推薦）
在終端機執行根目錄的啟動腳本即可同時跑起 Web 介面與 Telegram 機器人：
```bash
./start_agent.sh
```

### 2. 方式 B：手動啟動
```bash
# 啟動 Web 控制台 (包含全功能核心、防休眠、排程巡檢、自動搶購)
python3 run_local_app.py

# 啟動 Telegram 機器人服務 (若有需要)
python3 telegram_bot_service.py
```

* **🌐 本地控制台網址**：[http://localhost:8000](http://localhost:8000)
* **🤖 Telegram 機器人**：`@jmsaitry_bot`

---

## 🌟 核心功能一覽

1. **📦 商品庫存監控與價格趨勢追蹤**：
   - 貼網址自動解析商品名稱與售價，歷史價格變動自動記錄並提供走勢圖。
   - 支援心理預算上限：若補貨價格超過預算，自動攔截不誤扣款。
2. **☕ 跨平台防休眠守護 (SystemWakeLock)**：
   - 支援 Windows (`SetThreadExecutionState`)、macOS (`caffeinate`)、Linux (`systemd-inhibit`)。
   - 地端背景執行期間絕不睡眠中斷，關閉程式時安全平滑釋放。
3. **🌐 網路瞬斷自癒 (Self-Healing)**：
   - WiFi 斷線或電商 502/504 異常時，自動啟用指數退避重試，復通即刻自動恢復。
4. **⚡ 擬真自動搶購流水線 (Playwright Autobuy)**：
   - 支援規格/尺寸優先選擇（如 `L號`、`黑色`）。
   - 會員帳密自動模擬鍵入登入。
   - 支援信用卡預填與安全沙盒測試（絕不誤刷）。
5. **🧙 萬用爬蟲精靈 (F12 圖文指引)**：
   - 內建小白教學：按 F12 複製選擇器 (Copy selector) 貼入即可追蹤任意網頁。
6. **📱 Telegram 行動雙向助理**：
   - 打字框底部常駐 4 鍵實體鍵盤（商品清單、立即檢查、頻率調整、系統開關）。
   - 對話框貼網址自動帶規格與預算（例如：`https://... L號 1200`）。
   - 雙向即時設定同步，手機改頻率，電腦 Web 介面 3 秒內自動同步！

---

## 📂 檔案架構總覽 (File Structure)

本專案奉行**「最小檔案數、最大聚合度」**之現代設計，避免分散雜亂：

```text
inventory-notifier-agent/
│
├── run_local_app.py          ⭐【最核心！全功能聚合旗艦主程式】
│                             • Web 控制台 (FastAPI + Bootstrap 5 SPA)
│                             • 庫存自動巡檢與背景排程 (Background Scheduler)
│                             • 跨平台防休眠鎖 (Windows/Mac/Linux WakeLock)
│                             • 自動代搶加車與結帳流水線 (Playwright Autobuy Pipeline)
│                             • 萬用網頁爬蟲與 F12 規則測試 (Crawler & Rule Engine)
│                             • 定時預約任務模組 (Scheduled Tasks)
│                             • 價格歷史走勢時序數據庫存取
│
├── telegram_bot_service.py   ⭐【Telegram 行動機器人獨立服務】
│                             • 底部 4 鍵常駐實體鍵盤
│                             • 貼網址規格/預算即時解析
│                             • 與 Web 控制台設定雙向同步
│
├── start_agent.sh            🚀【一鍵啟動腳本】同時管理 Web + Bot 進程
│
├── tracked_products.json     📄【商品資料庫】儲存追蹤商品、庫存、價格歷史、自訂規則
├── app_config.json           📄【系統設定檔】巡檢頻率、通知偏好、勿擾模式
└── autobuy_config.json       🔒【搶購機密檔】帳密、信用卡預填 (已由 .gitignore 阻擋不上傳)
```

---

## 🤖 給 AI 的改版操作提示 (Prompt Template)

當您未來將 `run_local_app.py` 提供給 AI 進行升級時，可直接複製以下指令：

> 「這是我的一體化電商監控與代搶系統核心程式碼 `run_local_app.py`。所有的 Web UI、排程巡檢、萬用爬蟲、防休眠、Playwright 加車結帳流水線都在這單一檔案中。請幫我在這個檔案中 [描述您的需求，例如：增加某某電商的特殊加車邏輯 / 調整介面主題色 / 增加新的推播管道]，並請保持其單一檔案整合架構完整，不要拆散檔案。」


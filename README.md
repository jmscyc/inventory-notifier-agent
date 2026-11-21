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

## 🚀 快速開始使用指南 (Step-by-Step Guide)

### 📌 第一步：從哪裡開啟？如何啟動？

無論您是在本機桌面終端機、VS Code 或是雲端工作環境中，只要打開終端機 (Terminal) 並進入本專案資料夾：
```bash
cd /config/Desktop/BuildWithGemini/inventory-notifier-agent
```

#### 方式 A：一鍵雙開守護（最推薦 ⭐⭐⭐）
直接執行專案內建的一鍵啟動腳本，它會自動在背景啟動 **Web 控制台** 與 **Telegram 機器人守護程序**：
```bash
./start_agent.sh
```

#### 方式 B：手動分開啟動
若您想在終端機直接看即時日誌：
```bash
# 1. 啟動 Web 控制台（內含自動巡檢排程、防休眠、萬用爬蟲與自動搶購）
uv run python3 run_local_app.py

# 2. 啟動 Telegram 機器人對話服務（可另開一個終端機視窗執行）
uv run python3 telegram_bot_service.py
```

---

### 📌 第二步：如何進入控制台介面？

#### 🌐 雲端線上網頁版 (免開電腦 / 手機隨時可開)：
👉 **[https://jmscyc.github.io/inventory-notifier-agent/admin.html](https://jmscyc.github.io/inventory-notifier-agent/admin.html)**
*(同時支援 `index.html` 與 `admin.html`，隨時由瀏覽器查看 24/7 巡檢狀態與監控商品！)*

#### 💻 本地端完整控制台 (具備自動代搶/本機防休眠)：
啟動成功後，打開任何網頁瀏覽器，網址列輸入：
👉 **`http://localhost:8000`**

您將看到功能齊全的視覺化控制台，包含：
1. **新增追蹤商品**：貼上好市多或各大電商網址，可選填「指定規格（如 L號/黑色）」與「預算上限（如 1500）」，點擊「新增追蹤」即刻檢驗並排入巡檢！
2. **即時搜尋與狀態篩選**：上方提供即時關鍵字搜尋框與 `全部` / `現貨` / `缺貨` / `有設預算` 快捷切換。
3. **價格歷史趨勢圖**：商品卡片點擊「📈 價格趨勢圖」即可調出 Chart.js 繪製的波動折線圖與高低價分析。
4. **萬用網頁爬蟲精靈**：點選頁籤「萬用網頁爬蟲精靈 (F12小白指引)」，按瀏覽器 F12 複製 Selector 即可監控任何網路商城。
5. **預約搶登入 / 定時任務**：設定指定時間，毫秒級倒數自動開啟目標網頁或自動登入。

---

### 📌 第三步：如何搭配手機 Telegram 使用？

1. 打開手機 Telegram，搜尋並進入機器人：👉 **`@jmsaitry_bot`**
2. 點擊 `/start`，對話框底部會自動彈出 **4 顆常駐快捷按鈕**（📦 查看追蹤商品、⚡ 立即檢查、⏱️ 調整巡檢頻率、🛑 暫停監控）。
3. **出門在外免開網頁**：直接把電商商品網址傳給機器人，支援附帶規格與預算（例如：`https://.../p/123456 L號 1500`），機器人會自動新增並同步回電腦控制台！

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

## 📊 Google Sheet 雲端資料庫整合與權限教學 (跨設備 100% 同步)

想要讓資料不再只存在自己的本機設備，而是與 Google 試算表即時連動，由手機、網頁後台、GitHub 雲端排程三方自動同步？請照以下步驟操作：

### 🛠️ 5 步驟完成 Google Sheet 串接

1. **建立 Google 試算表**：
   - 進入您的 [Google 雲端硬碟](https://drive.google.com/)，建立一個空白「Google 試算表 (Google Sheets)」，命名為：`電商庫存監控雲端資料庫`。
2. **複製 Apps Script 程式碼**：
   - 在試算表上方選單點選 **「擴充功能」 ➔ 「Apps Script」**。
   - 將專案中的 [`GoogleAppsScript_Code.js`](GoogleAppsScript_Code.js) 內容全部複製，貼上並覆蓋 Apps Script 編輯器中的所有代碼，按 `Ctrl+S` (或 `Cmd+S`) 儲存。
3. **執行一次初始化建立工作表**：
   - 在 Apps Script 頂部的函式下拉選單中選擇 **`setupSheets`**，點擊 **「執行」**。
   - *首次執行會跳出「需要授權」提示，依序點擊「審查權限」➔ 選擇您的 Google 帳戶 ➔ 點擊左下角「進階」➔ 點擊「前往專案 (不安全)」➔ 點擊「允許」。*
   - 執行成功後，回到試算表會發現自動建好了 **「商品清單」**、**「系統設定」** 與 **「巡檢日誌」** 三個分頁！
4. **部署為網路應用程式 (關鍵權限設定 🔐)**：
   - 點擊 Apps Script 右上角藍色 **「部署」 ➔ 「新建部署」**。
   - 齒輪種類選擇：**「網路應用程式 (Web App)」**。
   - 設定如下：
     - **說明**：電商監控 API
     - **執行身分**：`我 (您的 Google 帳戶)`
     - **誰可以存取**：<span style="color:#10b981; font-weight:bold;">任何人 (Anyone)</span> *(⚠️ 必須選 Anyone，這樣網頁與 GitHub Actions 才能連線讀寫，他人無法查看您的 Google 帳號與其他私人檔案)*
   - 點擊「部署」，完成後複製產生的 **「網路應用程式網址 (Web App URL)」** (網址以 `/exec` 結尾)。
5. **貼入網頁後台或設定環境變數**：
   - **線上網頁版**：打開 [admin.html](https://jmscyc.github.io/inventory-notifier-agent/admin.html)，點擊右上角 **「設定 Google Sheet API」** 貼上網址即可即時雙向連線！
   - **地端本機版**：在 `.env` 中加入 `GOOGLE_SHEET_API_URL="您的_GAS_網址"`，本機新增/刪除商品會自動同步至試算表！
   - **GitHub 24/7 巡檢**：在 GitHub 儲存庫 `Settings` ➔ `Secrets and variables` ➔ `Actions` 新增 Secret 名稱為 `GOOGLE_SHEET_API_URL`，雲端巡檢將優先自 Google Sheet 抓取最新商品並回寫現貨狀態！

### 📱 換手機/換平板如何一秒同步？（零手動輸入、專屬 QR Code / 一鍵直連）

您不需要在每台裝置上手動輸入冗長的 Google 網址：
1. 在您已綁定好的電腦或手機上，點擊頂部紫色按鈕 **「跨設備免設定同步 (QR Code)」**。
2. **面對面掃碼**：另一台手機直接開啟相機掃描 QR Code，打開瞬間即**自動連線綁定**！
3. **通訊軟體分享**：點擊「複製」，將專屬直連連結（如 `https://.../admin.html?sync=...`）傳送到自己的 LINE / Telegram / 備忘錄，在新設備點開即刻永久同步！

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
│                             • Google Sheet 雲端雙向同步模組
│
├── GoogleAppsScript_Code.js  📊【Google Sheet 雲端資料庫後端 API】
│                             • 提供 doGet / doPost REST API
│                             • 自動維護商品清單、系統設定與 500 筆歷史日誌
│
├── admin.html / index.html   🌐【GitHub Pages 線上免電腦管理後台】
│                             • 支援直接連線 Google Sheet API
│                             • 支援商品篩選、狀態追蹤、手動即時觸發
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


# 商品庫存監控與智慧自動化工作台 (Inventory Notifier & Automation Agent)

支援電商（Costco 好市多、PChome 等）商品庫存即時偵測、階梯式自動加車與結帳、**預約定時搶登入/搶名額**、以及**小白友善的萬用視覺化網頁爬蟲精靈（含 F12 完整圖文導引）**之智慧代理系統。

---

## 🚀 快速啟動指南 (Quick Start)

### 本地 Web 旗艦控制台（零雲端依賴，推薦）
可在個人電腦（Mac / Windows / Linux）啟動全功能圖形化介面：

1. **安裝環境依賴**
   ```bash
   pip install fastapi uvicorn httpx beautifulsoup4 python-dotenv
   ```
2. **啟動 Web 控制台**
   ```bash
   python3 run_local_app.py
   ```
3. **瀏覽器開啟控制台**
   👉 **http://localhost:8000**

---

## 🌟 核心功能三大分頁介紹

打開控制台後，頂部提供三大專屬工作分頁：

### 1. 📦 商品庫存監控與自動加車 (Inventory & Auto-Cart)
* **新增商品**：貼上商品網址，系統自動檢驗並抓取商品名稱與即時現貨狀態。
* **桌面推播**：點擊頂部「啟用本機推播通知」，補貨或缺貨時彈出桌面右下角推播卡片與音效。
* **搶購模組**：
  - **⚡ 第一階段**：自動加入購物車（支援讀取 Cookie 授權，自動將現貨鎖在購物車，推播「已放入購物車！請在 15 分鐘內結帳」）。
  - **🚀 第二階段**：自動推進至最後付款確認頁面，為您省下所有排隊步驟。
  - **🛡️ 階梯容錯**：每一階段失敗均會自動降級為前一段正常通知，並提供一鍵手動直達連結。

### 2. ⏰ 預約搶登入 / 定時任務 (Scheduled Auto-Login & Trigger)
* **秒級預約執行**：支援指定特定年月日時分秒（例如開放搶登入的整點瞬間 `00:00:00`），或點擊「立即執行」進行測試。
* **自動載入與執行**：時間一到，背景排程自動喚起瀏覽器載入目標網頁，預先帶入 Session/Cookie 執行搶購或登入操作。
* **執行結果即時通報**：無論成功與否，立刻透過桌面推播與 Telegram 推播結果。

### 3. 🪄 萬用網頁爬蟲精靈 (小白友善 F12 視覺化指引)
想追蹤其他非好市多的網頁（例如：羽球場預約名額、餐廳訂位、課程報名、其他電商）？
控制台內建**新手小白 F12 抓取教學**：

```
[步驟 1] 在欲追蹤的網頁按下鍵盤 F12（或按滑鼠右鍵 -> 點「檢查」）。
   │
[步驟 2] 點擊開發者工具左上角的「小箭頭」選擇工具 (快捷鍵 Ctrl+Shift+C)。
   │
[步驟 3] 用滑鼠游標直接點擊網頁上你想看的那段文字（如「庫存數量」或「加入購物車」按鈕）。
   │
[步驟 4] 右側代碼會自動跳到對應的那行，在該行點滑鼠右鍵 -> Copy -> 點「Copy selector (複製選擇器)」。
   │
[步驟 5] 貼回本控制台的「CSS 選擇器」欄位，點擊「🔍 即時測試抓取」！
```
* **🔍 即時測試預覽**：點擊測試後，系統會立刻模擬抓取並呈現當前抓到的 HTML 標籤與文字，確認無誤後即可單鍵「儲存為監控規則」！
* **關鍵字純搜尋**：若不想用 F12，也可以只填寫關鍵字（如 `尚有名額` 或 `補貨中`），系統會全頁面監控。

---

## 🏗️ 系統架構與檔案清單 (Architecture & File Overview)

```
inventory-notifier-agent/
│
├── run_local_app.py               # 【本地 Web 旗艦工作台進入點】
│                                  # 整合三大分頁 SPA、背景巡檢排程、預約任務 Worker、
│                                  # Telegram 輪詢 Worker 與本機桌面推播佇列 (Polling Queue)。
│
├── run_local_bot.py               # 【本地純 Telegram Bot 進入點】
│
├── app/
│   ├── autobuy_module.py          # 【搶購核心模組】實作階梯式加車、Cookie 管理與降級回報。
│   ├── scheduled_tasks_module.py  # 【預約排程模組】實作定時搶登入、精確秒數比對與狀態管理。
│   ├── custom_crawler_module.py   # 【萬用爬蟲模組】實作自訂 CSS 選擇器、關鍵字比對與即時測試。
│   ├── inventory_tools.py         # 【電商爬蟲工具箱】嚴格判定好市多 PDP DOM 與通用電商特徵。
│   └── agent.py                   # 【ADK Agent 核心】(支援 Google Vertex AI / Agent Engine 模式)
│
├── tracked_products.json          # 【商品資料庫】儲存所有追蹤中的商品資訊與庫存狀態。
├── scheduled_tasks.json           # 【預約任務資料庫】儲存所有定時預約搶登入任務。
├── custom_crawler_rules.json      # 【自訂規則資料庫】儲存萬用爬蟲的 CSS 選擇器與關鍵字。
├── app_config.json                # 【系統組態檔】儲存巡檢間隔、通知頻率、推播管道開關。
└── autobuy_config.json            # 【搶購組態檔】儲存自動加車開關、結帳導向設定。
```

---

## 🛠️ 未來 AI 改版與維護守則 (Maintenance Guide for AI & Developers)

未來接手本專案的 AI 或開發者請務必遵守以下規範：

1. **預約任務擴充**：
   - 定時邏輯維護於 `app/scheduled_tasks_module.py`，背景比對迴圈位於 `run_local_app.py` 的 `scheduled_tasks_worker()`。
   - 若要增加更複雜的定時表單填寫或自訂動作，應在此 Worker 擴充 `action_type` 分支。
2. **萬用爬蟲精靈擴充**：
   - 測試解析與規則持久化位於 `app/custom_crawler_module.py` 的 `test_crawl_url()`。
   - 永遠確保回傳值具備 `success`、`page_title`、`elements` 與 `match_text_found`，以便前端 SPA 進行即時卡片渲染。
3. **安全與容錯準則**：
   - 不管是爬蟲、加車或預約任務，**任何子流程崩潰時均需 catch 例外並優雅 fallback**，並回傳明確的 `error_detail`，絕不可導致 FastAPI 服務中斷。

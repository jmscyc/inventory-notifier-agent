/**
 * 好市多與電商庫存監控系統 - Google Apps Script (GAS) 雲端資料庫 API
 * 
 * 部署說明：
 * 1. 在 Google 雲端硬碟中建立一個新的「Google 試算表 (Google Sheets)」，例如命名為「電商庫存監控雲端資料庫」。
 * 2. 點擊頂部選單「擴充功能」 -> 「Apps Script」。
 * 3. 將本檔案內容完整複製並覆蓋原本的代碼，按下 Ctrl+S (或 Cmd+S) 儲存。
 * 4. 點擊頂部函式選單選擇「setupSheets」，然後點擊「執行」按鈕進行初始化（系統會自動幫您建好工作表與表頭）。
 * 5. 點擊右上角藍色「部署」 -> 「新建部署」：
 *    - 種類選擇「網路應用程式 (Web App)」
 *    - 說明：電商監控 API
 *    - 執行身分：我 (您的 Google 帳戶)
 *    - 誰可以存取：任何人 (Anyone)  <-- 關鍵設定！
 * 6. 點擊「部署」，授權存取後複製產生的「網路應用程式網址 (Web App URL)」。
 * 7. 將此 URL 填入 admin.html 右上角的「設定 Google API」或本機環境變數 GOOGLE_SHEET_API_URL 即可！
 */

const SHEET_PRODUCTS = "商品清單";
const SHEET_CONFIG = "系統設定";
const SHEET_LOGS = "巡檢日誌";

/**
 * 初始化工作表結構
 */
function setupSheets() {
  const ss = SpreadsheetApp.getActiveSpreadsheet();
  
  // 1. 初始化「商品清單」
  let pSheet = ss.getSheetByName(SHEET_PRODUCTS);
  if (!pSheet) {
    pSheet = ss.insertSheet(SHEET_PRODUCTS);
    pSheet.appendRow([
      "商品編號", 
      "商品名稱", 
      "商品網址", 
      "指定規格", 
      "預算上限", 
      "現貨狀態", 
      "當前售價", 
      "最後檢查時間", 
      "狀態說明", 
      "是否啟用"
    ]);
    pSheet.getRange(1, 1, 1, 10)
      .setBackground("#1e3a8a")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
    
    // 加入範例資料
    pSheet.appendRow([
      "item-142997",
      "Costco 熱門商品",
      "https://www.costco.com.tw/p/142997",
      "",
      "",
      "🔴 缺貨中",
      "",
      Utilities.formatDate(new Date(), "Asia/Taipei", "yyyy-MM-dd HH:mm:ss"),
      "系統初始化建立",
      "TRUE"
    ]);
  }

  // 2. 初始化「系統設定」
  let cSheet = ss.getSheetByName(SHEET_CONFIG);
  if (!cSheet) {
    cSheet = ss.insertSheet(SHEET_CONFIG);
    cSheet.appendRow(["設定項目", "數值", "說明"]);
    cSheet.getRange(1, 1, 1, 3)
      .setBackground("#065f46")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
    
    cSheet.appendRow(["poll_interval_minutes", "15", "自動巡檢間隔 (分鐘)"]);
    cSheet.appendRow(["is_active", "TRUE", "全域監控開關 (TRUE/FALSE)"]);
    cSheet.appendRow(["telegram_notify", "TRUE", "是否發送 Telegram 推播"]);
    cSheet.appendRow(["oos_periodic_notify_hours", "4", "缺貨每隔幾小時提醒一次"]);
  }

  // 3. 初始化「巡檢日誌」
  let lSheet = ss.getSheetByName(SHEET_LOGS);
  if (!lSheet) {
    lSheet = ss.insertSheet(SHEET_LOGS);
    lSheet.appendRow(["記錄時間", "商品編號", "商品名稱", "現貨狀態", "售價", "事件說明"]);
    lSheet.getRange(1, 1, 1, 6)
      .setBackground("#374151")
      .setFontColor("#ffffff")
      .setFontWeight("bold");
  }

  return "✅ Google Sheets 初始化完成！";
}

/**
 * 處理 GET 請求 (讀取商品與設定)
 */
function doGet(e) {
  try {
    const action = (e && e.parameter && e.parameter.action) ? e.parameter.action : "getItems";
    const ss = SpreadsheetApp.getActiveSpreadsheet();

    if (action === "getItems") {
      const pSheet = ss.getSheetByName(SHEET_PRODUCTS);
      if (!pSheet) return jsonResponse({ success: false, message: "尚未初始化工作表，請先執行 setupSheets" });
      
      const rows = getSheetData(pSheet);
      const items = rows.map(r => {
        const inStockText = String(r["現貨狀態"] || "");
        const inStock = inStockText.includes("現貨") || inStockText.includes("有貨") || inStockText === "true" || inStockText === "TRUE";
        return {
          id: String(r["商品編號"] || ""),
          name: String(r["商品名稱"] || ""),
          target_url: String(r["商品網址"] || ""),
          url: String(r["商品網址"] || ""),
          preferred_option: String(r["指定規格"] || ""),
          target_price: r["預算上限"] ? parseFloat(r["預算上限"]) : null,
          in_stock: inStock,
          last_status: inStockText || (inStock ? "🟢 有現貨" : "🔴 缺貨中"),
          current_price: String(r["當前售價"] || ""),
          last_checked: String(r["最後檢查時間"] || ""),
          status_notes: String(r["狀態說明"] || ""),
          enabled: String(r["是否啟用"]).toUpperCase() !== "FALSE"
        };
      }).filter(it => it.target_url);

      return jsonResponse({ success: true, items: items });
    }

    if (action === "getConfig") {
      const cSheet = ss.getSheetByName(SHEET_CONFIG);
      const rows = getSheetData(cSheet);
      const config = {};
      rows.forEach(r => {
        config[r["設定項目"]] = r["數值"];
      });
      return jsonResponse({ success: true, config: config });
    }

    return jsonResponse({ success: false, message: "未知的 action 參數" });
  } catch (err) {
    return jsonResponse({ success: false, error: err.toString() });
  }
}

/**
 * 處理 POST 請求 (新增/更新/刪除商品或回寫狀態)
 */
function doPost(e) {
  const lock = LockService.getScriptLock();
  lock.tryLock(15000);
  try {
    let data;
    try {
      data = JSON.parse(e.postData.contents);
    } catch (parseErr) {
      data = e.parameter || {};
    }

    const action = data.action;
    const ss = SpreadsheetApp.getActiveSpreadsheet();
    const pSheet = ss.getSheetByName(SHEET_PRODUCTS);
    if (!pSheet) setupSheets();

    const nowStr = Utilities.formatDate(new Date(), "Asia/Taipei", "yyyy-MM-dd HH:mm:ss");

    // 1. 新增或更新商品 (addItem)
    if (action === "addItem") {
      const url = String(data.url || data.target_url || "").trim();
      if (!url) return jsonResponse({ success: false, message: "商品網址不能為空" });

      const name = String(data.name || "").trim() || "待辨識商品";
      const option = String(data.preferred_option || "").trim();
      const targetPrice = data.target_price !== undefined && data.target_price !== null && data.target_price !== "" ? String(data.target_price) : "";
      
      let itemId = String(data.id || "").trim();
      if (!itemId) {
        const m = url.match(/\/p\/(\d+)/);
        itemId = m ? `item-${m[1]}` : `item-${Math.floor(Math.random() * 899999 + 100000)}`;
      }

      const rows = pSheet.getDataRange().getValues();
      let foundRow = -1;
      for (let i = 1; i < rows.length; i++) {
        if (String(rows[i][0]) === itemId || String(rows[i][2]) === url) {
          foundRow = i + 1;
          break;
        }
      }

      if (foundRow > 0) {
        // 更新現有商品
        if (name && name !== "待辨識商品") pSheet.getRange(foundRow, 2).setValue(name);
        pSheet.getRange(foundRow, 3).setValue(url);
        pSheet.getRange(foundRow, 4).setValue(option);
        pSheet.getRange(foundRow, 5).setValue(targetPrice);
        pSheet.getRange(foundRow, 8).setValue(nowStr);
        pSheet.getRange(foundRow, 10).setValue("TRUE");
      } else {
        // 新增商品
        pSheet.appendRow([
          itemId,
          name,
          url,
          option,
          targetPrice,
          "⚪ 剛加入待巡檢",
          "",
          nowStr,
          "由 Web 控制台新增",
          "TRUE"
        ]);
      }

      return jsonResponse({ success: true, message: "商品已同步至 Google Sheet", id: itemId });
    }

    // 2. 刪除商品 (deleteItem)
    if (action === "deleteItem") {
      const itemId = String(data.id || "").trim();
      const url = String(data.url || "").trim();
      if (!itemId && !url) return jsonResponse({ success: false, message: "缺少商品 ID 或網址" });

      const rows = pSheet.getDataRange().getValues();
      let deleted = false;
      for (let i = rows.length - 1; i >= 1; i--) {
        if ((itemId && String(rows[i][0]) === itemId) || (url && String(rows[i][2]) === url)) {
          pSheet.deleteRow(i + 1);
          deleted = true;
          break;
        }
      }
      return jsonResponse({ success: deleted, message: deleted ? "已自 Google Sheet 移除" : "未找到該商品" });
    }

    // 3. 爬蟲回寫庫存與售價狀態 (updateStatus)
    if (action === "updateStatus") {
      const itemId = String(data.id || "").trim();
      const url = String(data.url || data.target_url || "").trim();
      const inStock = Boolean(data.in_stock);
      const statusText = inStock ? "🟢 有現貨" : "🔴 缺貨中";
      const notes = String(data.status_notes || "");
      const price = String(data.current_price || "");
      const name = String(data.name || "");

      const rows = pSheet.getDataRange().getValues();
      let foundRow = -1;
      for (let i = 1; i < rows.length; i++) {
        if ((itemId && String(rows[i][0]) === itemId) || (url && String(rows[i][2]) === url)) {
          foundRow = i + 1;
          break;
        }
      }

      if (foundRow > 0) {
        if (name) pSheet.getRange(foundRow, 2).setValue(name);
        pSheet.getRange(foundRow, 6).setValue(statusText);
        if (price) pSheet.getRange(foundRow, 7).setValue(price);
        pSheet.getRange(foundRow, 8).setValue(nowStr);
        pSheet.getRange(foundRow, 9).setValue(notes);

        // 記錄到日誌
        const lSheet = ss.getSheetByName(SHEET_LOGS);
        if (lSheet) {
          lSheet.appendRow([nowStr, itemId, name || rows[foundRow-1][1], statusText, price, notes]);
          if (lSheet.getLastRow() > 500) {
            lSheet.deleteRow(2); // 維護最大 500 筆歷史日誌
          }
        }
        return jsonResponse({ success: true, message: "庫存狀態已回寫試算表" });
      }

      return jsonResponse({ success: false, message: "找不到對應商品列" });
    }

    return jsonResponse({ success: false, message: "未支援的 POST 操作" });
  } catch (err) {
    return jsonResponse({ success: false, error: err.toString() });
  } finally {
    lock.releaseLock();
  }
}

// 輔助函式：讀取工作表並轉換為物件陣列
function getSheetData(sheet) {
  if (!sheet) return [];
  const rows = sheet.getDataRange().getValues();
  if (rows.length < 2) return [];
  const headers = rows[0];
  const result = [];
  for (let i = 1; i < rows.length; i++) {
    const row = rows[i];
    const obj = {};
    for (let j = 0; j < headers.length; j++) {
      obj[headers[j]] = row[j];
    }
    result.push(obj);
  }
  return result;
}

// 輔助函式：輸出 JSON 格式回應 (支援 CORS 跨域存取)
function jsonResponse(obj) {
  return ContentService.createTextOutput(JSON.stringify(obj))
    .setMimeType(ContentService.MimeType.JSON);
}

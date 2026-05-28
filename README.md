# BDA Final — LLM 整合後端

本專案包含一個簡易前端 Demo 與最小化的 Node.js 後端，可將即時 CWA 天氣與災害資料、以及來自 `date-holidays` 的台灣國定假日資料注入至 Prompt。Gemini 為選用功能，即使未設定模型金鑰，應用程式仍可正常運作。

環境變數（請建立 `.env` 檔案）：

- `CWA_WEATHER_API_KEY` — 用於從台灣中央氣象署開放資料平台取得及時天氣, 豪大雨特報(+-5天), 低溫特報(+-5天), 高溫特報(+-5天), 颱風警報(+-7)。
- `GEMINI_API_KEY` — 生成式模型的 API 金鑰。

透過 `npm install` 安裝相依套件，包含 `express`、`dotenv` 與 `date-holidays`。

安裝與執行：

```bash
npm install
npm start
```

API 端點：

- `POST /api/generate` — 接受 JSON `{ userKey, weather, festival, userData }`，回傳 `{ text, promptUsed, context }`。
  - `userData` 應包含前端已知的商品與使用者欄位，例如 `{ name, cityName, mainProduct, mainPrice, recProduct, recPrice, intentLabel }`。
  - `context.weather` 包含解析後的 CWA 測站與天氣文字。
  - `context.holiday` 包含 30 天內的下一個台灣國定假日（若有）。
  - `context.disaster` 包含地震與天氣警特報摘要。

注意：本範例僅在 `ENABLE_GEMINI=true` 時才使用 Generative Language 公開端點。若缺少 `CWA_WEATHER_API_KEY`，後端將無法取得 CWA 氣象資料。國定假日資料來自本地台灣假日套件，不需要金鑰。

---

## 離線資料處理模組（Stage 3 關聯規則挖掘）

### `data_cleaner.py`

用於從大型原始訂單資料中篩選出指定店家的資料，輸出為過濾後的 CSV 檔案供後續分析使用。

- 讀取 `Order_TG.csv`（主單）與 `Order_TS.csv`（子單）
- 以 `ShopId` 過濾指定店家（`SHOP_ID` 變數）
- 採用 chunksize 逐批讀取，避免大檔案造成記憶體不足
- 逐 chunk 寫入輸出檔案，不在記憶體中堆積資料
- 輸出：`Order_TG_filtered.csv`、`Order_TS_filtered.csv`

執行方式：
```bash
python data_cleaner.py
```

---

### `relation_product.ipynb`

基於歷史子單資料，利用 **FP-Growth 演算法** 挖掘商品之間的關聯規則（Market Basket Analysis），產出互補商品關聯對照表 `relation_product.csv`。

**處理流程：**
1. 讀取 `Order_TS_filtered.csv`，篩選已完成交易（`StatusDef == 'Finish'`）
2. 以前 80% 時間區間作為訓練集（避免資料洩漏）
3. 篩選含 2 件以上商品的訂單，限縮至 Top 500 高頻商品
4. 使用 `TransactionEncoder` 建立稀疏布林矩陣
5. 執行 FP-Growth，計算關聯規則（confidence × log(lift) 綜合評分排序）
6. 過濾同一 `SalePageId` 的規則（排除同商品不同 SKU 的組合銷售）
7. 過濾【滿額贈】商品
8. 對應 `SalePage.csv` 帶入商品名稱

**輸出欄位（`relation_product.csv`）：**

| 欄位 | 說明 |
|------|------|
| `target_title` | 目標商品名稱 |
| `complementary_title` | 互補商品名稱 |
| `target_product_id` | 目標商品 SKU（加密） |
| `complementary_product_id` | 互補商品 SKU（加密） |
| `target_salepage_id` | 目標商品頁 ID |
| `complementary_salepage_id` | 互補商品頁 ID |
| `score` | 綜合評分（confidence × log(lift)） |
| `confidence` | 信賴度 |
| `lift` | 提升度 |
| `support` | 支持度 |

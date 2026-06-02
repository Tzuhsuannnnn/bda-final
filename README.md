# BDA Final — 情境化 LLM 導購推薦系統

本專案為一套整合行為資料分析與大型語言模型的電商導購系統，針對不同猶豫類型的會員，結合商品關聯規則、個人化推薦與即時外部資訊，自動生成情境化推播文案。

---

## 系統架構總覽

```
使用者行為資料 (CSV)
  ├── 款式猶豫偵測   (horizontal_hesitation_users_shop.csv)
  ├── 價格猶豫偵測   (purchase_hesitation.csv)
  └── 規格猶豫偵測   (vertical_hesitation_users_shop.csv)
          ↓
商品推薦引擎 (server.js)
  ├── FP-Growth 關聯規則    (relation_product_*.csv)   ← 全局互補品
  └── Two-Tower 個人化推薦  (bundle_recommendation.csv) ← 個人化互補品
          ↓
外部即時資料
  ├── 中央氣象署 API (天氣、豪大雨/低溫/高溫特報、颱風)
  └── 台灣國定假日 (date-holidays)
          ↓
LLM 文案生成 (Gemini API)
          ↓
前端推播示範面板 (index.html)
```

---

## LLM 導購系統（主系統）

### 猶豫類型識別

系統依以下優先序判斷會員的猶豫類型（一人只取最高優先序）：

| 優先序 | 類型 | 行為特徵 | 資料來源 |
|--------|------|----------|----------|
| 1 | 款式猶豫 | 在同類商品間高頻切換 | `horizontal_hesitation_users_shop.csv` |
| 2 | 價格猶豫 | 商品在購物車中長期滯留 | `purchase_hesitation.csv` |
| 3 | 規格猶豫 | 單一商品頁停留時間異常長 | `vertical_hesitation_users_shop.csv` |

### 商品推薦邏輯（`POST /api/user-products`）

```
1. 查詢會員猶豫類型與猶豫商品 SalePageId
2. 用 SalePageId 查 relation_product（FP-Growth 關聯表）
   ├── 找到 → 取出主商品資訊（名稱、價格）
   │         再查 bundle_recommendation（Two-Tower 個人化推薦）
   │           ├── 有此 user 的個人化紀錄 → 用個人化互補品
   │           └── 無 → 用 FP-Growth 的最高分互補品
   └── 找不到（猶豫商品不在關聯表）
         → Fallback：使用整個 relation_product 中 score 最高的一組
```

**互補品優先策略**：Two-Tower 個人化推薦 > FP-Growth 全局關聯 > 全域 Top-1 Fallback

### 文案生成邏輯（`POST /api/generate`）

根據猶豫類型注入不同的 Persona Prompt：

| 類型 | 心理卡關點 | 文案策略 | 行銷切入點 |
|------|-----------|---------|-----------|
| 款式猶豫 | 資訊過載、害怕選錯 | 做減法，幫忙分類推薦 | 專家指南、情境標籤、懶人包 |
| 價格猶豫 | 覺得不划算、臨門一腳 | 放大價值感、降低痛感 | 換算降維、利益疊加、損失厭惡 |
| 規格猶豫 | 不確定性、缺乏安全感 | 做加法，提供證據與細節 | 厚實證言、痛點對照、條款白話化 |

Prompt 結構：猶豫類型 + 主商品 + 互補品 + 關聯分數 + 即時外部資訊（天氣/節慶/災害）→ Gemini 生成 LINE 推播文案

### 外部即時資訊（`POST /api/context`）

| 資料 | 來源 | 說明 |
|------|------|------|
| 即時天氣 | CWA F-C0032-001 | 縣市天氣文字描述 |
| 豪大雨/低溫/高溫特報 | CWA W-C0033-003/004/005 | 時間窗 ±5 天 |
| 颱風警報 | CWA W-C0034-005 | 時間窗 ±7 天 |
| 國定假日 | date-holidays (TW) | 30 天內節慶 |

### 前端示範面板（`index.html`）

操作流程分四步驟：

1. **輸入會員 ID** — 自動識別猶豫類型，顯示對應 badge
2. **勾選外部資訊** — 從天氣/地震/節慶中選擇納入 Prompt 的項目
3. **選擇商品組合** — 從下拉選單選主商品，系統自動帶出互補品（含價格、關聯分數、信賴度、提升度）
4. **生成文案** — 點擊生成，以打字機效果顯示於手機推播模擬畫面

---

## 環境設定

建立 `.env` 檔案並填入以下金鑰：

```
CWA_WEATHER_API_KEY=你的氣象署金鑰
GEMINI_API_KEY=你的Gemini金鑰
```

- `CWA_WEATHER_API_KEY` — [中央氣象署開放資料平台](https://opendata.cwa.gov.tw/) 申請
- `GEMINI_API_KEY` — 未設定時系統仍可運作，但無法生成文案

## 安裝與執行

```bash
npm install
npm start
```

預設於 `http://localhost:3001` 啟動。

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/user-products` | 輸入 `{ shopMemberId }`，回傳猶豫類型與推薦商品清單 |
| `POST` | `/api/context` | 回傳即時天氣、災害特報、節慶資訊 |
| `POST` | `/api/generate` | 輸入商品組合與外部資訊，回傳 LLM 生成文案 |
| `GET`  | `/api/recsys-products` | 回傳完整 recsys 商品目錄（前端下拉選單用） |

---

## 離線資料處理模組

### `data_cleaner.py`（資料清洗）

用於從大型原始訂單資料中篩選出指定店家的資料，輸出為過濾後的 CSV 檔案供後續分析使用。

- 讀取 `Order_TG.csv`（主單）與 `Order_TS.csv`（子單）
- 以 `ShopId` 過濾指定店家（`SHOP_ID` 變數）
- 採用 chunksize 逐批讀取，避免大檔案造成記憶體不足
- 輸出：`Order_TG_filtered.csv`、`Order_TS_filtered.csv`

```bash
python data_cleaner.py
```

---

### `relation_product.ipynb`（FP-Growth 關聯規則挖掘）

基於歷史子單資料，利用 **FP-Growth 演算法** 挖掘商品之間的關聯規則（Market Basket Analysis），產出互補商品關聯對照表 `relation_product.csv`。

**處理流程：**

1. 讀取 `Order_TS_filtered.csv`，篩選已完成交易（`StatusDef == 'Finish'`）
2. 以前 80% 時間區間作為訓練集（避免資料洩漏）
3. 篩選含 2 件以上商品的訂單，限縮至 Top 500 高頻商品
4. 使用 `TransactionEncoder` 建立稀疏布林矩陣
5. 執行 FP-Growth，計算關聯規則（confidence × log(lift) 綜合評分排序）
6. 過濾同一 `SalePageId` 的規則（排除同商品不同 SKU 的組合銷售）
7. 過濾【滿額贈】商品
8. 對應 `SalePage.csv` 帶入商品名稱與價格

**輸出欄位（`relation_product.csv`）：**

| 欄位 | 說明 |
|------|------|
| `target_title` | 目標商品名稱 |
| `complementary_title` | 互補商品名稱 |
| `target_price` | 目標商品價格 |
| `complementary_price` | 互補商品價格 |
| `target_product_id` | 目標商品 SKU（加密） |
| `complementary_product_id` | 互補商品 SKU（加密） |
| `target_salepage_id` | 目標商品頁 ID |
| `complementary_salepage_id` | 互補商品頁 ID |
| `score` | 綜合評分（confidence × log(lift)） |
| `confidence` | 信賴度 |
| `lift` | 提升度 |
| `support` | 支持度 |

---

### Two-Tower 個人化推薦模型（`bundle_recommendation.csv`）

基於 Two-Tower Model 為每位會員產出個人化商品綑綁推薦，作為 FP-Growth 全局關聯的升級版本。

- **輸入**：ShopMemberId（用戶特徵）、relation_product（關聯商品表）
- **輸出**：每位 user 的 top-k 推薦商品組合（`bundle_recommendation.csv`）

| 欄位 | 說明 |
|------|------|
| `ShopMemberId` | 會員 ID |
| `MainProduct` | 主商品 product_id |
| `BundleProduct` | 推薦搭配商品 product_id |
| `RecommendationScore` | 推薦分數 |
| `Lift` | 提升度 |
| `Confidence` | 信賴度 |

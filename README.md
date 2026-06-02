# BDA Final — 情境化 LLM 導購推薦系統

針對電商會員的猶豫行為，自動識別猶豫類型、結合商品關聯規則與個人化推薦，搭配即時外部資訊（天氣/節慶），透過 LLM 生成情境化推播文案。

---

## 系統架構

```
使用者行為資料 (Clickstream / Behavior)
        ↓
┌─────────────────────────────────────────┐
│           猶豫類型識別                    │
│  款式猶豫  │  價格猶豫  │  規格猶豫        │
└─────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────┐
│           商品推薦引擎                    │
│  FP-Growth 關聯規則（全局互補品）          │
│  Two-Tower 個人化推薦（per-user 互補品）   │
└─────────────────────────────────────────┘
        ↓
┌─────────────────────────────────────────┐
│         即時外部資訊（選用）               │
│  CWA 天氣 / 特報 / 颱風  │  台灣國定假日   │
└─────────────────────────────────────────┘
        ↓
     LLM 文案生成 (Gemini)
        ↓
     前端推播示範面板
```

---

## 快速啟動

**1. 安裝相依套件**
```bash
npm install
```

**2. 建立 `.env`**
```
CWA_WEATHER_API_KEY=你的氣象署金鑰
GEMINI_API_KEY=你的Gemini金鑰
```
> 兩個金鑰皆為選用；未設定時系統仍可啟動，但天氣資料與文案生成功能無法使用。

**3. 啟動 Server**
```bash
npm start
```
預設於 `http://localhost:3001`。

---

## 前端操作流程

| 步驟 | 操作 | 說明 |
|------|------|------|
| 1 | 輸入 ShopMemberId | 自動識別猶豫類型（款式／價格／規格），顯示對應 badge |
| 2 | 勾選外部資訊 | 從天氣、地震、節慶中選擇納入 Prompt 的項目 |
| 3 | 選擇商品組合 | 選主商品，系統自動帶出互補品（含價格、關聯分數、信賴度、提升度） |
| 4 | 生成文案 | 點擊生成，文案以打字機效果顯示於手機推播模擬畫面 |

---

## API 端點

| 方法 | 路徑 | 說明 |
|------|------|------|
| `POST` | `/api/user-products` | 輸入 `{ shopMemberId }`，回傳猶豫類型與推薦商品清單 |
| `POST` | `/api/context` | 回傳即時天氣、災害特報、節慶資訊 |
| `POST` | `/api/generate` | 輸入商品組合與外部資訊，回傳 LLM 生成文案 |
| `GET`  | `/api/recsys-products` | 回傳完整 recsys 商品目錄（前端下拉選單用） |

---

## 猶豫類型與推薦邏輯

優先序：**款式猶豫 > 價格猶豫 > 規格猶豫**（一人只取最高優先序）

| 類型 | 心理卡關點 | LLM 文案策略 |
|------|-----------|-------------|
| 款式猶豫 | 資訊過載、害怕選錯 | 做減法，幫忙分類推薦 |
| 價格猶豫 | 覺得不划算、臨門一腳 | 放大價值感、損失厭惡 |
| 規格猶豫 | 不確定性、缺乏安全感 | 做加法，提供證據與細節 |

**互補品選取優先序**：
1. Two-Tower 個人化推薦（`bundle_recommendation.csv`）
2. FP-Growth 全局關聯最高分（`relation_product.csv`）
3. Fallback：整個資料集中 score 最高的一組（全域 Top-1）

---

## 文件導覽

| 文件 | 說明 |
|------|------|
| [horizontal_vertical_hesitation_guide.md](horizontal_vertical_hesitation_guide.md) | 款式猶豫、規格猶豫偵測演算法、CLI 參數與執行方式 |
| [priceHesitate_guide.md](priceHesitate_guide.md) | 價格猶豫偵測（購物車滯留）邏輯與執行方式 |
| [handoff_guide.md](handoff_guide.md) | Two-Tower 模型訓練流程、推論、評估與衍生檔案說明 |
| [recommendation_models/model_architecture.md](recommendation_models/model_architecture.md) | Two-Tower 模型架構圖 |

---

## 資料檔說明

| 檔案 | 產出來源 | 用途 |
|------|---------|------|
| `horizontal_hesitation_users_shop.csv` | `hesitation_detection_with_shopid.py` | 款式猶豫 user 清單 |
| `vertical_hesitation_users_shop.csv` | `hesitation_detection_with_shopid.py` | 規格猶豫 user 清單 |
| `purchase_hesitation.csv` | `PurchaseHesitation.py` | 價格猶豫 user 清單 |
| `relation_product_*.csv` | `relation_product.ipynb`（FP-Growth） | 商品關聯表（含名稱、價格、分數） |
| `bundle_recommendation.csv` | `infer_recsys.py`（Two-Tower） | 個人化綑綁推薦清單 |

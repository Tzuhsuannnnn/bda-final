# 縱向、橫向猶豫偵測 Hesitation Detection

## 目的

從 clickstream 行為資料中偵測兩種使用者猶豫行為：

- 水平（Horizontal）：使用者在比較多個商品（以類別為單位的比較／選擇猶豫）。
- 垂直（Vertical）：使用者反覆查看同一商品（商品層級的深入瀏覽）。

## 執行方式

- 這個版本預設只處理店鋪 `RZSHERLBqjPGOUFO01RYew==`，並會讀取 `RZSHERLBqjPGOUFO01RYew_category_mapping_multicat.csv` 來補 `CategoryId`。
- 啟用虛擬環境後執行主程式：

```shell
./venv/bin.activate
./venv/bin/python ./hesitation_detection_with_shopid.py --folder . --pattern "session01_202*.csv" --shop-id "RZSHERLBqjPGOUFO01RYew==" --category-mapping "RZSHERLBqjPGOUFO01RYew_category_mapping_multicat.csv" --out-horizontal horizontal_hesitation_users.csv --out-vertical vertical_hesitation_users.csv
```

- 顯示幫助（含所有 CLI 參數與預設值）：

```shell
./venv/bin/python ./hesitation_detection_with_shopid.py -h
```

## 檔案說明

- 主程式： [hesitation_detection_with_shopid.py](hesitation_detection_with_shopid.py)
- 輸出： `horizontal_hesitation_users.csv`, `vertical_hesitation_users.csv`

### 處理流程摘要

1. 依 `--pattern` 在 `--folder` 下找到 session CSV 檔。
2. （選用）由所有 session 檔建立 SalePageId -> primary CategoryId 映射，用來填補缺失的 `CategoryId`。
3. 以 `ShopId`+`ShopMemberId` 並依 `--idle-minutes`（預設 30 分鐘）進行 session 切分。
4. 計算停留時間（dwell），並將超過 `max_dwell_seconds`（預設 1800 秒）截斷。
5. 產生兩個 feature 表（串流到暫存 CSV）：水平與垂直特徵。
6. 計算全域與商品級門檻，並套用篩選得到最終輸出。

### 水平（Horizontal）偵測邏輯（以 session 為單位）

- 產生欄位（最終水平 CSV 包含）：
  - `ShopId`, `ShopMemberId`, `session_id`
  - `unique_category_count`
  - `unique_salepage_count`
  - `avg_page_stay_time`
  - `top_category_unique_salepage_count`
  - `hesitation_category_ids`（以 `|` 串接）
  - `hesitation_salepage_ids`（以 `|` 串接）
  - `dominant_category_ratio = top_category_unique_salepage_count / unique_salepage_count`

- 預設篩選規則（於 `filter_horizontal()`）：
  - `unique_category_count >= 1`
  - `unique_category_count <= --horizontal-max-same-category-products`（預設 5）
  - `unique_salepage_count >= --horizontal-min-unique-products`（預設 5）
  - `avg_page_stay_time >= 15`（秒）
  - `avg_page_stay_time < overall_avg`（`overall_avg` 由所有水平特徵計算得出）
  - `dominant_category_ratio > --horizontal-min-dominant-category-ratio`（預設 0.75）

備註：本工具不會使用原始 session 的 `CategoryId` 欄位（假如 session 內的 category 為錯誤或不一致，會導致誤偵測）。程式會僅以你提供的 `SalePageId -> ProductCategory` 映射來決定 `CategoryId`，若某筆商品在映射中找不到對應類別，該商品列會被移除。

### 垂直（Vertical）偵測邏輯（以 session + SalePageId 為單位）

- 產生欄位（最終垂直 CSV 包含）：
  - `ShopId`, `ShopMemberId`, `session_id`, `SalePageId`
  - `product_stay_time`
  - `repeated_view_count`
  - `avg_product_stay`
  - `dynamic_threshold`（等於 `avg_product_stay * --vertical-threshold-multiplier`）

- 篩選規則（於 `filter_vertical()`）：
  1. 核心：`product_stay_time > dynamic_threshold`
  2. 重複觀看：`repeated_view_count >= --vertical-repeated-view-threshold`（預設 3）
  3. 排除掛機：需 `product_stay_time > 0`（排除 0 或占位停留）
  4. 排除 baseline 不足：`avg_product_stay` 必須存在（即該商品有足夠的歷史樣本）

檢出條件：同時滿足規則 4 與 3，且（規則 1 或 規則 2）其中之一。

## 主要 CLI 參數（含預設值）

- `--folder` : session CSV 所在資料夾（預設 `.`）
- `--pattern` : glob 模式（預設 `session01_202*.csv`）
- `--out-horizontal` : 水平輸出檔（預設 `horizontal_hesitation_users.csv`）
- `--out-vertical` : 垂直輸出檔（預設 `vertical_hesitation_users.csv`）
- `--temp-dir` : 暫存特徵檔資料夾（預設自動建立）
- `--chunksize` : 串流讀寫的 chunk 大小（預設 `500000`）
- `--idle-minutes` : session 閒置分鐘數（預設 `30`）

### 水平相關：

- `--horizontal-min-unique-products`（預設 `5`）
- `--horizontal-max-same-category-products`（預設 `5`）
- `--horizontal-min-dominant-category-ratio`（預設 `0.75`）

### 垂直相關：

- `--min-product-samples` : 用於計算 `avg_product_stay` 的最小樣本數（預設 `5`）
- `--vertical-repeated-view-threshold` : 重複觀看門檻（預設 `3`）
- `--vertical-threshold-multiplier` : 動態門檻乘數（預設 `1.5`）

## 注意事項與建議

- 本程式採用串流暫存方式以降低記憶體使用，會在暫存資料夾產生 `_horizontal_features.csv` 與 `_vertical_features.csv`。

## 範例（針對 session01 執行）

```shell
./venv/bin/python ./hesitation_detection_with_shopid.py --folder . --pattern "session01_202*.csv" --shop-id "RZSHERLBqjPGOUFO01RYew==" --category-mapping "RZSHERLBqjPGOUFO01RYew_category_mapping_multicat.csv" --out-horizontal horizontal_hesitation_users01.csv --out-vertical vertical_hesitation_users01.csv
```

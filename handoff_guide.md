## 1. 執行流程 (Execution Flow)

目前推薦系統的資料處理與模型訓練流程如下：

1. **資料準備 (`prepare.py`)**：清理原始資料，將訂單資料依照時間切分（前 80% 作為訓練，後 20% 作為測試），並過濾出目標 ShopID 的資料。
2. **模型訓練 (`train_recsys.py`)**：
   - 讀取訓練集資料並萃取使用者特徵（RFM、購物車轉換率等）與商品特徵（價格、透過 `bge-small-zh-v1.5` 生成的文字 Embedding）。
   - 進行負樣本採樣（Negative Sampling）。
   - 訓練導入 AMP 與非同步傳輸加速的 **Two-Tower Model (雙塔模型)**（若訓練失敗會自動退回 Implicit ALS 模型）。
   - 將訓練好的模型權重與特徵正規化參數儲存至 `artifacts/`。
3. **模型推論與評估 (`infer_recsys.py` & `baseline_recsys.py`)**：
   - 針對所有使用者，計算其與所有商品的內積並排序，產出 Top-K 推薦。
   - 結合 FP-Growth 關聯規則 (`relation_product.csv`) 生成最終 Bundle 推薦。
   - 與測試集（後 20% 時間）進行比對，計算 HitRate@10、NDCG@10，並與 Most Popular、Random 等 Baseline 比較。
4. **探索性資料分析 (`EDA_Recommendation.ipynb`)**：提供視覺化的購買行為分佈、長尾效應、行為漏斗與時序趨勢分析。

## 2. 衍生檔案說明 (Derived Files)

執行完上述流程後，工作目錄會產生以下重要檔案供後續模組使用：

- **`user_feature.parquet`**: 使用者特徵檔。可用於後續讓 LLM 了解使用者輪廓（例如購買力、活躍度、轉換率）。
- **`product_feature.parquet`**: 商品特徵檔（包含文字語意特徵與熱門度）。
- **`recommendation.csv`**: 模型推論的個人化 Top K 商品清單。欄位包含 `ShopMemberId`, `RecommendRank`, `ProductId`, `Score`。
- **`bundle_recommendation.csv`**: 結合關聯規則的最終綑綁推薦商品清單。
- **`evaluation.json`**: 模型離線評估結果（包含 Two-Tower 與 Baselines 的成績）。
- **`artifacts/metadata.json`**: 紀錄模型訓練時使用的參數、平均值/標準差、以及對應的 User/Product IDs。
- **`artifacts/model.pt`**: 訓練完畢的雙塔模型 PyTorch 權重。

## 3. `parse_args` 參數設定指南

在執行現有腳本時，可以透過命令列參數進行微調：

### `train_recsys.py`
- `--data-dir` / `--output-dir`: 原始資料與輸出檔案的路徑。
- `--artifact-dir`: 儲存模型權重與 `metadata.json` 的資料夾（預設 `artifacts`）。
- `--model`: 模型選擇，支援 `auto` (預設，優先雙塔失敗退 ALS)、`two_tower`、`als`。
- `--text-model`: 使用的 HuggingFace 句向量模型（預設 `BAAI/bge-small-zh-v1.5`）。
- `--epochs`: 神經網路訓練的迭代次數（預設 `3`）。
- `--batch-size`: 訓練 Batch Size（預設 `1024`）。
- `--lr`: Learning Rate（預設 `1e-3`）。
- `--neg-ratio`: 負樣本比例（預設 `4`，即 1 個正樣本搭配 4 個隨機負樣本）。

### `infer_recsys.py` / `baseline_recsys.py`
- `--topk`: 要為每位使用者推薦的商品數量（預設 `10`）。
- `--evaluate`: 加入此 flag 則會在推論後自動利用測試集計算 HitRate 與 NDCG。
---

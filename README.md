# 購物猶豫 - 找出已經將商品加入購物車但未結帳的名單

## 目的
從 Behavior 資料中，找出同`ShopMemberId`和同`SalePage`的資料，篩選 add 後 24 小時沒有 Purcahse 記錄的名單

## 檔案說明
- 主程式：
- 輸出檔案：`behavior_add_purchase_filtered.csv`, `add_no_purchase_within_24h.csv`

## 作法
### Step 1
- 先篩選所有檔案，整理成特定商店的 `ShopMemberId`,`SalePageId`,`Behavior`,`HitTime`
- 依序匯入所有 Behavior 資料
- 篩選商店`RZSHERLBqjPGOUFO01RYew==`，並只留下`ShopId`,`ShopMemberId`,`SalePageId`,`Behavior`,`HitTime`這幾個欄位 
- 輸出`behavior_add_purchase_filtered.csv`

### Step 2
- 以`HitTime`判斷 add 後 24 小時內沒有 purchase 的資料
- 輸出`add_no_purchase_within_24h.csv`，欄位為`ShopMemberId`,`SalePageId`,`HitTime`，其中`HitTime`為 add 的資料時間

import pandas as pd
import glob
import os
from bisect import bisect_left

folder = "/Users/youfaladihe/Desktop/BDA_Dataset/behavior"
pattern = "session*.csv"

# 篩選特定商店和欄位
shop_id = "RZSHERLBqjPGOUFO01RYew=="
needed_cols = ["ShopId", "ShopMemberId", "SalePageId", "Behavior", "HitTime"]

# 先篩選所有檔案，整理成特定商店的 ShopMemberId, SalePageId, Behavior, HitTime
output_path = "/Users/youfaladihe/Desktop/behavior_add_purchase_filtered.csv"

if os.path.exists(output_path):
    os.remove(output_path)

files = sorted(glob.glob(os.path.join(folder, pattern)))
chunksize = 500_000

for file in files:
    print(f"Processing: {os.path.basename(file)}")

    for chunk in pd.read_csv(
        file,
        usecols=lambda c: c in needed_cols,
        dtype="string",
        chunksize=chunksize,
        low_memory=False
    ):
        chunk = chunk[chunk["ShopId"].str.strip() == shop_id].copy()

        if chunk.empty:
            continue

        chunk["ShopMemberId"] = chunk["ShopMemberId"].str.strip()
        chunk["SalePageId"] = chunk["SalePageId"].str.strip()
        chunk["Behavior"] = chunk["Behavior"].str.lower().str.strip()
        chunk["HitTime"] = pd.to_numeric(chunk["HitTime"], errors="coerce")

        chunk = chunk.dropna(subset=["ShopMemberId", "SalePageId", "Behavior", "HitTime"])

        chunk = chunk[
            chunk["Behavior"].isin(["add", "purchase"])
        ][["ShopMemberId", "SalePageId", "Behavior", "HitTime"]]

        if not chunk.empty:
            chunk.to_csv(
                output_path,
                mode="a",
                header=not os.path.exists(output_path),
                index=False
            )

print(f"完成，已輸出精簡檔：{output_path}")

input_path = "/Users/youfaladihe/Desktop/behavior_add_purchase_filtered.csv"
output_path = "/Users/youfaladihe/Desktop/add_no_purchase_within_24h.csv"

# 以 HitTime判斷 add 後 24 小時內沒有 purchase 的資料
window_ms = 24 * 60 * 60 * 1000
chunksize = 500_000

purchase_times = {}

for chunk in pd.read_csv(input_path, dtype="string", chunksize=chunksize):
    chunk["HitTime"] = pd.to_numeric(chunk["HitTime"], errors="coerce")
    chunk = chunk.dropna(subset=["ShopMemberId", "SalePageId", "Behavior", "HitTime"])

    purchases = chunk[chunk["Behavior"] == "purchase"]

    for row in purchases.itertuples(index=False):
        key = (row.ShopMemberId, row.SalePageId)
        purchase_times.setdefault(key, []).append(int(row.HitTime))

for key in purchase_times:
    purchase_times[key].sort()

if os.path.exists(output_path):
    os.remove(output_path)

for chunk in pd.read_csv(input_path, dtype="string", chunksize=chunksize):
    chunk["HitTime"] = pd.to_numeric(chunk["HitTime"], errors="coerce")
    chunk = chunk.dropna(subset=["ShopMemberId", "SalePageId", "Behavior", "HitTime"])

    adds = chunk[chunk["Behavior"].isin(["add"])]

    keep_rows = []

    for row in adds.itertuples(index=False):
        key = (row.ShopMemberId, row.SalePageId)
        add_time = int(row.HitTime)

        times = purchase_times.get(key, [])
        idx = bisect_left(times, add_time)

        has_purchase_within_24h = (
            idx < len(times)
            and times[idx] <= add_time + window_ms
        )

        if not has_purchase_within_24h:
            keep_rows.append({
                "ShopMemberId": row.ShopMemberId,
                "SalePageId": row.SalePageId,
                "HitTime": add_time
            })

    if keep_rows:
        pd.DataFrame(keep_rows).drop_duplicates().to_csv(
            output_path,
            mode="a",
            header=not os.path.exists(output_path),
            index=False
        )

print(f"完成，已輸出：{output_path}")

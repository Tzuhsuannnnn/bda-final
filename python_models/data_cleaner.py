import pandas as pd
import os

SHOP_ID = "zXQPxhiL90nRa1XbvctRfA=="
CHUNK_SIZE = 100_000

# 過濾 Order_TG.csv
# print("Processing Order_TG.csv...")
# if os.path.exists("Order_TG_filtered.csv"):
#     os.remove("Order_TG_filtered.csv")
# total_tg = 0
# for i, chunk in enumerate(pd.read_csv("Order_TG.csv", chunksize=CHUNK_SIZE, dtype=object)):
#     filtered = chunk[chunk["ShopId"] == SHOP_ID]
#     filtered.to_csv("Order_TG_filtered.csv", mode='a', header=(i == 0), index=False)
#     total_tg += len(filtered)
#     print(f"  TG chunk {i+1} done, 累計符合筆數: {total_tg:,}")
# print("Order_TG done\n")

# 過濾 Order_TS.csv
print("Processing Order_TS.csv...")
if os.path.exists(f"Order_TS_filtered_{SHOP_ID}.csv"):
    os.remove(f"Order_TS_filtered_{SHOP_ID}.csv")
    print("舊的 Order_TS_filtered.csv 已刪除")

total_ts = 0
for i, chunk in enumerate(pd.read_csv("Order_TS.csv", chunksize=CHUNK_SIZE, dtype=object)):
    filtered = chunk[chunk["ShopId"] == SHOP_ID]
    filtered.to_csv(f"Order_TS_filtered_{SHOP_ID}.csv", mode='a', header=(i == 0), index=False)
    total_ts += len(filtered)
    print(f"  TS chunk {i+1} done, 累計符合筆數: {total_ts:,}")
print("Order_TS done")

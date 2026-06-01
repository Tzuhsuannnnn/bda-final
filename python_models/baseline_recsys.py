import argparse
import json
from pathlib import Path
import numpy as np
import pandas as pd
from typing import Dict, List

from python_models.infer_recsys import (
    load_feature_tables,
    build_purchase_map,
    evaluate_recommendations
)
from python_models.recsys_common import detect_shop_id, load_metadata, load_orders

def recommend_popular(
    product_df: pd.DataFrame,
    user_ids: List[str],
    product_ids: List[str],
    purchase_map: Dict[str, set],
    topk: int
) -> pd.DataFrame:
    popularity_scores = product_df["purchase_count"].fillna(0).astype(float).values
    
    rows = []
    batch_size = 1000
    for start in range(0, len(user_ids), batch_size):
        end = min(start + batch_size, len(user_ids))
        batch_users = user_ids[start:end]
        
        scores = np.tile(popularity_scores, (len(batch_users), 1))
        
        for idx, user_id in enumerate(batch_users):
            purchased = purchase_map.get(user_id)
            if purchased:
                scores[idx, list(purchased)] = -1e9
                
        top_idx = np.argsort(-scores, axis=1)[:, :topk]
        
        for row_offset, user_id in enumerate(batch_users):
            for rank in range(topk):
                item_idx = top_idx[row_offset, rank]
                item_id = product_ids[item_idx]
                score = float(scores[row_offset, item_idx])
                rows.append((user_id, rank + 1, item_id, score))
                
    return pd.DataFrame(rows, columns=["ShopMemberId", "RecommendRank", "ProductId", "Score"])

def recommend_random(
    user_ids: List[str],
    product_ids: List[str],
    purchase_map: Dict[str, set],
    topk: int,
    seed: int = 42
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    
    rows = []
    batch_size = 1000
    for start in range(0, len(user_ids), batch_size):
        end = min(start + batch_size, len(user_ids))
        batch_users = user_ids[start:end]
        
        scores = rng.random((len(batch_users), len(product_ids)))
        
        for idx, user_id in enumerate(batch_users):
            purchased = purchase_map.get(user_id)
            if purchased:
                scores[idx, list(purchased)] = -1e9
                
        top_idx = np.argsort(-scores, axis=1)[:, :topk]
        
        for row_offset, user_id in enumerate(batch_users):
            for rank in range(topk):
                item_idx = top_idx[row_offset, rank]
                item_id = product_ids[item_idx]
                score = float(scores[row_offset, item_idx])
                rows.append((user_id, rank + 1, item_id, score))
                
    return pd.DataFrame(rows, columns=["ShopMemberId", "RecommendRank", "ProductId", "Score"])

def parse_args():
    parser = argparse.ArgumentParser(description="Evaluate baselines for recommendation")
    parser.add_argument("--data-dir", default=".")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--shop-id", default=None)
    parser.add_argument("--topk", type=int, default=10)
    return parser.parse_args()

def main():
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    artifact_dir = Path(args.artifact_dir)
    
    shop_id = args.shop_id or detect_shop_id(data_dir)
    
    metadata = load_metadata(artifact_dir / "metadata.json")
    user_df, product_df = load_feature_tables(output_dir)
    
    user_df = user_df.set_index("ShopMemberId").loc[metadata["user_ids"]].reset_index()
    product_df = product_df.set_index("ProductId").loc[metadata["product_ids"]].reset_index()
    
    orders_train_path = data_dir / f"orders_train_{shop_id}.csv"
    orders_filtered_path = data_dir / f"Order_TS_filtered_{shop_id}.csv"
    
    if orders_train_path.exists():
        train_orders = load_orders(orders_train_path, shop_id)
    elif orders_filtered_path.exists():
        all_orders = load_orders(orders_filtered_path, shop_id)
        split_time = metadata.get("split_time")
        split_ts = pd.to_datetime(split_time) if split_time else all_orders["OrderDateTime"].quantile(0.8)
        train_orders = all_orders[all_orders["OrderDateTime"] <= split_ts]
    else:
        raise FileNotFoundError("Missing training orders.")
        
    product_id_to_idx = {pid: idx for idx, pid in enumerate(metadata["product_ids"])}
    purchase_map = build_purchase_map(train_orders, product_id_to_idx)
    
    print("Generating Most Popular recommendations...")
    recs_popular = recommend_popular(
        product_df,
        metadata["user_ids"],
        metadata["product_ids"],
        purchase_map,
        args.topk
    )
    
    print("Generating Random recommendations...")
    recs_random = recommend_random(
        metadata["user_ids"],
        metadata["product_ids"],
        purchase_map,
        args.topk
    )
    
    # Load test orders for evaluation
    orders_test_path = data_dir / f"orders_test_{shop_id}.csv"
    if orders_test_path.exists():
        test_orders = load_orders(orders_test_path, shop_id)
    else:
        all_orders = load_orders(orders_filtered_path, shop_id)
        test_orders = all_orders[all_orders["OrderDateTime"] > split_ts]
        
    print("Evaluating Most Popular...")
    popular_metrics = evaluate_recommendations(recs_popular, test_orders, args.topk)
    print("Most Popular:", popular_metrics)
    
    print("Evaluating Random...")
    random_metrics = evaluate_recommendations(recs_random, test_orders, args.topk)
    print("Random:", random_metrics)
    
    # Update evaluation.json
    eval_path = output_dir / "evaluation.json"
    if eval_path.exists():
        with eval_path.open("r", encoding="utf-8") as f:
            final_metrics = json.load(f)
    else:
        final_metrics = {}
        
    # Group existing metrics into a main model dict if not grouped yet
    if "HitRate@10" in final_metrics:
        model_type = metadata.get("model_type", "Model")
        old_metrics = final_metrics.copy()
        final_metrics = {model_type: old_metrics}
        
    final_metrics["Popular"] = popular_metrics
    final_metrics["Random"] = random_metrics
    
    with eval_path.open("w", encoding="utf-8") as f:
        json.dump(final_metrics, f, indent=2)
        
    print(f"Metrics saved to {eval_path}")

if __name__ == "__main__":
    main()

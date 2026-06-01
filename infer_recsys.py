from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import torch

from recsys_common import DEFAULT_TEXT_EMB_DIM, detect_shop_id, load_metadata, load_orders
from recsys_model import TwoTowerModel


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Infer two-tower recommendations")
    parser.add_argument("--data-dir", default=".")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--shop-id", default=None)
    parser.add_argument("--topk", type=int, default=10)
    parser.add_argument("--bundle-topn", type=int, default=3)
    parser.add_argument(
        "--bundle-score", choices=["confidence", "lift"], default="confidence"
    )
    parser.add_argument("--evaluate", action="store_true")
    return parser.parse_args()


def load_feature_tables(output_dir: Path) -> Tuple[pd.DataFrame, pd.DataFrame]:
    user_path = output_dir / "user_feature.parquet"
    product_path = output_dir / "product_feature.parquet"
    if not user_path.exists() or not product_path.exists():
        raise FileNotFoundError("Missing user_feature.parquet or product_feature.parquet")
    return pd.read_parquet(user_path), pd.read_parquet(product_path)


def encode_categories(values: pd.Series, mapping: Dict[str, int]) -> List[int]:
    return [mapping.get(str(v), 0) for v in values.fillna("UNK").astype(str)]


def apply_standardization(values: np.ndarray, mean: List[float], std: List[float]) -> np.ndarray:
    mean_arr = np.array(mean)
    std_arr = np.array(std)
    std_arr = np.where(std_arr == 0, 1.0, std_arr)
    return (values - mean_arr) / std_arr


def build_purchase_map(
    orders_df: pd.DataFrame, product_id_to_idx: Dict[str, int]
) -> Dict[str, set]:
    purchase_map: Dict[str, set] = {}
    grouped = orders_df.groupby("ShopMemberId")["ProductId"].apply(set)
    for user_id, items in grouped.items():
        idxs = {product_id_to_idx[item] for item in items if item in product_id_to_idx}
        purchase_map[user_id] = idxs
    return purchase_map


def recommend_topk(
    model: TwoTowerModel,
    user_numeric: torch.Tensor,
    user_gender: torch.Tensor,
    user_level: torch.Tensor,
    item_numeric: torch.Tensor,
    item_text: torch.Tensor,
    user_ids: List[str],
    product_ids: List[str],
    purchase_map: Dict[str, set],
    topk: int,
    device: torch.device,
) -> pd.DataFrame:
    model.eval()
    with torch.no_grad():
        item_numeric = item_numeric.to(device)
        item_text = item_text.to(device)
        item_emb = model.encode_item(item_numeric, item_text)

        rows = []
        batch_size = 512
        for start in range(0, len(user_ids), batch_size):
            end = min(start + batch_size, len(user_ids))
            user_num = user_numeric[start:end].to(device)
            user_gender_b = user_gender[start:end].to(device)
            user_level_b = user_level[start:end].to(device)
            user_emb = model.encode_user(user_num, user_gender_b, user_level_b)
            scores = torch.matmul(user_emb, item_emb.T)

            for idx, user_id in enumerate(user_ids[start:end]):
                purchased = purchase_map.get(user_id)
                if purchased:
                    scores[idx, list(purchased)] = -1e9

            top_scores, top_idx = torch.topk(scores, k=topk, dim=1)
            top_scores = top_scores.cpu().numpy()
            top_idx = top_idx.cpu().numpy()

            for row_offset, user_id in enumerate(user_ids[start:end]):
                for rank in range(topk):
                    item_id = product_ids[top_idx[row_offset, rank]]
                    score = float(top_scores[row_offset, rank])
                    rows.append((user_id, rank + 1, item_id, score))

    return pd.DataFrame(rows, columns=["ShopMemberId", "RecommendRank", "ProductId", "Score"])


def recommend_topk_als(
    user_factors: np.ndarray,
    item_factors: np.ndarray,
    user_ids: List[str],
    product_ids: List[str],
    purchase_map: Dict[str, set],
    topk: int,
) -> pd.DataFrame:
    rows = []
    batch_size = 512
    for start in range(0, len(user_ids), batch_size):
        end = min(start + batch_size, len(user_ids))
        u_batch = user_factors[start:end]
        scores = np.dot(u_batch, item_factors.T)

        for idx, user_id in enumerate(user_ids[start:end]):
            purchased = purchase_map.get(user_id)
            if purchased:
                scores[idx, list(purchased)] = -1e9

        scores_t = torch.from_numpy(scores)
        top_scores, top_idx = torch.topk(scores_t, k=topk, dim=1)
        top_scores = top_scores.numpy()
        top_idx = top_idx.numpy()

        for row_offset, user_id in enumerate(user_ids[start:end]):
            for rank in range(topk):
                item_id = product_ids[top_idx[row_offset, rank]]
                score = float(top_scores[row_offset, rank])
                rows.append((user_id, rank + 1, item_id, score))

    return pd.DataFrame(rows, columns=["ShopMemberId", "RecommendRank", "ProductId", "Score"])


def load_relation_product(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    if "target_product_id" in df.columns:
        df = df.rename(
            columns={
                "target_product_id": "MainProduct",
                "complementary_product_id": "RelatedProduct",
            }
        )
    if "MainProduct" not in df.columns or "RelatedProduct" not in df.columns:
        raise ValueError("relation_product missing expected columns")
    return df


def build_bundle_recommendations(
    recs: pd.DataFrame, relation_df: pd.DataFrame, score_col: str, topn: int
) -> pd.DataFrame:
    rel = relation_df.copy()
    rel = rel.sort_values(score_col, ascending=False)
    rel = rel.groupby("MainProduct").head(topn)

    bundle_rows = []
    relation_map = {}
    for _, row in rel.iterrows():
        relation_map.setdefault(row["MainProduct"], []).append(row)

    for _, rec in recs.iterrows():
        main_product = rec["ProductId"]
        if main_product not in relation_map:
            continue
        for rel_row in relation_map[main_product]:
            bundle_rows.append(
                (
                    rec["ShopMemberId"],
                    main_product,
                    rel_row["RelatedProduct"],
                    rec["Score"],
                    float(rel_row.get("lift", np.nan)),
                    float(rel_row.get("confidence", np.nan)),
                )
            )

    return pd.DataFrame(
        bundle_rows,
        columns=[
            "ShopMemberId",
            "MainProduct",
            "BundleProduct",
            "RecommendationScore",
            "Lift",
            "Confidence",
        ],
    )


def evaluate_recommendations(
    recs: pd.DataFrame, test_orders: pd.DataFrame, topk: int
) -> Dict[str, float]:
    rec_map = recs.groupby("ShopMemberId")["ProductId"].apply(list).to_dict()
    test_map = test_orders.groupby("ShopMemberId")["ProductId"].apply(set).to_dict()

    hit_rates = []
    precisions = []
    recalls = []
    ndcgs = []

    for user_id, truth in test_map.items():
        if user_id not in rec_map:
            continue
        rec_list = rec_map[user_id][:topk]
        hits = [1 if item in truth else 0 for item in rec_list]
        hit = 1.0 if sum(hits) > 0 else 0.0
        precision = sum(hits) / topk
        recall = sum(hits) / max(len(truth), 1)

        dcg = 0.0
        for idx, h in enumerate(hits):
            if h:
                dcg += 1.0 / np.log2(idx + 2)
        ideal_hits = min(len(truth), topk)
        idcg = sum(1.0 / np.log2(i + 2) for i in range(ideal_hits))
        ndcg = dcg / idcg if idcg > 0 else 0.0

        hit_rates.append(hit)
        precisions.append(precision)
        recalls.append(recall)
        ndcgs.append(ndcg)

    def safe_mean(values: List[float]) -> float:
        return float(np.mean(values)) if values else 0.0

    return {
        "HitRate@10": safe_mean(hit_rates),
        "Precision@10": safe_mean(precisions),
        "Recall@10": safe_mean(recalls),
        "NDCG@10": safe_mean(ndcgs),
    }


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    artifact_dir = Path(args.artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    shop_id = args.shop_id or detect_shop_id(data_dir)
    if not shop_id:
        raise ValueError("Unable to detect shop id. Use --shop-id.")

    metadata = load_metadata(artifact_dir / "metadata.json")
    user_df, product_df = load_feature_tables(output_dir)

    user_df = user_df.set_index("ShopMemberId").loc[metadata["user_ids"]].reset_index()
    product_df = product_df.set_index("ProductId").loc[metadata["product_ids"]].reset_index()

    model_type = metadata.get("model_type", "two_tower")
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    orders_train_path = data_dir / f"orders_train_{shop_id}.csv"
    orders_filtered_path = data_dir / f"Order_TS_filtered_{shop_id}.csv"
    if orders_train_path.exists():
        train_orders = load_orders(orders_train_path, shop_id)
    elif orders_filtered_path.exists():
        all_orders = load_orders(orders_filtered_path, shop_id)
        split_time = metadata.get("split_time")
        if split_time:
            split_ts = pd.to_datetime(split_time)
        else:
            split_ts = all_orders["OrderDateTime"].quantile(0.8)
        train_orders = all_orders[all_orders["OrderDateTime"] <= split_ts]
    else:
        raise FileNotFoundError("Missing orders data for purchase filtering")

    product_id_to_idx = {pid: idx for idx, pid in enumerate(metadata["product_ids"])}
    purchase_map = build_purchase_map(train_orders, product_id_to_idx)

    if model_type == "two_tower":
        user_numeric = user_df[metadata["user_numeric_cols"]].fillna(0).astype(float).values
        user_numeric = apply_standardization(
            user_numeric, metadata["user_mean"], metadata["user_std"]
        )
        item_numeric = product_df[metadata["item_numeric_cols"]].fillna(0).astype(float).values
        item_numeric = apply_standardization(
            item_numeric, metadata["item_mean"], metadata["item_std"]
        )

        text_cols = [f"text_emb_{i:03d}" for i in range(DEFAULT_TEXT_EMB_DIM)]
        item_text = product_df[text_cols].fillna(0).astype(float).values

        user_gender = encode_categories(user_df["Gender"], metadata["gender_map"])
        user_level = encode_categories(user_df["MemberCardLevel"], metadata["member_level_map"])

        model = TwoTowerModel(
            user_numeric_dim=len(metadata["user_numeric_cols"]),
            item_numeric_dim=len(metadata["item_numeric_cols"]),
            text_dim=metadata["text_dim"],
            gender_vocab_size=len(metadata["gender_map"]),
            member_level_vocab_size=len(metadata["member_level_map"]),
        ).to(device)
        model.load_state_dict(torch.load(artifact_dir / "model.pt", map_location=device))

        user_numeric_t = torch.tensor(user_numeric, dtype=torch.float32)
        user_gender_t = torch.tensor(user_gender, dtype=torch.long)
        user_level_t = torch.tensor(user_level, dtype=torch.long)
        item_numeric_t = torch.tensor(item_numeric, dtype=torch.float32)
        item_text_t = torch.tensor(item_text, dtype=torch.float32)

        recs = recommend_topk(
            model,
            user_numeric_t,
            user_gender_t,
            user_level_t,
            item_numeric_t,
            item_text_t,
            metadata["user_ids"],
            metadata["product_ids"],
            purchase_map,
            args.topk,
            device,
        )
    else:
        user_factors = np.load(artifact_dir / metadata["als_user_factors"])
        item_factors = np.load(artifact_dir / metadata["als_item_factors"])
        recs = recommend_topk_als(
            user_factors,
            item_factors,
            metadata["user_ids"],
            metadata["product_ids"],
            purchase_map,
            args.topk,
        )

    rec_path = output_dir / "recommendation.csv"
    recs.to_csv(rec_path, index=False)

    relation_path = data_dir / f"relation_product_{shop_id}.csv"
    if relation_path.exists():
        relation_df = load_relation_product(relation_path)
        bundle_df = build_bundle_recommendations(
            recs, relation_df, args.bundle_score, args.bundle_topn
        )
        bundle_df.to_csv(output_dir / "bundle_recommendation.csv", index=False)

    if args.evaluate:
        orders_test_path = data_dir / f"orders_test_{shop_id}.csv"
        if orders_test_path.exists():
            test_orders = load_orders(orders_test_path, shop_id)
        elif orders_filtered_path.exists():
            all_orders = load_orders(orders_filtered_path, shop_id)
            split_time = metadata.get("split_time")
            if split_time:
                split_ts = pd.to_datetime(split_time)
            else:
                split_ts = all_orders["OrderDateTime"].quantile(0.8)
            test_orders = all_orders[all_orders["OrderDateTime"] > split_ts]
        else:
            test_orders = None

        if test_orders is not None:
            metrics = evaluate_recommendations(recs, test_orders, args.topk)
            with (output_dir / "evaluation.json").open("w", encoding="utf-8") as f:
                json.dump(metrics, f, indent=2)

    print("Inference complete.")


if __name__ == "__main__":
    main()

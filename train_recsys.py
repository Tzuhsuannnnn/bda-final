from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import torch
from torch.utils.data import DataLoader, Dataset

from recsys_common import (
    DEFAULT_TEXT_EMB_DIM,
    build_interactions,
    build_product_features,
    build_user_features,
    detect_shop_id,
    load_member,
    load_orders,
    load_salepage,
    load_sessions,
    negative_sampling,
    save_metadata,
)
from recsys_model import TwoTowerModel

USER_NUMERIC_COLS = [
    "Age",
    "IsAppInstalled",
    "IsEnableEmail",
    "IsEnablePushNotification",
    "last_purchase_gap",
    "purchase_count",
    "total_spending",
    "view_count",
    "search_count",
    "cart_count",
    "checkout_count",
    "purchase_event_count",
    "cart_to_purchase_rate",
    "checkout_to_purchase_rate",
    "view_to_cart_rate",
]
USER_CAT_COLS = ["Gender", "MemberCardLevel"]
ITEM_NUMERIC_COLS = ["purchase_count", "avg_price"]
TEXT_EMB_COLS = [f"text_emb_{i:03d}" for i in range(DEFAULT_TEXT_EMB_DIM)]


def save_parquet(df: pd.DataFrame, path: Path) -> None:
    try:
        df.to_parquet(path, index=False)
    except Exception as exc:
        raise RuntimeError("pyarrow is required to write parquet files") from exc


class InteractionDataset(Dataset):
    def __init__(
        self,
        user_numeric: torch.Tensor,
        user_gender: torch.Tensor,
        user_level: torch.Tensor,
        item_numeric: torch.Tensor,
        item_text: torch.Tensor,
        user_idx: np.ndarray,
        item_idx: np.ndarray,
        labels: np.ndarray,
    ) -> None:
        self.user_numeric = user_numeric
        self.user_gender = user_gender
        self.user_level = user_level
        self.item_numeric = item_numeric
        self.item_text = item_text
        self.user_idx = user_idx
        self.item_idx = item_idx
        self.labels = labels

    def __len__(self) -> int:
        return len(self.labels)

    def __getitem__(self, idx: int):
        u = self.user_idx[idx]
        i = self.item_idx[idx]
        return (
            self.user_numeric[u],
            self.user_gender[u],
            self.user_level[u],
            self.item_numeric[i],
            self.item_text[i],
            self.labels[idx],
        )


def build_category_mapping(series: pd.Series) -> Tuple[Dict[str, int], List[int]]:
    values = series.fillna("UNK").astype(str)
    uniq = sorted(set(values))
    mapping = {"UNK": 0}
    mapping.update({v: i + 1 for i, v in enumerate(uniq) if v != "UNK"})
    encoded = [mapping.get(v, 0) for v in values]
    return mapping, encoded


def standardize_array(values: np.ndarray) -> Tuple[np.ndarray, List[float], List[float]]:
    mean = values.mean(axis=0)
    std = values.std(axis=0)
    std = np.where(std == 0, 1.0, std)
    scaled = (values - mean) / std
    return scaled, mean.tolist(), std.tolist()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train two-tower recommender")
    parser.add_argument("--data-dir", default=".")
    parser.add_argument("--output-dir", default=".")
    parser.add_argument("--artifact-dir", default="artifacts")
    parser.add_argument("--shop-id", default=None)
    parser.add_argument("--text-model", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument(
        "--model",
        default="auto",
        choices=["auto", "two_tower", "als"],
        help="auto tries two-tower then falls back to ALS",
    )
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch-size", type=int, default=1024)
    parser.add_argument("--lr", type=float, default=1e-3)
    parser.add_argument("--neg-ratio", type=int, default=4)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--als-factors", type=int, default=64)
    parser.add_argument("--als-iterations", type=int, default=20)
    parser.add_argument("--als-regularization", type=float, default=0.01)
    return parser.parse_args()


def train_als(
    interactions: pd.DataFrame,
    user_count: int,
    item_count: int,
    factors: int = 64,
    iterations: int = 20,
    regularization: float = 0.01,
):
    try:
        from implicit.als import AlternatingLeastSquares
        from scipy.sparse import coo_matrix
    except ImportError:
        raise ImportError("Please install 'implicit' and 'scipy' to use ALS fallback.")

    pos = interactions[interactions["label"] == 1]
    rows = pos["user_idx"].values
    cols = pos["item_idx"].values
    data = np.ones(len(pos), dtype=np.float32)

    matrix = coo_matrix((data, (rows, cols)), shape=(user_count, item_count)).tocsr()

    model = AlternatingLeastSquares(
        factors=factors, iterations=iterations, regularization=regularization, random_state=42
    )
    model.fit(matrix)

    return model.user_factors, model.item_factors


def main() -> None:
    args = parse_args()
    data_dir = Path(args.data_dir)
    output_dir = Path(args.output_dir)
    artifact_dir = Path(args.artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    artifact_dir.mkdir(parents=True, exist_ok=True)

    np.random.seed(args.seed)
    torch.manual_seed(args.seed)

    shop_id = args.shop_id or detect_shop_id(data_dir)
    if not shop_id:
        raise ValueError("Unable to detect shop id. Use --shop-id.")

    orders_train_path = data_dir / f"orders_train_{shop_id}.csv"
    orders_test_path = data_dir / f"orders_test_{shop_id}.csv"
    orders_all_path = data_dir / f"Order_TS_filtered_{shop_id}.csv"

    if orders_train_path.exists() and orders_test_path.exists():
        train_orders = load_orders(orders_train_path, shop_id)
        test_orders = load_orders(orders_test_path, shop_id)
        split_time = train_orders["OrderDateTime"].max()
    elif orders_all_path.exists():
        all_orders = load_orders(orders_all_path, shop_id)
        split_time = all_orders["OrderDateTime"].quantile(0.8)
        train_orders = all_orders[all_orders["OrderDateTime"] <= split_time]
        test_orders = all_orders[all_orders["OrderDateTime"] > split_time]
    else:
        raise FileNotFoundError("Missing order files for training.")

    member_path = data_dir / f"member_filtered_{shop_id}.csv"
    if not member_path.exists():
        member_path = data_dir / "Member.csv"

    salepage_path = data_dir / f"salepage_filtered_{shop_id}.csv"
    if not salepage_path.exists():
        salepage_path = data_dir / "SalePage.csv"

    sessions_train_path = data_dir / f"sessions_train_{shop_id}.csv"
    sessions_df = None
    if sessions_train_path.exists():
        sessions_df = load_sessions(sessions_train_path, shop_id)
    else:
        session_files = sorted(data_dir.glob("session01_*.csv"))
        if session_files:
            frames = []
            for path in session_files:
                chunk = load_sessions(path, shop_id)
                time_col = "EventTime" if "EventTime" in chunk.columns else "HitTime"
                if time_col in chunk.columns:
                    chunk = chunk[chunk[time_col] <= split_time]
                frames.append(chunk)
            if frames:
                sessions_df = pd.concat(frames, ignore_index=True)

    member_df = load_member(member_path, shop_id)
    salepage_df = load_salepage(salepage_path, shop_id)

    user_features = build_user_features(member_df, train_orders, sessions_df, split_time)
    user_features = user_features.drop_duplicates("ShopMemberId")

    product_features, text_method = build_product_features(
        train_orders, salepage_df, args.text_model
    )
    product_features = product_features.drop_duplicates("ProductId")

    save_parquet(user_features, output_dir / "user_feature.parquet")
    save_parquet(product_features, output_dir / "product_feature.parquet")

    interactions = build_interactions(train_orders)
    interactions = negative_sampling(
        interactions, product_features["ProductId"].tolist(), args.neg_ratio, args.seed
    )

    user_id_to_idx = {
        user_id: idx for idx, user_id in enumerate(user_features["ShopMemberId"].tolist())
    }
    product_id_to_idx = {
        prod_id: idx
        for idx, prod_id in enumerate(product_features["ProductId"].tolist())
    }

    interactions = interactions[
        interactions["ShopMemberId"].isin(user_id_to_idx)
        & interactions["ProductId"].isin(product_id_to_idx)
    ]

    interactions["user_idx"] = interactions["ShopMemberId"].map(user_id_to_idx)
    interactions["item_idx"] = interactions["ProductId"].map(product_id_to_idx)

    gender_map, gender_idx = build_category_mapping(user_features["Gender"])
    level_map, level_idx = build_category_mapping(user_features["MemberCardLevel"])

    user_numeric = user_features[USER_NUMERIC_COLS].fillna(0).astype(float).values
    user_numeric, user_mean, user_std = standardize_array(user_numeric)
    item_numeric = product_features[ITEM_NUMERIC_COLS].fillna(0).astype(float).values
    item_numeric, item_mean, item_std = standardize_array(item_numeric)
    item_text = product_features[TEXT_EMB_COLS].fillna(0).astype(float).values

    user_numeric_t = torch.tensor(user_numeric, dtype=torch.float32)
    user_gender_t = torch.tensor(gender_idx, dtype=torch.long)
    user_level_t = torch.tensor(level_idx, dtype=torch.long)
    item_numeric_t = torch.tensor(item_numeric, dtype=torch.float32)
    item_text_t = torch.tensor(item_text, dtype=torch.float32)

    dataset = InteractionDataset(
        user_numeric_t,
        user_gender_t,
        user_level_t,
        item_numeric_t,
        item_text_t,
        interactions["user_idx"].values,
        interactions["item_idx"].values,
        interactions["label"].values.astype(np.float32),
    )

    loader = DataLoader(
        dataset, 
        batch_size=args.batch_size, 
        shuffle=True, 
        num_workers=4, 
        pin_memory=torch.cuda.is_available()
    )

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

    model_type = "two_tower"
    model_path = artifact_dir / "model.pt"
    als_user_path = artifact_dir / "als_user_factors.npy"
    als_item_path = artifact_dir / "als_item_factors.npy"

    if args.model in ["auto", "two_tower"]:
        try:
            model = TwoTowerModel(
                user_numeric_dim=len(USER_NUMERIC_COLS),
                item_numeric_dim=len(ITEM_NUMERIC_COLS),
                text_dim=DEFAULT_TEXT_EMB_DIM,
                gender_vocab_size=len(gender_map),
                member_level_vocab_size=len(level_map),
            ).to(device)

            optimizer = torch.optim.Adam(model.parameters(), lr=args.lr)
            criterion = torch.nn.BCEWithLogitsLoss()
            scaler = torch.cuda.amp.GradScaler(enabled=torch.cuda.is_available())

            model.train()
            for epoch in range(args.epochs):
                running_loss = 0.0
                for batch in loader:
                    user_num, user_gender, user_level, item_num, item_text, labels = batch
                    user_num = user_num.to(device, non_blocking=True)
                    user_gender = user_gender.to(device, non_blocking=True)
                    user_level = user_level.to(device, non_blocking=True)
                    item_num = item_num.to(device, non_blocking=True)
                    item_text = item_text.to(device, non_blocking=True)
                    labels = labels.to(device, non_blocking=True)

                    optimizer.zero_grad()
                    with torch.amp.autocast("cuda", enabled=torch.cuda.is_available()):
                        logits = model(user_num, user_gender, user_level, item_num, item_text)
                        loss = criterion(logits, labels)
                    
                    scaler.scale(loss).backward()
                    scaler.step(optimizer)
                    scaler.update()
                    
                    running_loss += loss.item()


                avg_loss = running_loss / max(len(loader), 1)
                print(f"Epoch {epoch + 1}/{args.epochs} loss={avg_loss:.6f}")

            torch.save(model.state_dict(), model_path)
            print(f"Two-tower training complete. Model saved: {model_path}")
        except Exception as exc:
            if args.model == "two_tower":
                raise
            print(f"Two-tower training failed, falling back to ALS: {exc}")
            model_type = "als"

    if args.model == "als" or model_type == "als":
        model_type = "als"
        print("Training ALS model...")
        user_factors, item_factors = train_als(
            interactions,
            len(user_id_to_idx),
            len(product_id_to_idx),
            args.als_factors,
            args.als_iterations,
            args.als_regularization,
        )
        np.save(als_user_path, user_factors)
        np.save(als_item_path, item_factors)
        print(f"ALS training complete. Factors saved to {als_user_path} and {als_item_path}")

    metadata = {
        "model_type": model_type,
        "model_path": str(model_path.name) if model_type == "two_tower" else None,
        "als_user_factors": str(als_user_path.name) if model_type == "als" else None,
        "als_item_factors": str(als_item_path.name) if model_type == "als" else None,
        "shop_id": shop_id,
        "split_time": str(split_time),
        "user_numeric_cols": USER_NUMERIC_COLS,
        "item_numeric_cols": ITEM_NUMERIC_COLS,
        "text_dim": DEFAULT_TEXT_EMB_DIM,
        "gender_map": gender_map,
        "member_level_map": level_map,
        "user_mean": user_mean,
        "user_std": user_std,
        "item_mean": item_mean,
        "item_std": item_std,
        "text_method": text_method,
        "user_ids": user_features["ShopMemberId"].tolist(),
        "product_ids": product_features["ProductId"].tolist(),
    }
    save_metadata(artifact_dir / "metadata.json", metadata)
    print("Metadata saved.")


if __name__ == "__main__":
    main()

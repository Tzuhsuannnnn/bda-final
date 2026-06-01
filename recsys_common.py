from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd

DEFAULT_TEXT_EMB_DIM = 384


def detect_shop_id(data_dir: Path) -> Optional[str]:
    patterns = [
        "orders_train_*.csv",
        "orders_test_*.csv",
        "Order_TS_filtered_*.csv",
        "relation_product_*.csv",
        "member_filtered_*.csv",
        "salepage_filtered_*.csv",
    ]
    for pattern in patterns:
        for path in data_dir.glob(pattern):
            name = path.name
            for prefix in [
                "orders_train_",
                "orders_test_",
                "Order_TS_filtered_",
                "relation_product_",
                "member_filtered_",
                "salepage_filtered_",
            ]:
                if name.startswith(prefix) and name.endswith(".csv"):
                    return name[len(prefix) : -4]
    return None


def _detect_epoch_unit(series: pd.Series) -> Optional[str]:
    sample = series.dropna().astype(str).str.strip().head(1000)
    if sample.empty:
        return None
    if (sample.str.fullmatch(r"\d{13}").mean() or 0) >= 0.8:
        return "ms"
    if (sample.str.fullmatch(r"\d{10}").mean() or 0) >= 0.8:
        return "s"
    return None


def _parse_datetime_column(df: pd.DataFrame, col: str) -> None:
    if col not in df.columns:
        return
    unit = _detect_epoch_unit(df[col])
    if unit:
        df[col] = pd.to_datetime(df[col], errors="coerce", unit=unit)
    else:
        df[col] = pd.to_datetime(df[col], errors="coerce")


def _read_csv_select(path: Path, wanted_cols: Iterable[str]) -> pd.DataFrame:
    header = pd.read_csv(path, nrows=0, low_memory=False)
    available = [c for c in wanted_cols if c in header.columns]
    return pd.read_csv(
        path,
        usecols=available,
        dtype=object,
        on_bad_lines="skip",
        low_memory=False,
    )


def _clean_shop_id(df: pd.DataFrame) -> pd.DataFrame:
    if "ShopMemberId" in df.columns:
        df["ShopMemberId"] = df["ShopMemberId"].astype(str).str.strip()
    return df


def _ensure_numeric(df: pd.DataFrame, cols: Iterable[str]) -> None:
    for col in cols:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")


def load_orders(path: Path, shop_id: Optional[str] = None) -> pd.DataFrame:
    wanted_cols = [
        "ShopId",
        "ShopMemberId",
        "TradesGroupCode",
        "OrderDateTime",
        "OrderFinishDateTime",
        "ChannelType",
        "ChannelDetail",
        "PaymentType",
        "ShippingType",
        "OuterProductSkuCode",
        "ProductSkuCode",
        "SalePageId",
        "Qty",
        "UnitPrice",
        "SubtotalSalesAmount",
        "SubtotalPrice",
        "SubtotalPromotionDiscount",
        "SubtotalCouponDiscount",
        "SubtotalLoyaltyPointDiscount",
        "StatusDef",
    ]
    df = _read_csv_select(path, wanted_cols)

    if shop_id and "ShopId" in df.columns:
        df = df[df["ShopId"] == shop_id]

    _clean_shop_id(df)

    if "StatusDef" in df.columns:
        df = df[df["StatusDef"] == "Finish"]

    outer = df["OuterProductSkuCode"] if "OuterProductSkuCode" in df.columns else None
    inner = df["ProductSkuCode"] if "ProductSkuCode" in df.columns else None
    if outer is not None and inner is not None:
        df["ProductId"] = outer.fillna("").replace("", pd.NA)
        df["ProductId"] = df["ProductId"].fillna(inner)
    elif outer is not None:
        df["ProductId"] = outer
    elif inner is not None:
        df["ProductId"] = inner
    else:
        df["ProductId"] = pd.NA

    df = df.dropna(subset=["ShopMemberId", "ProductId"])

    _parse_datetime_column(df, "OrderDateTime")
    _parse_datetime_column(df, "OrderFinishDateTime")
    _ensure_numeric(df, ["Qty", "UnitPrice", "SubtotalSalesAmount", "SubtotalPrice"])

    return df


def load_sessions(path: Path, shop_id: Optional[str] = None) -> pd.DataFrame:
    wanted_cols = [
        "ShopId",
        "ShopMemberId",
        "Behavior",
        "EventTime",
        "HitTime",
        "SalePageId",
        "UnitPrice",
        "Qty",
        "TotalSalesAmount",
    ]
    df = _read_csv_select(path, wanted_cols)
    if shop_id and "ShopId" in df.columns:
        df = df[df["ShopId"] == shop_id]
    _clean_shop_id(df)
    _parse_datetime_column(df, "EventTime")
    _parse_datetime_column(df, "HitTime")
    _ensure_numeric(df, ["UnitPrice", "Qty", "TotalSalesAmount"])
    return df


def load_member(path: Path, shop_id: Optional[str] = None) -> pd.DataFrame:
    wanted_cols = [
        "ShopId",
        "ShopMemberId",
        "RegisterSourceTypeDef",
        "RegisterDateTime",
        "Gender",
        "Birthday",
        "APPRefereeId",
        "APPRefereeLocationId",
        "IsAppInstalled",
        "IsEnableEmail",
        "IsEnablePushNotification",
        "IsEnableShortMessage",
        "FirstAppOpenDateTime",
        "LastAppOpenDateTime",
        "MemberCardLevel",
        "CountryAliasCode",
    ]
    df = _read_csv_select(path, wanted_cols)
    if shop_id and "ShopId" in df.columns:
        df = df[df["ShopId"] == shop_id]
    _clean_shop_id(df)
    _parse_datetime_column(df, "RegisterDateTime")
    _parse_datetime_column(df, "FirstAppOpenDateTime")
    _parse_datetime_column(df, "LastAppOpenDateTime")
    _parse_datetime_column(df, "Birthday")
    return df


def load_salepage(path: Path, shop_id: Optional[str] = None) -> pd.DataFrame:
    wanted_cols = [
        "ShopId",
        "SalePageId",
        "SalePageTitle",
        "SaleProductDescShortContent",
    ]
    df = _read_csv_select(path, wanted_cols)
    if shop_id and "ShopId" in df.columns:
        df = df[df["ShopId"] == shop_id]
    return df


def build_user_features(
    member_df: pd.DataFrame,
    orders_df: pd.DataFrame,
    sessions_df: Optional[pd.DataFrame],
    reference_time: Optional[pd.Timestamp],
) -> pd.DataFrame:
    if reference_time is None:
        reference_time = orders_df["OrderDateTime"].max()

    if member_df is None or member_df.empty:
        members = pd.DataFrame({"ShopMemberId": orders_df["ShopMemberId"].unique()})
    else:
        members = member_df.copy()
        missing_ids = set(orders_df["ShopMemberId"].unique()) - set(
            members["ShopMemberId"].unique()
        )
        if missing_ids:
            missing_df = pd.DataFrame({"ShopMemberId": list(missing_ids)})
            members = pd.concat([members, missing_df], ignore_index=True)

    members["ShopMemberId"] = members["ShopMemberId"].astype(str)
    members["Gender"] = members.get("Gender", pd.Series(index=members.index)).fillna("UNK")
    members["MemberCardLevel"] = members.get(
        "MemberCardLevel", pd.Series(index=members.index)
    ).fillna("UNK")

    if "Birthday" in members.columns:
        age_days = (reference_time - members["Birthday"]).dt.days
        members["Age"] = (age_days / 365.25).round(2)
    else:
        members["Age"] = np.nan

    bool_cols = ["IsAppInstalled", "IsEnableEmail", "IsEnablePushNotification"]
    for col in bool_cols:
        if col in members.columns:
            members[col] = pd.to_numeric(members[col], errors="coerce").fillna(0)
        else:
            members[col] = 0

    orders = orders_df.copy()
    orders = orders.dropna(subset=["ShopMemberId", "OrderDateTime"])

    if "TradesGroupCode" in orders.columns:
        freq = orders.groupby("ShopMemberId")["TradesGroupCode"].nunique()
    else:
        freq = orders.groupby("ShopMemberId").size()

    last_purchase = orders.groupby("ShopMemberId")["OrderDateTime"].max()
    gap_days = (reference_time - last_purchase).dt.days

    if "SubtotalSalesAmount" in orders.columns:
        spending = orders.groupby("ShopMemberId")["SubtotalSalesAmount"].sum()
    elif "SubtotalPrice" in orders.columns:
        spending = orders.groupby("ShopMemberId")["SubtotalPrice"].sum()
    else:
        spending = (orders["UnitPrice"].fillna(0) * orders["Qty"].fillna(0)).groupby(
            orders["ShopMemberId"]
        ).sum()

    rfm = pd.DataFrame(
        {
            "last_purchase_gap": gap_days,
            "purchase_count": freq,
            "total_spending": spending,
        }
    ).reset_index()

    user_df = members.merge(rfm, on="ShopMemberId", how="left")

    if sessions_df is not None and not sessions_df.empty:
        behavior_map = {
            "ViewProduct": "view_count",
            "Search": "search_count",
            "AddToCart": "cart_count",
            "Checkout": "checkout_count",
            "Purchase": "purchase_event_count",
        }
        sessions = sessions_df[sessions_df["Behavior"].isin(behavior_map.keys())]
        counts = (
            sessions.groupby(["ShopMemberId", "Behavior"]).size().unstack(fill_value=0)
        )
        counts = counts.rename(columns=behavior_map).reset_index()
        user_df = user_df.merge(counts, on="ShopMemberId", how="left")
    else:
        for col in [
            "view_count",
            "search_count",
            "cart_count",
            "checkout_count",
            "purchase_event_count",
        ]:
            user_df[col] = 0

    for col in [
        "view_count",
        "search_count",
        "cart_count",
        "checkout_count",
        "purchase_event_count",
    ]:
        if col not in user_df.columns:
            user_df[col] = 0

    user_df["cart_to_purchase_rate"] = user_df["purchase_event_count"] / (
        user_df["cart_count"].replace(0, np.nan)
    )
    user_df["checkout_to_purchase_rate"] = user_df["purchase_event_count"] / (
        user_df["checkout_count"].replace(0, np.nan)
    )
    user_df["view_to_cart_rate"] = user_df["cart_count"] / (
        user_df["view_count"].replace(0, np.nan)
    )

    fill_zero = [
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
    for col in fill_zero:
        if col in user_df.columns:
            user_df[col] = user_df[col].fillna(0)

    user_df["Age"] = user_df["Age"].fillna(-1)

    return user_df


def _build_text_embeddings(texts: List[str], model_name: str) -> Tuple[np.ndarray, str]:
    texts = [t if isinstance(t, str) else "" for t in texts]
    try:
        from sentence_transformers import SentenceTransformer

        model = SentenceTransformer(model_name)
        embeddings = model.encode(
            texts,
            batch_size=64,
            show_progress_bar=True,
            normalize_embeddings=True,
        )
        method = "sentence-transformers"
    except Exception:
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer

            vectorizer = TfidfVectorizer(max_features=DEFAULT_TEXT_EMB_DIM)
            embeddings = vectorizer.fit_transform(texts).toarray()
            method = "tfidf"
        except Exception:
            embeddings = np.zeros((len(texts), DEFAULT_TEXT_EMB_DIM), dtype=np.float32)
            method = "zeros"

    if embeddings.shape[1] < DEFAULT_TEXT_EMB_DIM:
        pad_width = DEFAULT_TEXT_EMB_DIM - embeddings.shape[1]
        embeddings = np.pad(embeddings, ((0, 0), (0, pad_width)), mode="constant")
    elif embeddings.shape[1] > DEFAULT_TEXT_EMB_DIM:
        embeddings = embeddings[:, :DEFAULT_TEXT_EMB_DIM]

    return embeddings.astype(np.float32), method


def build_product_features(
    orders_df: pd.DataFrame,
    salepage_df: pd.DataFrame,
    embedding_model: str,
) -> Tuple[pd.DataFrame, str]:
    orders = orders_df.copy()
    orders = orders.dropna(subset=["ProductId"])

    purchase_count = orders.groupby("ProductId").size().rename("purchase_count")
    avg_price = orders.groupby("ProductId")["UnitPrice"].mean().rename("avg_price")

    product_df = pd.concat([purchase_count, avg_price], axis=1).reset_index()

    if "SalePageId" in orders.columns:
        mapping = (
            orders.dropna(subset=["SalePageId"])
            .groupby(["ProductId", "SalePageId"])
            .size()
            .reset_index(name="cnt")
            .sort_values(["ProductId", "cnt"], ascending=[True, False])
            .drop_duplicates("ProductId")
        )
        product_df = product_df.merge(
            mapping[["ProductId", "SalePageId"]], on="ProductId", how="left"
        )
    else:
        product_df["SalePageId"] = pd.NA

    salepage = salepage_df.copy()
    salepage["SalePageTitle"] = salepage.get("SalePageTitle", pd.Series()).fillna("")
    salepage["SaleProductDescShortContent"] = salepage.get(
        "SaleProductDescShortContent", pd.Series()
    ).fillna("")
    salepage["text"] = (
        salepage["SalePageTitle"].astype(str)
        + " "
        + salepage["SaleProductDescShortContent"].astype(str)
    ).str.strip()

    product_df = product_df.merge(
        salepage[["SalePageId", "text"]], on="SalePageId", how="left"
    )

    embeddings, method = _build_text_embeddings(
        product_df["text"].fillna("").tolist(), embedding_model
    )

    emb_cols = [f"text_emb_{i:03d}" for i in range(DEFAULT_TEXT_EMB_DIM)]
    emb_df = pd.DataFrame(embeddings, columns=emb_cols)

    product_df = pd.concat([product_df.drop(columns=["text"]), emb_df], axis=1)
    product_df["purchase_count"] = product_df["purchase_count"].fillna(0)
    product_df["avg_price"] = product_df["avg_price"].fillna(0)

    return product_df, method


def build_interactions(orders_df: pd.DataFrame) -> pd.DataFrame:
    interactions = orders_df[["ShopMemberId", "ProductId"]].dropna().drop_duplicates()
    interactions["label"] = 1
    return interactions


def negative_sampling(
    positive_df: pd.DataFrame,
    all_products: List[str],
    ratio: int = 4,
    seed: int = 42,
) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    product_array = np.array(all_products, dtype=object)

    user_groups = positive_df.groupby("ShopMemberId")["ProductId"].apply(set)
    negative_rows = []

    for user_id, pos_items in user_groups.items():
        pos_count = len(pos_items)
        if pos_count == 0:
            continue
        n_neg = min(ratio * pos_count, len(product_array) - pos_count)
        if n_neg <= 0:
            continue

        candidates = np.setdiff1d(product_array, np.array(list(pos_items)), assume_unique=False)
        if len(candidates) == 0:
            continue

        replace = len(candidates) < n_neg
        sampled = rng.choice(candidates, size=n_neg, replace=replace)
        for item_id in sampled:
            negative_rows.append((user_id, item_id, 0))

    if not negative_rows:
        return positive_df.copy()

    neg_df = pd.DataFrame(negative_rows, columns=["ShopMemberId", "ProductId", "label"])
    return pd.concat([positive_df, neg_df], ignore_index=True)


def save_metadata(path: Path, payload: Dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=True, indent=2)


def load_metadata(path: Path) -> Dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)

import argparse
import glob
import os
import tempfile
from typing import Dict, Iterable, List, Tuple

import pandas as pd


DEFAULT_PATTERN = "session01_202*.csv"
DEFAULT_SHOP_ID = "RZSHERLBqjPGOUFO01RYew=="
DEFAULT_CATEGORY_MAPPING = "RZSHERLBqjPGOUFO01RYew_category_mapping_multicat.csv"

NEEDED_COLUMNS = [
    "ShopId",
    "ShopMemberId",
    "EventTime",
    "HitTime",
    "Behavior",
    "CategoryId",
    "SalePageId",
    "UTMSource",
    "UTMMedium",
]

TRAFFIC_SOURCE_COLUMNS = ["UTMSource", "UTMMedium"]


def find_session_files(folder: str, pattern: str = DEFAULT_PATTERN) -> List[str]:
    paths = sorted(glob.glob(os.path.join(folder, pattern)))
    if not paths:
        raise FileNotFoundError(f"No files found for pattern: {os.path.join(folder, pattern)}")
    return paths


def load_category_mapping(path: str, shop_id: str) -> Dict[str, str]:
    if not os.path.exists(path):
        raise FileNotFoundError(f"Category mapping file not found: {path}")

    mapping_df = pd.read_csv(path, low_memory=False)
    required = {"ShopId", "SalePageId", "ProductCategory"}
    missing = required.difference(mapping_df.columns)
    if missing:
        raise KeyError(f"{os.path.basename(path)} missing required columns: {sorted(missing)}")

    mapping_df = mapping_df.copy()
    mapping_df["ShopId"] = mapping_df["ShopId"].astype("string").str.strip()
    mapping_df["SalePageId"] = mapping_df["SalePageId"].astype("string").str.strip()
    mapping_df["ProductCategory"] = mapping_df["ProductCategory"].astype("string").str.strip()

    mapping_df = mapping_df[
        (mapping_df["ShopId"] == shop_id)
        & mapping_df["SalePageId"].notna()
        & mapping_df["ProductCategory"].notna()
        & (mapping_df["SalePageId"] != "")
        & (mapping_df["ProductCategory"] != "")
    ].copy()

    mapping = (
        mapping_df.drop_duplicates(subset=["SalePageId"], keep="first")
        .set_index("SalePageId")["ProductCategory"]
        .to_dict()
    )
    return {str(k): str(v) for k, v in mapping.items()}


def read_session_file(path: str, shop_id: str, sale_page_category: Dict[str, str]) -> pd.DataFrame:
    df = pd.read_csv(
        path,
        usecols=lambda c: c in NEEDED_COLUMNS,
        dtype={
            "ShopId": "string",
            "ShopMemberId": "string",
            "Behavior": "string",
            "CategoryId": "string",
            "SalePageId": "string",
            "HitTime": "string",
            "UTMSource": "string",
            "UTMMedium": "string",
        },
        low_memory=False,
    )

    missing = [c for c in NEEDED_COLUMNS if c not in df.columns]
    if missing:
        raise KeyError(f"{os.path.basename(path)} missing required columns: {missing}")

    df["ShopId"] = df["ShopId"].astype("string").str.strip()
    df["EventTime"] = pd.to_numeric(df["EventTime"], errors="coerce")
    df = df.dropna(subset=["ShopId", "ShopMemberId", "EventTime"])
    df = df[df["ShopId"] == shop_id].copy()

    # Normalize SalePageId and ignore any CategoryId present in the session data.
    df["SalePageId"] = df["SalePageId"].astype("string").str.strip()
    df.loc[df["SalePageId"] == "", "SalePageId"] = pd.NA

    # Use only the provided sale_page_category mapping to populate CategoryId.
    # Do NOT use CategoryId values coming from the session files (they may be unreliable).
    df["CategoryId"] = pd.NA
    if sale_page_category:
        mapped = df["SalePageId"].map(sale_page_category)
        df["CategoryId"] = mapped.astype("string")

    for c in ("UTMSource", "UTMMedium"):
        if c in df.columns:
            df[c] = df[c].astype("string").str.strip()
            df.loc[df[c] == "", c] = pd.NA
        else:
            df[c] = pd.NA

    # Drop any rows that still lack a mapped CategoryId (we require mapping-derived categories).
    df = df[df["CategoryId"].notna() & df["SalePageId"].notna()].copy()
    df["Behavior_norm"] = df["Behavior"].astype("string").str.lower().str.strip()
    df = df[df["CategoryId"].notna()].copy()

    return df


def sessionize(df: pd.DataFrame, source_name: str, idle_minutes: int = 30) -> pd.DataFrame:
    if df.empty:
        return df

    df = df.copy()
    df["EventTime_dt"] = pd.to_datetime(df["EventTime"], unit="ms", errors="coerce")
    df = df.dropna(subset=["EventTime_dt"])
    df.sort_values(["ShopId", "ShopMemberId", "EventTime_dt"], inplace=True)

    df["prev_EventTime"] = (
        df.groupby(["ShopId", "ShopMemberId"], sort=False)["EventTime_dt"].shift(1)
    )

    df["diff_minutes"] = (
        df["EventTime_dt"] - df["prev_EventTime"]
    ).dt.total_seconds() / 60.0

    # previous UTM source/medium for traffic-source change detection
    for c in TRAFFIC_SOURCE_COLUMNS:
        df[f"prev_{c}"] = df.groupby(["ShopId", "ShopMemberId"], sort=False)[c].shift(1)

    # Rule 1: idle timeout
    cond_idle = df["diff_minutes"].isna() | (df["diff_minutes"] > idle_minutes)

    # Rule 2: date boundary (crossing midnight starts new session)
    cond_date_change = (
        df["prev_EventTime"].isna()
        | (df["EventTime_dt"].dt.date != df["prev_EventTime"].dt.date)
    )

    # Rule 3: traffic source change (UTM source/medium changed)
    cond_source_change = pd.Series(False, index=df.index)
    for c in TRAFFIC_SOURCE_COLUMNS:
        cond_source_change = cond_source_change | (df[c].fillna("") != df[f"prev_{c}"].fillna(""))

    df["new_session"] = cond_idle | cond_date_change | cond_source_change

    df["session_seq"] = (
        df.groupby(["ShopId", "ShopMemberId"], sort=False)["new_session"].cumsum()
    )

    df["session_id"] = (
        source_name
        + "::"
        + df["ShopId"].astype(str)
        + "::"
        + df["ShopMemberId"].astype(str)
        + "_s"
        + df["session_seq"].astype("int64").astype(str)
    )

    df.drop(
        columns=[
            "prev_EventTime",
            "diff_minutes",
            "new_session",
            "session_seq",
            "prev_UTMSource",
            "prev_UTMMedium",
        ],
        inplace=True,
    )

    return df


def add_dwell_seconds(df: pd.DataFrame, max_dwell_seconds: int = 1800) -> pd.DataFrame:
    df = df.copy()
    df.sort_values(["ShopId", "ShopMemberId", "session_id", "EventTime_dt"], inplace=True)

    df["next_EventTime"] = (
        df.groupby(["ShopId", "ShopMemberId", "session_id"], sort=False)["EventTime_dt"]
        .shift(-1)
    )

    df["dwell_seconds"] = (
        df["next_EventTime"] - df["EventTime_dt"]
    ).dt.total_seconds()

    df.loc[df["dwell_seconds"] < 0, "dwell_seconds"] = pd.NA
    df.loc[df["dwell_seconds"] > max_dwell_seconds, "dwell_seconds"] = max_dwell_seconds

    return df


def join_unique_nonnull(values: pd.Series) -> str:
    cleaned = values.dropna().astype("string").str.strip()
    cleaned = cleaned[cleaned != ""]
    if cleaned.empty:
        return ""
    return "|".join(pd.unique(cleaned.astype(str)))


def build_horizontal_features(df: pd.DataFrame) -> pd.DataFrame:
    output_columns = [
        "ShopId",
        "ShopMemberId",
        "session_id",
        "unique_category_count",
        "unique_salepage_count",
        "top_category_unique_salepage_count",
        "dominant_category_ratio",
        "avg_page_stay_time",
        "hesitation_category_ids",
        "hesitation_salepage_ids",
    ]

    if df.empty:
        return pd.DataFrame(columns=output_columns)

    work = add_dwell_seconds(df)

    base = work.groupby(["ShopId", "ShopMemberId", "session_id"], as_index=False).agg(
        unique_category_count=("CategoryId", "nunique"),
        unique_salepage_count=("SalePageId", "nunique"),
        avg_page_stay_time=("dwell_seconds", "mean"),
    )

    category_counts = (
        work.dropna(subset=["CategoryId", "SalePageId"])
        .groupby(["ShopId", "ShopMemberId", "session_id", "CategoryId"], as_index=False)
        .agg(category_unique_salepage_count=("SalePageId", "nunique"))
    )

    if category_counts.empty:
        top_category = pd.DataFrame(
            columns=["ShopId", "ShopMemberId", "session_id", "top_category_unique_salepage_count"]
        )
    else:
        top_category = category_counts.groupby(
            ["ShopId", "ShopMemberId", "session_id"], as_index=False
        ).agg(top_category_unique_salepage_count=("category_unique_salepage_count", "max"))

    session_items = work.groupby(
        ["ShopId", "ShopMemberId", "session_id"], as_index=False
    ).agg(
        hesitation_category_ids=("CategoryId", join_unique_nonnull),
        hesitation_salepage_ids=("SalePageId", join_unique_nonnull),
    )

    base = base.merge(
        top_category,
        on=["ShopId", "ShopMemberId", "session_id"],
        how="left",
    ).merge(
        session_items,
        on=["ShopId", "ShopMemberId", "session_id"],
        how="left",
    )

    base["avg_page_stay_time"] = base["avg_page_stay_time"].fillna(0)
    base["top_category_unique_salepage_count"] = base["top_category_unique_salepage_count"].fillna(0)
    base["dominant_category_ratio"] = base.apply(
        lambda row: (
            row["top_category_unique_salepage_count"] / row["unique_salepage_count"]
            if row["unique_salepage_count"] > 0
            else 0
        ),
        axis=1,
    )
    base["hesitation_category_ids"] = base["hesitation_category_ids"].fillna("")
    base["hesitation_salepage_ids"] = base["hesitation_salepage_ids"].fillna("")

    return base[output_columns]


def build_vertical_features(df: pd.DataFrame) -> pd.DataFrame:
    output_columns = [
        "ShopId",
        "ShopMemberId",
        "session_id",
        "SalePageId",
        "product_stay_time",
        "repeated_view_count",
    ]

    if df.empty:
        return pd.DataFrame(columns=output_columns)

    work = df.dropna(subset=["SalePageId"]).copy()
    if work.empty:
        return pd.DataFrame(columns=output_columns)

    product_stats = work.groupby(
        ["ShopId", "ShopMemberId", "session_id", "SalePageId"],
        as_index=False,
    ).agg(
        first_ts=("EventTime_dt", "min"),
        last_ts=("EventTime_dt", "max"),
        repeated_view_count=("SalePageId", "count"),
    )

    product_stats["product_stay_time"] = (
        product_stats["last_ts"] - product_stats["first_ts"]
    ).dt.total_seconds()

    return product_stats[output_columns]


def append_csv(df: pd.DataFrame, path: str) -> None:
    if df.empty:
        return

    write_header = not os.path.exists(path)
    df.to_csv(path, mode="a", header=write_header, index=False)


def write_empty_csv(path: str, columns: List[str]) -> None:
    if not os.path.exists(path):
        pd.DataFrame(columns=columns).to_csv(path, index=False)


def iter_csv_chunks(path: str, chunksize: int = 500_000) -> Iterable[pd.DataFrame]:
    if not os.path.exists(path):
        return

    yield from pd.read_csv(path, chunksize=chunksize, low_memory=False)


def compute_horizontal_overall_avg(horizontal_feature_path: str, chunksize: int) -> float:
    total = 0.0
    count = 0

    for chunk in iter_csv_chunks(horizontal_feature_path, chunksize):
        values = pd.to_numeric(chunk["avg_page_stay_time"], errors="coerce").dropna()
        total += values.sum()
        count += len(values)

    return total / count if count > 0 else 0.0


def filter_horizontal(
    horizontal_feature_path: str,
    out_path: str,
    overall_avg: float,
    min_unique_products: int = 5,
    max_category_count: int = 5,
    min_dominant_category_ratio: float = 0.75,
    min_stay_time: float = 15,
    max_stay_time: float = 45,
    chunksize: int = 500_000,
) -> int:
    if os.path.exists(out_path):
        os.remove(out_path)

    total_detected = 0

    for chunk in iter_csv_chunks(horizontal_feature_path, chunksize):
        for c in [
            "unique_category_count",
            "unique_salepage_count",
            "top_category_unique_salepage_count",
            "dominant_category_ratio",
            "avg_page_stay_time",
        ]:
            chunk[c] = pd.to_numeric(chunk[c], errors="coerce").fillna(0)

        detected = chunk[
            (chunk["unique_category_count"] >= 1)
            & (chunk["unique_category_count"] <= max_category_count)
            & (chunk["unique_salepage_count"] >= min_unique_products)
            & (chunk["avg_page_stay_time"] >= min_stay_time)
            & (chunk["avg_page_stay_time"] <= max_stay_time)
            & (chunk["dominant_category_ratio"] > min_dominant_category_ratio)
        ].copy()

        detected = detected[
            [
                "ShopId",
                "ShopMemberId",
                "session_id",
                "unique_category_count",
                "unique_salepage_count",
                "top_category_unique_salepage_count",
                "dominant_category_ratio",
                "avg_page_stay_time",
                "hesitation_category_ids",
                "hesitation_salepage_ids",
            ]
        ]

        append_csv(detected, out_path)
        total_detected += len(detected)

    write_empty_csv(
        out_path,
        [
            "ShopId",
            "ShopMemberId",
            "session_id",
            "unique_category_count",
            "unique_salepage_count",
            "top_category_unique_salepage_count",
            "dominant_category_ratio",
            "avg_page_stay_time",
            "hesitation_category_ids",
            "hesitation_salepage_ids",
        ],
    )

    return total_detected


def compute_product_avg_stay(
    vertical_feature_path: str,
    min_product_samples: int,
    chunksize: int,
) -> pd.DataFrame:
    sum_by_product = {}
    count_by_product = {}

    for chunk in iter_csv_chunks(vertical_feature_path, chunksize):
        chunk["product_stay_time"] = pd.to_numeric(
            chunk["product_stay_time"], errors="coerce"
        ).fillna(0)

        chunk = chunk.dropna(subset=["SalePageId"])

        grouped = chunk.groupby(["SalePageId"])["product_stay_time"].agg(["sum", "count"])

        for key, row in grouped.iterrows():
            sum_by_product[key] = sum_by_product.get(key, 0.0) + float(row["sum"])
            count_by_product[key] = count_by_product.get(key, 0) + int(row["count"])

    rows = []

    for key, cnt in count_by_product.items():
        if cnt >= min_product_samples:
            sale_page_id = key
            avg = sum_by_product[key] / cnt
            rows.append((sale_page_id, avg, cnt))

    return pd.DataFrame(
        rows,
        columns=["SalePageId", "avg_product_stay", "product_sample_count"],
    )


def filter_vertical(
    vertical_feature_path: str,
    out_path: str,
    product_avg: pd.DataFrame,
    repeated_view_threshold: int = 3,
    threshold_multiplier: float = 1.5,
    chunksize: int = 500_000,
) -> int:
    if os.path.exists(out_path):
        os.remove(out_path)

    if product_avg.empty:
        write_empty_csv(
            out_path,
            [
                "ShopId",
                "ShopMemberId",
                "session_id",
                "SalePageId",
                "product_stay_time",
                "repeated_view_count",
                "avg_product_stay",
                "dynamic_threshold",
            ],
        )
        return 0

    total_detected = 0

    product_avg = product_avg.copy()
    product_avg["dynamic_threshold"] = (
        product_avg["avg_product_stay"] * threshold_multiplier
    )

    for chunk in iter_csv_chunks(vertical_feature_path, chunksize):
        chunk["product_stay_time"] = pd.to_numeric(
            chunk["product_stay_time"], errors="coerce"
        ).fillna(0)

        chunk["repeated_view_count"] = pd.to_numeric(
            chunk["repeated_view_count"], errors="coerce"
        ).fillna(0)

        merged = chunk.merge(
            product_avg[["SalePageId", "avg_product_stay", "dynamic_threshold"]],
            on=["SalePageId"],
            how="left",
        )

        # Apply vertical rules:
        # 1) core: product_stay_time > dynamic_threshold
        # 2) repeated view: repeated_view_count >= repeated_view_threshold
        # 3) exclude hang: product_stay_time > 0
        # 4) exclude baseline insufficient: avg_product_stay must be present
        merged["avg_product_stay"] = merged["avg_product_stay"].where(
            pd.notna(merged["avg_product_stay"])
        )

        cond_baseline = merged["avg_product_stay"].notna()
        cond_positive_stay = merged["product_stay_time"] > 0
        cond_core = merged["product_stay_time"] > merged["dynamic_threshold"]
        cond_repeated = merged["repeated_view_count"] >= repeated_view_threshold

        detected = merged[cond_baseline & cond_positive_stay & (cond_core | cond_repeated)].copy()

        detected = detected[
            [
                "ShopId",
                "ShopMemberId",
                "session_id",
                "SalePageId",
                "product_stay_time",
                "repeated_view_count",
                "avg_product_stay",
                "dynamic_threshold",
            ]
        ]

        append_csv(detected, out_path)
        total_detected += len(detected)

    write_empty_csv(
        out_path,
        [
            "ShopId",
            "ShopMemberId",
            "session_id",
            "SalePageId",
            "product_stay_time",
            "repeated_view_count",
            "avg_product_stay",
            "dynamic_threshold",
        ],
    )

    return total_detected


def process_raw_files(
    paths: List[str],
    horizontal_feature_path: str,
    vertical_feature_path: str,
    idle_minutes: int,
    shop_id: str,
    sale_page_category: Dict[str, str],
) -> Tuple[int, int, int]:
    if os.path.exists(horizontal_feature_path):
        os.remove(horizontal_feature_path)

    if os.path.exists(vertical_feature_path):
        os.remove(vertical_feature_path)

    total_raw_rows = 0
    total_horizontal_rows = 0
    total_vertical_rows = 0

    for i, path in enumerate(paths, start=1):
        source_name = os.path.basename(path)
        print(f"[{i}/{len(paths)}] Processing {source_name} ...", flush=True)

        raw = read_session_file(path, shop_id=shop_id, sale_page_category=sale_page_category)
        print(f"  useful raw rows: {len(raw):,}", flush=True)

        total_raw_rows += len(raw)

        if raw.empty:
            continue

        sess = sessionize(raw, source_name=source_name, idle_minutes=idle_minutes)

        horizontal_features = build_horizontal_features(sess)
        vertical_features = build_vertical_features(sess)

        append_csv(horizontal_features, horizontal_feature_path)
        append_csv(vertical_features, vertical_feature_path)

        total_horizontal_rows += len(horizontal_features)
        total_vertical_rows += len(vertical_features)

        del raw, sess, horizontal_features, vertical_features

        print(
            f"  feature rows so far: horizontal={total_horizontal_rows:,}, vertical={total_vertical_rows:,}",
            flush=True,
        )

    return total_raw_rows, total_horizontal_rows, total_vertical_rows


def main(args: argparse.Namespace) -> None:
    paths = find_session_files(args.folder, args.pattern)
    print(f"Found {len(paths)} file(s).", flush=True)

    sale_page_category = load_category_mapping(args.category_mapping, args.shop_id)
    print(f"Loaded category mapping entries: {len(sale_page_category):,}", flush=True)

    temp_dir = args.temp_dir or tempfile.mkdtemp(prefix="hesitation_detection_")
    os.makedirs(temp_dir, exist_ok=True)

    horizontal_feature_path = os.path.join(temp_dir, "_horizontal_features.csv")
    vertical_feature_path = os.path.join(temp_dir, "_vertical_features.csv")

    print(f"Temporary feature folder: {temp_dir}", flush=True)

    total_raw, total_h_feat, total_v_feat = process_raw_files(
        paths=paths,
        horizontal_feature_path=horizontal_feature_path,
        vertical_feature_path=vertical_feature_path,
        idle_minutes=args.idle_minutes,
        shop_id=args.shop_id,
        sale_page_category=sale_page_category,
    )

    print("Computing global horizontal threshold ...", flush=True)
    overall_avg = compute_horizontal_overall_avg(
        horizontal_feature_path,
        chunksize=args.chunksize,
    )
    print(f"Overall average page stay time: {overall_avg:.2f} seconds", flush=True)

    print("Filtering horizontal hesitation users ...", flush=True)
    h_count = filter_horizontal(
        horizontal_feature_path=horizontal_feature_path,
        out_path=args.out_horizontal,
        overall_avg=overall_avg,
        min_unique_products=args.horizontal_min_unique_products,
        max_category_count=args.horizontal_max_category_count,
        min_dominant_category_ratio=args.horizontal_min_dominant_category_ratio,
        min_stay_time=args.horizontal_min_stay_time,
        max_stay_time=args.horizontal_max_stay_time,
        chunksize=args.chunksize,
    )

    print("Computing product dynamic thresholds ...", flush=True)
    product_avg = compute_product_avg_stay(
        vertical_feature_path=vertical_feature_path,
        min_product_samples=args.min_product_samples,
        chunksize=args.chunksize,
    )
    print(f"Products with valid dynamic thresholds: {len(product_avg):,}", flush=True)

    print("Filtering vertical hesitation users ...", flush=True)
    v_count = filter_vertical(
        vertical_feature_path=vertical_feature_path,
        out_path=args.out_vertical,
        product_avg=product_avg,
        repeated_view_threshold=args.vertical_repeated_view_threshold,
        threshold_multiplier=args.vertical_threshold_multiplier,
        chunksize=args.chunksize,
    )

    print("Done.", flush=True)
    print(f"Useful raw rows processed: {total_raw:,}", flush=True)
    print(f"Horizontal feature rows: {total_h_feat:,}", flush=True)
    print(f"Vertical feature rows: {total_v_feat:,}", flush=True)
    print(f"Horizontal detections: {h_count:,} saved to {args.out_horizontal}", flush=True)
    print(f"Vertical detections: {v_count:,} saved to {args.out_vertical}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Detect horizontal and vertical hesitation users from behavior CSV files."
    )

    parser.add_argument("--folder", type=str, default=".", help="Folder containing behavior/session CSV files")
    parser.add_argument("--pattern", type=str, default=DEFAULT_PATTERN, help="Glob pattern for behavior/session CSV files")
    parser.add_argument("--shop-id", type=str, default=DEFAULT_SHOP_ID, help="Only process this ShopId")
    parser.add_argument("--category-mapping", type=str, default=DEFAULT_CATEGORY_MAPPING, help="SalePageId to category mapping CSV")

    parser.add_argument("--out-horizontal", type=str, default="horizontal_hesitation_users.csv")
    parser.add_argument("--out-vertical", type=str, default="vertical_hesitation_users.csv")

    parser.add_argument("--temp-dir", type=str, default=None, help="Folder for temporary feature files")
    parser.add_argument("--chunksize", type=int, default=500_000, help="Chunk size for temporary feature CSV reading")

    parser.add_argument("--idle-minutes", type=int, default=30, help="Minutes of inactivity to start a new session")

    parser.add_argument("--horizontal-min-unique-products", type=int, default=5)
    parser.add_argument("--horizontal-max-category-count", type=int, default=5)
    parser.add_argument("--horizontal-min-dominant-category-ratio", type=float, default=0.75)
    parser.add_argument("--horizontal-min-stay-time", type=float, default=15)
    parser.add_argument("--horizontal-max-stay-time", type=float, default=45)

    parser.add_argument("--min-product-samples", type=int, default=5)
    parser.add_argument("--vertical-repeated-view-threshold", type=int, default=3)
    parser.add_argument("--vertical-threshold-multiplier", type=float, default=1.5)

    args = parser.parse_args()
    main(args)
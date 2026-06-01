import glob
import os
from typing import Iterable, List, Optional

import pandas as pd

SHOP_ID = "RZSHERLBqjPGOUFO01RYew=="
SPLIT_TS = pd.Timestamp("2023-10-23 12:59:04.377")
CHUNK_SIZE = 200_000

SESSION_TIME_COLS = ["EventTime", "HitTime"]
ORDER_TIME_COLS = ["OrderDateTime", "OrderFinishDateTime"]


def _pick_time_col(columns: Iterable[str], candidates: List[str]) -> Optional[str]:
    for name in candidates:
        if name in columns:
            return name
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


def _remove_if_exists(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


def _split_csv_by_time(
    input_files: List[str],
    output_train: str,
    output_test: str,
    shop_id: str,
    time_candidates: List[str],
    chunksize: int,
) -> None:
    _remove_if_exists(output_train)
    _remove_if_exists(output_test)

    total_train = 0
    total_test = 0
    total_drop = 0
    wrote_train = False
    wrote_test = False
    header_columns: Optional[List[str]] = None

    for file_path in input_files:
        time_col: Optional[str] = None
        epoch_unit: Optional[str] = None
        shop_missing_warned = False
        file_shop_rows = 0

        for i, chunk in enumerate(
            pd.read_csv(
                file_path,
                chunksize=chunksize,
                dtype=object,
                on_bad_lines="skip",
                low_memory=False,
            )
        ):
            if header_columns is None:
                header_columns = list(chunk.columns)

            if "ShopId" in chunk.columns:
                chunk = chunk[chunk["ShopId"] == shop_id]
            elif not shop_missing_warned:
                print(f"[WARN] ShopId column not found in {file_path}")
                shop_missing_warned = True

            file_shop_rows += len(chunk)

            if time_col is None:
                time_col = _pick_time_col(chunk.columns, time_candidates)
                if time_col is None:
                    raise ValueError(
                        f"No time column found in {file_path}. Tried: {time_candidates}"
                    )

            if epoch_unit is None:
                epoch_unit = _detect_epoch_unit(chunk[time_col])
                if epoch_unit:
                    print(
                        f"[INFO] Using time column {time_col} with epoch unit {epoch_unit} in {file_path}"
                    )
                elif i == 0:
                    print(f"[INFO] Using time column {time_col} in {file_path}")

            if epoch_unit:
                dt = pd.to_datetime(chunk[time_col], errors="coerce", unit=epoch_unit)
            else:
                dt = pd.to_datetime(chunk[time_col], errors="coerce")
            valid_mask = dt.notna()
            total_drop += int((~valid_mask).sum())

            train_mask = valid_mask & (dt <= SPLIT_TS)
            test_mask = valid_mask & (dt > SPLIT_TS)

            train_chunk = chunk[train_mask]
            test_chunk = chunk[test_mask]

            if not train_chunk.empty:
                train_chunk.to_csv(
                    output_train, mode="a", header=not os.path.exists(output_train), index=False
                )
                wrote_train = True
                total_train += len(train_chunk)

            if not test_chunk.empty:
                test_chunk.to_csv(
                    output_test, mode="a", header=not os.path.exists(output_test), index=False
                )
                wrote_test = True
                total_test += len(test_chunk)

            if (i + 1) % 10 == 0:
                print(
                    f"[INFO] {os.path.basename(file_path)} chunk {i+1} done. "
                    f"train={total_train:,} test={total_test:,} dropped={total_drop:,}"
                )

        if file_shop_rows == 0:
            print(f"[WARN] No rows matched ShopId in {file_path}")

    if header_columns and not wrote_train:
        pd.DataFrame(columns=header_columns).to_csv(output_train, index=False)
        print(f"[WARN] No train rows; created empty {os.path.basename(output_train)}")

    if header_columns and not wrote_test:
        pd.DataFrame(columns=header_columns).to_csv(output_test, index=False)
        print(f"[WARN] No test rows; created empty {os.path.basename(output_test)}")

    print(
        f"[DONE] {os.path.basename(output_train)}={total_train:,}, "
        f"{os.path.basename(output_test)}={total_test:,}, dropped={total_drop:,}"
    )


def _filter_csv_by_shop(
    input_file: str, output_file: str, shop_id: str, chunksize: int
) -> None:
    _remove_if_exists(output_file)
    total = 0

    for i, chunk in enumerate(
        pd.read_csv(
            input_file,
            chunksize=chunksize,
            dtype=object,
            on_bad_lines="skip",
            low_memory=False,
        )
    ):
        if "ShopId" in chunk.columns:
            chunk = chunk[chunk["ShopId"] == shop_id]
        else:
            raise ValueError(f"ShopId column not found in {input_file}")

        if not chunk.empty:
            chunk.to_csv(output_file, mode="a", header=not os.path.exists(output_file), index=False)
            total += len(chunk)

        if (i + 1) % 10 == 0:
            print(f"[INFO] {os.path.basename(input_file)} chunk {i+1} done. total={total:,}")

    print(f"[DONE] {os.path.basename(output_file)}={total:,}")


def main() -> None:
    session_files = sorted(glob.glob("session01_*.csv"))
    if not session_files:
        raise FileNotFoundError("No session01_*.csv files found.")

    order_file = f"Order_TS_filtered_{SHOP_ID}.csv"
    if not os.path.exists(order_file):
        raise FileNotFoundError(f"Missing {order_file}. Run data_cleaner.py first.")

    print("[STEP] Split sessions by time")
    _split_csv_by_time(
        session_files,
        output_train=f"sessions_train_{SHOP_ID}.csv",
        output_test=f"sessions_test_{SHOP_ID}.csv",
        shop_id=SHOP_ID,
        time_candidates=SESSION_TIME_COLS,
        chunksize=CHUNK_SIZE,
    )

    # print("[STEP] Split orders by time")
    # _split_csv_by_time(
    #     [order_file],
    #     output_train=f"orders_train_{SHOP_ID}.csv",
    #     output_test=f"orders_test_{SHOP_ID}.csv",
    #     shop_id=SHOP_ID,
    #     time_candidates=ORDER_TIME_COLS,
    #     chunksize=CHUNK_SIZE,
    # )

    # print("[STEP] Filter Member.csv")
    # _filter_csv_by_shop(
    #     "Member.csv",
    #     output_file=f"member_filtered_{SHOP_ID}.csv",
    #     shop_id=SHOP_ID,
    #     chunksize=CHUNK_SIZE,
    # )

    # print("[STEP] Filter SalePage.csv")
    # _filter_csv_by_shop(
    #     "SalePage.csv",
    #     output_file=f"salepage_filtered_{SHOP_ID}.csv",
    #     shop_id=SHOP_ID,
    #     chunksize=CHUNK_SIZE,
    # )


if __name__ == "__main__":
    main()

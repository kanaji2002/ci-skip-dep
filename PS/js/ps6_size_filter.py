#!/usr/bin/env python3
"""
ps6_size_filter.py

Input : ps5/ps5_filtered.csv
Check : size < 10MB (GitHub API の size カラムは KB 単位)
Output: ps6-size/ps6_size_filtered.csv
"""

import csv
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps5" / "ps5_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps6-size"
OUTPUT_CSV = OUTPUT_DIR / "ps6_size_filtered.csv"

SIZE_LIMIT_KB = 10 * 1024  # 10 MB


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS6-size: リポジトリサイズ < 10MB フィルタ")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print(f"閾値  : size < {SIZE_LIMIT_KB} KB (10 MB)")
    print("=" * 60 + "\n")

    passed = []
    failed = 0
    missing = 0

    for row in rows:
        raw = row.get("size", "").strip()
        if not raw:
            missing += 1
            continue
        try:
            size_kb = float(raw)
        except ValueError:
            missing += 1
            continue

        if size_kb < SIZE_LIMIT_KB:
            passed.append(row)
        else:
            failed += 1

    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(passed)

    print(f"Pass  : {len(passed)}")
    print(f"Fail  : {failed}  (size >= 10 MB)")
    print(f"Skip  : {missing}  (size 不明)")
    print(f"\n出力  : {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

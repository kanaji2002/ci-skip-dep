"""
PS1出力をフィルタリングするスクリプト (PS2→PS3→PS4の3段階)

PS2: 2024/3/1〜2026/3/1 に 10コミット以上
PS3: .github/workflows/*.yml が存在
PS4: GitHub Actions 実行履歴が 10件以上

Input:  ps1/js-repo.csv  (--input で変更可)
Output: ps2/ps2_{job_id}.csv
        ps3/ps3_{job_id}.csv
        ps4/ps4_{job_id}.csv

Usage:
    # ローカルで全件処理 (job_id=0)
    python3 ps2_ps4_filter.py

    # SLURMアレイジョブ用 (offset/limit で担当範囲を指定)
    python3 ps2_ps4_filter.py --input ps1/js-repo.csv --offset 50000 --limit 50000 --job-id 1
"""

import argparse
import csv
import time
import requests
import os
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

# ============================================================
# 設定
# ============================================================
GITHUB_TOKEN = os.environ["GITHUB_TOKEN_1"]
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

DATE_SINCE = "2024-03-01T00:00:00Z"
DATE_UNTIL = "2026-03-01T00:00:00Z"
DATE_RANGE = "2024-03-01..2026-03-01"

RATE_LIMIT_THRESHOLD = 50
RETRY_MAX = 5
RETRY_DELAY = 3


# ============================================================
# GitHub API 共通関数
# ============================================================
def github_get(url: str, params: dict = None):
    """
    GitHub API に GET リクエストを送る。
    レート制限対応・リトライ付き。
    戻り値: (data, status_code)
    """
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    for attempt in range(RETRY_MAX):
        try:
            resp = requests.get(url, headers=headers, params=params, timeout=30)

            remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
            if remaining < RATE_LIMIT_THRESHOLD:
                reset_at = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait_sec = max(reset_at - int(time.time()), 0) + 5
                print(f"  [Rate Limit] Remaining={remaining}. Waiting {wait_sec}s...")
                time.sleep(wait_sec)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  [429] Retry after {retry_after}s...")
                time.sleep(retry_after)
                continue

            if resp.status_code in (200, 404):
                try:
                    return resp.json(), resp.status_code
                except Exception:
                    return None, resp.status_code

            print(f"  [HTTP {resp.status_code}] {url} (attempt {attempt+1}/{RETRY_MAX})")
            time.sleep(RETRY_DELAY * (attempt + 1))

        except requests.exceptions.RequestException as e:
            print(f"  [Error] {e} (attempt {attempt+1}/{RETRY_MAX})")
            time.sleep(RETRY_DELAY * (attempt + 1))

    return None, -1


def parse_name(name: str):
    parts = name.strip().split("/")
    if len(parts) == 2:
        return parts[0], parts[1]
    return None, None


def load_csv(path: str):
    with open(path, newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def save_csv(rows: list, path: str):
    if not rows:
        print(f"  [Warning] 0件のため {path} は作成しません")
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    fieldnames = list(rows[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    print(f"  -> {path} に {len(rows)} 件を保存")


# ============================================================
# PS2: コミット数フィルタ
# ============================================================
def ps2_filter_commits(rows: list, output_csv: str):
    print("\n" + "="*60)
    print("PS2: コミット数フィルタ (2024/3/1〜2026/3/1 に 10件以上)")
    print("="*60)
    print(f"  入力: {len(rows)} 件")

    passed = []
    for i, row in enumerate(rows):
        owner, repo = parse_name(row["name"])
        if not owner:
            continue

        url = f"https://api.github.com/repos/{owner}/{repo}/commits"
        params = {
            "since": DATE_SINCE,
            "until": DATE_UNTIL,
            "per_page": 10,
        }
        data, status = github_get(url, params)

        if status == 200 and isinstance(data, list) and len(data) >= 10:
            passed.append(row)
            result = f"PASS ({len(data)} commits returned)"
        elif status == 404:
            result = "SKIP (repo not found)"
        else:
            result = f"FAIL (status={status}, commits={len(data) if isinstance(data, list) else 'N/A'})"

        print(f"  [{i+1}/{len(rows)}] {owner}/{repo} -> {result}")

    save_csv(passed, output_csv)
    print(f"\nPS2 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# PS3: .github/workflows フィルタ
# ============================================================
def ps3_filter_workflows(input_csv: str, output_csv: str):
    print("\n" + "="*60)
    print("PS3: .github/workflows フィルタ")
    print("="*60)

    rows = load_csv(input_csv)
    print(f"  入力: {len(rows)} 件")

    passed = []
    for i, row in enumerate(rows):
        owner, repo = parse_name(row["name"])
        if not owner:
            continue

        url = f"https://api.github.com/repos/{owner}/{repo}/contents/.github/workflows"
        data, status = github_get(url)

        if status == 200 and isinstance(data, list):
            yml_files = [f for f in data if f.get("name", "").endswith((".yml", ".yaml"))]
            if yml_files:
                passed.append(row)
                result = f"PASS ({len(yml_files)} workflow files)"
            else:
                result = "FAIL (no .yml/.yaml)"
        elif status == 404:
            result = "FAIL (not found)"
        else:
            result = f"FAIL (status={status})"

        print(f"  [{i+1}/{len(rows)}] {owner}/{repo} -> {result}")

    save_csv(passed, output_csv)
    print(f"\nPS3 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# PS4: CI実行履歴フィルタ
# ============================================================
def ps4_filter_ci_runs(input_csv: str, output_csv: str):
    print("\n" + "="*60)
    print("PS4: GitHub Actions 実行履歴フィルタ (10件以上)")
    print("="*60)

    rows = load_csv(input_csv)
    print(f"  入力: {len(rows)} 件")

    passed = []
    for i, row in enumerate(rows):
        owner, repo = parse_name(row["name"])
        if not owner:
            continue

        url = f"https://api.github.com/repos/{owner}/{repo}/actions/runs"
        params = {
            "created": DATE_RANGE,
            "per_page": 1,
        }
        data, status = github_get(url, params)

        if status == 200 and isinstance(data, dict):
            total_count = data.get("total_count", 0)
            if total_count >= 10:
                passed.append(row)
                result = f"PASS (total_count={total_count})"
            else:
                result = f"FAIL (total_count={total_count})"
        elif status == 404:
            result = "FAIL (not found)"
        else:
            result = f"FAIL (status={status})"

        print(f"  [{i+1}/{len(rows)}] {owner}/{repo} -> {result}")

    save_csv(passed, output_csv)
    print(f"\nPS4 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# メイン
# ============================================================
def parse_args():
    _ps1_csv = os.environ.get("PS1_CSV", "ps1/js-repo.csv")
    parser = argparse.ArgumentParser(description="PS2→PS3→PS4 フィルタリングパイプライン")
    parser.add_argument(
        "--input", default=_ps1_csv,
        help="入力 CSV ファイル (default: PS1_CSV env or ps1/js-repo.csv)",
    )
    parser.add_argument(
        "--offset", type=int, default=0,
        help="処理開始行 (0-indexed, default: 0)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="処理行数 (省略時: 全件)",
    )
    parser.add_argument(
        "--job-id", type=int, default=0,
        help="ジョブ ID - 出力ファイル名に使用 (default: 0)",
    )
    return parser.parse_args()


def main():
    args = parse_args()

    input_path = os.path.join(BASE_DIR, args.input) if not os.path.isabs(args.input) else args.input
    job_id = args.job_id
    PS2_CSV = os.path.join(BASE_DIR, "ps2", f"ps2_{job_id}.csv")
    PS3_CSV = os.path.join(BASE_DIR, "ps3", f"ps3_{job_id}.csv")
    PS4_CSV = os.path.join(BASE_DIR, "ps4", f"ps4_{job_id}.csv")

    print("="*60)
    print("repo-list フィルタリング パイプライン (PS2 -> PS3 -> PS4)")
    print("="*60)

    all_rows = load_csv(input_path)
    end = args.offset + args.limit if args.limit is not None else len(all_rows)
    rows = all_rows[args.offset:end]
    print(f"入力: {input_path}")
    print(f"対象: {args.offset}〜{min(end, len(all_rows)) - 1} 行 ({len(rows)} 件 / 全 {len(all_rows)} 件)")
    print(f"出力: ps2_{job_id}.csv / ps3_{job_id}.csv / ps4_{job_id}.csv")

    ps2_filter_commits(rows, PS2_CSV)
    if not os.path.exists(PS2_CSV):
        print("PS2結果が0件のためPS3/PS4をスキップします")
        return
    ps3_filter_workflows(PS2_CSV, PS3_CSV)
    if not os.path.exists(PS3_CSV):
        print("PS3結果が0件のためPS4をスキップします")
        return
    ps4_filter_ci_runs(PS3_CSV, PS4_CSV)

    print("\n" + "="*60)
    print("全ステップ完了")
    print("="*60)


if __name__ == "__main__":
    main()

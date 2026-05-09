"""
ps0_filtered.csv を3段階でフィルタリングするスクリプト

PS1: 2024/3/1〜2026/3/1 に 10コミット以上 → ps1/ps1_filtered.csv
PS2: .github/workflows/*.yml が存在       → ps2/ps2_filtered.csv
PS3: GitHub Actions 実行履歴が 10件以上   → ps3/ps3_filtered.csv
"""

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

_input_arg = os.environ.get("INPUT_CSV", "ps0/ps0_filtered.csv")
INPUT_CSV = os.path.join(BASE_DIR, _input_arg) if not os.path.isabs(_input_arg) else _input_arg
PS1_CSV = os.path.join(BASE_DIR, "ps1", "ps1_filtered.csv")
PS2_CSV = os.path.join(BASE_DIR, "ps2", "ps2_filtered.csv")
PS3_CSV = os.path.join(BASE_DIR, "ps3", "ps3_filtered.csv")

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
# PS1: コミット数フィルタ
# ============================================================
def ps1_filter_commits(input_csv: str, output_csv: str):
    print("\n" + "="*60)
    print("PS1: コミット数フィルタ (2024/3/1〜2026/3/1 に 10件以上)")
    print("="*60)

    rows = load_csv(input_csv)
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
    print(f"\nPS1 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# PS2: .github/workflows フィルタ
# ============================================================
def ps2_filter_workflows(input_csv: str, output_csv: str):
    print("\n" + "="*60)
    print("PS2: .github/workflows フィルタ")
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
    print(f"\nPS2 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# PS3: CI実行履歴フィルタ
# ============================================================
def ps3_filter_ci_runs(input_csv: str, output_csv: str):
    print("\n" + "="*60)
    print("PS3: GitHub Actions 実行履歴フィルタ (10件以上)")
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
    print(f"\nPS3 完了: {len(rows)} -> {len(passed)} 件")


# ============================================================
# メイン
# ============================================================
def main():
    print("="*60)
    print("repo-list フィルタリング パイプライン (PS1 -> PS2 -> PS3)")
    print("="*60)
    print(f"入力: {INPUT_CSV}")

    ps1_filter_commits(INPUT_CSV, PS1_CSV)
    ps2_filter_workflows(PS1_CSV, PS2_CSV)
    ps3_filter_ci_runs(PS2_CSV, PS3_CSV)

    print("\n" + "="*60)
    print("全ステップ完了")
    print("="*60)


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
# step1_project_selection/filter_projects.py
"""
PS5フィルタリング: npmプロジェクトを選別

PS5: package.json が存在するか (npm project)

チェックポイント機能付き（50件ごとに保存、中断しても再開可能）。
"""

import argparse
import csv
import os
import sys
import time

import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config

CHECKPOINT_INTERVAL = 50


def get_auth_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def wait_for_rate_limit(response: requests.Response, token: str):
    """レート制限残数が100以下の場合、リセットまで待機"""
    remaining = int(response.headers.get("X-RateLimit-Remaining", 999))
    if remaining <= 100:
        reset_at = int(response.headers.get("X-RateLimit-Reset", 0))
        wait_seconds = max(reset_at - int(time.time()), 0) + 5
        print(f"  Rate limit low ({remaining} remaining). Waiting {wait_seconds}s...")
        time.sleep(wait_seconds)


def check_ps5(owner: str, repo: str, token: str) -> bool:
    """PS5: package.json の存在確認"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/package.json"
    headers = get_auth_headers(token)

    for attempt in range(config.API_RETRY_MAX):
        try:
            resp = requests.get(url, headers=headers, timeout=config.API_TIMEOUT)
            wait_for_rate_limit(resp, token)

            if resp.status_code == 200:
                return True
            elif resp.status_code == 404:
                return False
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  Rate limit 429. Waiting {retry_after}s...")
                time.sleep(retry_after)
                continue
            elif resp.status_code in (401, 403):
                time.sleep(config.API_RETRY_DELAY * (attempt + 1))
                continue
            else:
                print(f"  PS5 unexpected status {resp.status_code} for {owner}/{repo}")
                return False
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"  PS5 network error for {owner}/{repo}: {e}")
            time.sleep(config.API_RETRY_DELAY * (attempt + 1))
            continue
        except Exception as e:
            print(f"  PS5 unexpected error for {owner}/{repo}: {e}")
            return False

    print(f"  PS5 max retries exceeded for {owner}/{repo}")
    return False


def load_checkpoint(checkpoint_path: str):
    """チェックポイントから処理済みリポジトリ名セットを読み込む"""
    processed = set()
    if os.path.exists(checkpoint_path):
        with open(checkpoint_path, "r") as f:
            reader = csv.DictReader(f)
            for row in reader:
                processed.add(row["name"])
    return processed


def save_rows(output_path: str, rows: list, fieldnames: list):
    """結果を書き出す（上書き）"""
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(description="PS5 filtering for npm projects")
    parser.add_argument(
        "--limit", type=int, default=None,
        help="Limit number of projects to check (for testing)",
    )
    parser.add_argument(
        "--input", type=str, default=None,
        help="Input CSV path (overrides REPO_LIST_PATH env var)",
    )
    parser.add_argument(
        "--output", type=str, default=None,
        help="Output CSV path (default: derived from input path)",
    )
    args = parser.parse_args()

    input_path = args.input or config.PROJECT_LIST_PATH
    if input_path is None:
        print("Error: No input CSV specified. Use --input or set REPO_LIST_PATH.")
        sys.exit(1)

    output_path = args.output or config.get_filtered_project_list_path(input_path)

    # 入力CSV読み込み
    print(f"Loading projects from {input_path}")
    with open(input_path, "r") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        all_rows = list(reader)

    if args.limit is not None:
        all_rows = all_rows[: args.limit]

    total = len(all_rows)
    print(f"Total projects to check: {total}")
    print(f"Output: {output_path}")

    # チェックポイント読み込み
    processed_names = load_checkpoint(output_path)

    # 既存の結果を読み込む（追記用）
    passed_rows = []
    if os.path.exists(output_path):
        with open(output_path, "r") as f:
            reader = csv.DictReader(f)
            passed_rows = list(reader)
        print(f"Resuming: {len(processed_names)} already processed, {len(passed_rows)} passed so far")

    token = config.GITHUB_TOKENS[0]

    passed_count = len(passed_rows)
    skipped_count = 0
    failed_ps5 = 0
    new_since_checkpoint = 0

    for i, row in enumerate(all_rows):
        name = row["name"]

        if name in processed_names:
            skipped_count += 1
            continue

        parts = name.split("/")
        if len(parts) != 2:
            print(f"  [{i+1}/{total}] Invalid name format: {name}")
            processed_names.add(name)
            new_since_checkpoint += 1
            continue

        owner, repo = parts

        has_package_json = check_ps5(owner, repo, token)
        if not has_package_json:
            failed_ps5 += 1
            processed_names.add(name)
            new_since_checkpoint += 1
            if (i + 1) % 100 == 0:
                print(f"  [{i+1}/{total}] {name} — no package.json (PS5 fail)")
        else:
            passed_rows.append(row)
            passed_count += 1
            processed_names.add(name)
            new_since_checkpoint += 1
            print(f"  [{i+1}/{total}] {name} — PASS (PS5)")

        if new_since_checkpoint >= CHECKPOINT_INTERVAL:
            save_rows(output_path, passed_rows, fieldnames)
            new_since_checkpoint = 0
            print(f"  --- Checkpoint saved: {passed_count} passed / {len(processed_names)} processed ---")

    # 最終保存
    save_rows(output_path, passed_rows, fieldnames)

    print()
    print("=" * 60)
    print("Filtering complete!")
    print(f"  Total checked:  {total}")
    print(f"  Already done:   {skipped_count}")
    print(f"  Failed PS5:     {failed_ps5}")
    print(f"  Passed (PS5):   {passed_count}")
    print(f"  Output: {output_path}")
    print("=" * 60)


if __name__ == "__main__":
    main()

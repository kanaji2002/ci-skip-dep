#!/usr/bin/env python3
"""
PS5: .csproj ファイルの存在確認 (C# プロジェクト判定)

.csproj はプロジェクト名によってファイル名が変わるため、2ステップで検索する:
  1. ルートディレクトリ一覧から .csproj を探す
  2. 見つからない場合、Git Tree API (recursive) で全パスを検索

Input:  ps4/csharp-repo_ps4.csv  (--input で変更可)
Output: ps5/ps5_filtered.csv     (--output で変更可)

Usage:
    python3 ps5_filter.py
    python3 ps5_filter.py --input ps4/csharp-repo_ps4.csv
    python3 ps5_filter.py --limit 10
"""

import argparse
import csv
import os
import sys
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".env"))

GITHUB_TOKENS = [
    t
    for key in ["GITHUB_TOKEN_1", "GITHUB_TOKEN_2", "GITHUB_TOKEN_3", "GITHUB_TOKEN_4", "GITHUB_TOKEN_5"]
    if (t := os.environ.get(key))
]

API_RETRY_MAX = 5
API_RETRY_DELAY = 1
API_TIMEOUT = 30
CHECKPOINT_INTERVAL = 50

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
DEFAULT_INPUT = os.path.join(_SCRIPT_DIR, "ps4", "csharp-repo_ps4.csv")
DEFAULT_OUTPUT = os.path.join(_SCRIPT_DIR, "ps5", "ps5_filtered.csv")


def _auth_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _wait_rate_limit(resp: requests.Response):
    remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
    if remaining <= 100:
        reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
        wait = max(reset_at - int(time.time()), 0) + 5
        print(f"  Rate limit low ({remaining} remaining). Waiting {wait}s...", flush=True)
        time.sleep(wait)


def _get(url: str, token: str) -> requests.Response | None:
    """GET リクエスト with リトライ。成功レスポンスを返す。失敗時は None。"""
    headers = _auth_headers(token)
    for attempt in range(API_RETRY_MAX):
        try:
            resp = requests.get(url, headers=headers, timeout=API_TIMEOUT)
            _wait_rate_limit(resp)

            if resp.status_code == 200:
                return resp
            elif resp.status_code == 404:
                return resp  # 呼び出し元で判断
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  Rate limit 429. Waiting {retry_after}s...", flush=True)
                time.sleep(retry_after)
                continue
            elif resp.status_code in (401, 403):
                time.sleep(API_RETRY_DELAY * (attempt + 1))
                continue
            else:
                return resp
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"  Network error ({url}): {e}", flush=True)
            time.sleep(API_RETRY_DELAY * (attempt + 1))
        except Exception as e:
            print(f"  Unexpected error ({url}): {e}", flush=True)
            return None
    return None


def check_csproj(owner: str, repo: str, default_branch: str, token: str) -> bool:
    """
    .csproj ファイルの存在を確認する。
    Step1: ルートディレクトリ一覧で .csproj を検索
    Step2: 見つからない場合は Git Tree API (recursive) で全パスを検索
    """
    # --- Step 1: ルートディレクトリ確認 ---
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/"
    resp = _get(url, token)
    if resp is not None and resp.status_code == 200:
        try:
            entries = resp.json()
            if isinstance(entries, list):
                for entry in entries:
                    if isinstance(entry, dict) and entry.get("name", "").endswith(".csproj"):
                        return True
        except Exception:
            pass
    elif resp is None:
        print(f"  Failed to get root contents for {owner}/{repo}", flush=True)
        return False

    # --- Step 2: Git Tree API (recursive) で全パスを検索 ---
    # まずデフォルトブランチの最新コミットから tree SHA を取得
    branch = default_branch or "main"
    commit_url = f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}"
    commit_resp = _get(commit_url, token)
    if commit_resp is None or commit_resp.status_code != 200:
        return False

    try:
        tree_sha = commit_resp.json()["commit"]["tree"]["sha"]
    except (KeyError, TypeError):
        return False

    tree_url = f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1"
    tree_resp = _get(tree_url, token)
    if tree_resp is None or tree_resp.status_code != 200:
        return False

    try:
        tree_data = tree_resp.json()
        for item in tree_data.get("tree", []):
            if item.get("path", "").endswith(".csproj"):
                return True
    except Exception:
        pass

    return False


def load_csv(path: str):
    with open(path, "r", newline="") as f:
        reader = csv.DictReader(f)
        return list(reader), reader.fieldnames


def load_checkpoint(output_path: str):
    processed: set[str] = set()
    passed_rows: list[dict] = []
    if os.path.exists(output_path):
        with open(output_path, "r", newline="") as f:
            reader = csv.DictReader(f)
            passed_rows = list(reader)
            for row in passed_rows:
                processed.add(row["name"])
    return processed, passed_rows


def save_rows(output_path: str, rows: list, fieldnames: list):
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with open(output_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main():
    parser = argparse.ArgumentParser(
        description="PS5: Filter projects that have .csproj (C# projects)"
    )
    parser.add_argument("--input", type=str, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=str, default=DEFAULT_OUTPUT)
    parser.add_argument("--limit", type=int, default=None)
    args = parser.parse_args()

    if not GITHUB_TOKENS:
        print("Error: No GitHub token found. Set GITHUB_TOKEN_1 (or _2.._5) env var.", flush=True)
        sys.exit(1)

    token = GITHUB_TOKENS[0]

    print("=" * 60, flush=True)
    print("PS5: C# project filtering (.csproj check)", flush=True)
    print("=" * 60, flush=True)

    all_rows, fieldnames = load_csv(args.input)
    print(f"Input:  {args.input} ({len(all_rows)} 件)", flush=True)

    if args.limit:
        all_rows = all_rows[:args.limit]

    total = len(all_rows)
    print(f"Output: {args.output}", flush=True)

    processed_names, passed_rows = load_checkpoint(args.output)
    if processed_names:
        print(
            f"Resuming: {len(processed_names)} already processed, "
            f"{len(passed_rows)} passed so far",
            flush=True,
        )

    passed_count = len(passed_rows)
    failed_count = 0
    new_since_checkpoint = 0

    for i, row in enumerate(all_rows):
        name = row.get("name", "")

        if name in processed_names:
            continue

        parts = name.split("/")
        if len(parts) != 2:
            print(f"  [{i+1}/{total}] Invalid name: {name}", flush=True)
            processed_names.add(name)
            new_since_checkpoint += 1
            continue

        owner, repo = parts
        default_branch = row.get("default_branch", "main")
        has_file = check_csproj(owner, repo, default_branch, token)

        processed_names.add(name)
        new_since_checkpoint += 1

        if has_file:
            passed_rows.append(row)
            passed_count += 1
            print(f"  [{i+1}/{total}] {name} — PASS", flush=True)
        else:
            failed_count += 1
            if (i + 1) % 100 == 0 or failed_count <= 5:
                print(f"  [{i+1}/{total}] {name} — no .csproj", flush=True)

        if new_since_checkpoint >= CHECKPOINT_INTERVAL:
            save_rows(args.output, passed_rows, fieldnames)
            new_since_checkpoint = 0
            print(
                f"  --- Checkpoint saved: {passed_count} passed / "
                f"{len(processed_names)} processed ---",
                flush=True,
            )

    save_rows(args.output, passed_rows, fieldnames)

    print(flush=True)
    print("=" * 60, flush=True)
    print("PS5 filtering complete!", flush=True)
    print(f"  Total checked:  {total}", flush=True)
    print(f"  Passed (PS5):   {passed_count}", flush=True)
    print(f"  Failed (PS5):   {failed_count}", flush=True)
    print(f"  Output: {args.output}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()

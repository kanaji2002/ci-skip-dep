#!/usr/bin/env python3
"""
PS4: Cargo.toml の存在確認 (Rust プロジェクト判定)

Input:  ps3/ps3_filtered.csv  (--input で変更可)
Output: ps4/ps4_filtered.csv  (--output で変更可)

Usage:
    python3 ps4_filter.py
    python3 ps4_filter.py --input ps3/ps3_filtered.csv
    python3 ps4_filter.py --limit 10
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
DEFAULT_INPUT = os.path.join(_SCRIPT_DIR, "ps3", "ps3_filtered.csv")
DEFAULT_OUTPUT = os.path.join(_SCRIPT_DIR, "ps4", "ps4_filtered.csv")


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


def check_cargo_toml(owner: str, repo: str, token: str) -> bool:
    """Cargo.toml の存在を GitHub API で確認"""
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/Cargo.toml"
    headers = _auth_headers(token)

    for attempt in range(API_RETRY_MAX):
        try:
            resp = requests.get(url, headers=headers, timeout=API_TIMEOUT)
            _wait_rate_limit(resp)

            if resp.status_code == 200:
                return True
            elif resp.status_code == 404:
                return False
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  Rate limit 429. Waiting {retry_after}s...", flush=True)
                time.sleep(retry_after)
                continue
            elif resp.status_code in (401, 403):
                time.sleep(API_RETRY_DELAY * (attempt + 1))
                continue
            else:
                print(f"  Unexpected status {resp.status_code} for {owner}/{repo}", flush=True)
                return False
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            print(f"  Network error for {owner}/{repo}: {e}", flush=True)
            time.sleep(API_RETRY_DELAY * (attempt + 1))
        except Exception as e:
            print(f"  Unexpected error for {owner}/{repo}: {e}", flush=True)
            return False

    print(f"  Max retries exceeded for {owner}/{repo}", flush=True)
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
        description="PS4: Filter projects that have Cargo.toml (Rust projects)"
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
    print("PS4: Rust project filtering (Cargo.toml check)", flush=True)
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
        has_file = check_cargo_toml(owner, repo, token)

        processed_names.add(name)
        new_since_checkpoint += 1

        if has_file:
            passed_rows.append(row)
            passed_count += 1
            print(f"  [{i+1}/{total}] {name} — PASS", flush=True)
        else:
            failed_count += 1
            if (i + 1) % 100 == 0 or failed_count <= 5:
                print(f"  [{i+1}/{total}] {name} — no Cargo.toml", flush=True)

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
    print("PS4 filtering complete!", flush=True)
    print(f"  Total checked:  {total}", flush=True)
    print(f"  Passed (PS4):   {passed_count}", flush=True)
    print(f"  Failed (PS4):   {failed_count}", flush=True)
    print(f"  Output: {args.output}", flush=True)
    print("=" * 60, flush=True)


if __name__ == "__main__":
    main()

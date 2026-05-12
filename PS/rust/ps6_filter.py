#!/usr/bin/env python3
"""
ps6_filter.py  (Rust)

Input : ps5/ps5_filtered.csv
Check : GitHub API でテストディレクトリの存在を確認
        - Git Trees API でリポジトリ全ファイルパスを取得
        - tests/ または test/ ディレクトリの有無を確認 (大文字小文字区別なし)
Output: ps6/ps6_filtered.csv  (通過分のみ)
        ps6/progress.log      (再開用)
"""

import argparse
import csv
import os
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps5" / "ps5_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps6"
OUTPUT_CSV = OUTPUT_DIR / "ps6_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"

load_dotenv(BASE_DIR / ".." / ".." / ".env")

GITHUB_TOKENS = []
_i = 1
while True:
    _t = os.environ.get(f"GITHUB_TOKEN_{_i}", "").strip()
    if not _t:
        break
    GITHUB_TOKENS.append(_t)
    _i += 1
if not GITHUB_TOKENS:
    _t = os.environ.get("GITHUB_TOKEN", "").strip()
    if _t:
        GITHUB_TOKENS.append(_t)

API_TIMEOUT = 30


def _auth_headers(token: str) -> dict:
    return {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "X-GitHub-Api-Version": "2022-11-28",
    }


def _wait_rate_limit(resp: requests.Response):
    remaining = int(resp.headers.get("X-RateLimit-Remaining", 999))
    if remaining <= 20:
        reset_at = int(resp.headers.get("X-RateLimit-Reset", 0))
        wait = max(reset_at - int(time.time()), 0) + 5
        print(f"  Rate limit low ({remaining} remaining). Waiting {wait}s...", flush=True)
        time.sleep(wait)


def _get(url: str, token: str) -> requests.Response | None:
    headers = _auth_headers(token)
    for attempt in range(5):
        try:
            resp = requests.get(url, headers=headers, timeout=API_TIMEOUT)
            _wait_rate_limit(resp)
            if resp.status_code in (200, 404):
                return resp
            elif resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  429 Rate limit. Waiting {retry_after}s...", flush=True)
                time.sleep(retry_after)
            elif resp.status_code in (401, 403):
                time.sleep(2 ** attempt)
            else:
                return resp
        except Exception:
            time.sleep(2 ** attempt)
    return None


def check_has_tests(owner: str, repo: str, default_branch: str, token: str) -> tuple[bool, str]:
    """
    Rust リポジトリに tests/ または test/ ディレクトリがあるか GitHub API で確認。
    ディレクトリ名の大文字小文字は区別しない。
    Returns (has_tests: bool, reason: str)
    """
    branch = default_branch or "main"

    # Step 1: デフォルトブランチの最新コミットから tree SHA を取得
    commit_resp = _get(
        f"https://api.github.com/repos/{owner}/{repo}/commits/{branch}", token
    )
    if commit_resp is None or commit_resp.status_code != 200:
        return False, "api_error_commit"

    try:
        tree_sha = commit_resp.json()["commit"]["tree"]["sha"]
    except (KeyError, TypeError):
        return False, "parse_error_commit"

    # Step 2: ツリーを再帰的に取得
    tree_resp = _get(
        f"https://api.github.com/repos/{owner}/{repo}/git/trees/{tree_sha}?recursive=1",
        token,
    )
    if tree_resp is None or tree_resp.status_code != 200:
        return False, "api_error_tree"

    try:
        paths = [
            item.get("path", "")
            for item in tree_resp.json().get("tree", [])
        ]
    except Exception:
        return False, "parse_error_tree"

    # Step 3: tests/ または test/ ディレクトリの存在を確認 (大文字小文字区別なし)
    for path in paths:
        p = path.lower()
        if p == "tests" or p.startswith("tests/") \
                or p == "test" or p.startswith("test/"):
            return True, "tests_dir"

    return False, "no_test_dir"


def load_progress() -> dict[str, str]:
    done: dict[str, str] = {}
    if PROGRESS.exists():
        for line in PROGRESS.read_text().splitlines():
            if "," in line:
                repo, _, status = line.partition(",")
                done[repo.strip()] = status.strip()
    return done


def save_progress(repo: str, status: str):
    with open(PROGRESS, "a") as f:
        f.write(f"{repo},{status}\n")


def main():
    global INPUT_CSV, OUTPUT_CSV, PROGRESS, OUTPUT_DIR
    parser = argparse.ArgumentParser()
    parser.add_argument("--input",    default=str(INPUT_CSV))
    parser.add_argument("--output",   default=str(OUTPUT_CSV))
    parser.add_argument("--progress", default=str(PROGRESS))
    parser.add_argument("--limit",    type=int, default=None)
    args = parser.parse_args()
    INPUT_CSV  = Path(args.input)
    OUTPUT_CSV = Path(args.output)
    PROGRESS   = Path(args.progress)
    OUTPUT_DIR = OUTPUT_CSV.parent

    if not GITHUB_TOKENS:
        print("ERROR: GitHub トークンが見つかりません。GITHUB_TOKEN を .env に設定してください。")
        return

    token = GITHUB_TOKENS[0]

    OUTPUT_DIR.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    if args.limit:
        rows = rows[:args.limit]

    print("=" * 60)
    print("PS6 (Rust): テスト存在チェック (GitHub API)")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print(f"Token : {token[:8]}...")
    print("=" * 60 + "\n")

    done = load_progress()

    out_exists = OUTPUT_CSV.exists() and OUTPUT_CSV.stat().st_size > 0
    outfile    = open(OUTPUT_CSV, "a", newline="", encoding="utf-8")
    writer     = csv.DictWriter(outfile, fieldnames=fieldnames)
    if not out_exists:
        writer.writeheader()

    passed = failed = skipped = 0

    for i, row in enumerate(rows, 1):
        repo = row["name"]
        print(f"[{i}/{len(rows)}] {repo}", flush=True)

        if repo in done:
            print(f"  skip ({done[repo]})")
            skipped += 1
            if done[repo].startswith("pass"):
                passed += 1
            continue

        parts = repo.split("/", 1)
        if len(parts) != 2:
            save_progress(repo, "fail_invalid_name")
            failed += 1
            continue

        owner, repo_name = parts
        default_branch = row.get("default_branch", "main")

        has_tests, reason = check_has_tests(owner, repo_name, default_branch, token)

        if has_tests:
            writer.writerow(row)
            outfile.flush()
            save_progress(repo, f"pass({reason})")
            passed += 1
            print(f"  => SAVED ({reason})")
        else:
            save_progress(repo, f"fail_{reason}")
            failed += 1
            print(f"  => SKIP ({reason})")

    outfile.close()
    print(f"\n=== PS6 (Rust) 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

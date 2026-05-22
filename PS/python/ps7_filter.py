#!/usr/bin/env python3
"""
ps7_filter.py  (Python)

Input : ps6/ps6_filtered.csv
Check : pyproject.toml に pytest の設定または依存が含まれるか (GitHub Contents API)
Output: ps7/ps7_filtered.csv  (通過分のみ)
        ps7/progress.log      (再開用)
"""

import csv
import itertools
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps6" / "ps6_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps7"
OUTPUT_CSV = OUTPUT_DIR / "ps7_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"

RETRY_MAX            = 3
RATE_LIMIT_THRESHOLD = 50

# pyproject.toml での pytest 依存指定を検出
#   TOML スタイル: pytest = "^7.0"
#   PEP 508 スタイル: "pytest>=7.0", "pytest==9.0.3", "pytest"
_PYTEST_DEP_RE = re.compile(
    r'(?m)^[ \t]*pytest[ \t]*=[ \t]*["\']'       # pytest = "^7.0"
    r'|["\']pytest(?:[>=<!^~][^\s"\']*)?["\']',   # "pytest>=7.0" / "pytest"
    re.IGNORECASE,
)


def _load_env(path: str) -> dict:
    result = {}
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    result[k.strip()] = v.strip()
    except FileNotFoundError:
        pass
    return result


_env    = _load_env(str(BASE_DIR / ".." / ".." / ".env"))
_tokens = [v for k, v in _env.items() if k.startswith("GITHUB_TOKEN") and v]
_cycle  = itertools.cycle(_tokens) if _tokens else None


def get_token() -> str:
    return next(_cycle) if _cycle else os.environ.get("GITHUB_TOKEN", "")


def github_get(url: str) -> tuple[bytes | None, int]:
    for attempt in range(RETRY_MAX):
        token = get_token()
        headers = {
            "User-Agent": "ps7-python-filter",
            "Accept":     "application/vnd.github.raw+json",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
                if remaining < RATE_LIMIT_THRESHOLD:
                    reset_at = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                    wait = max(reset_at - int(time.time()), 0) + 5
                    print(f"  [RateLimit] remaining={remaining}, wait {wait}s ...")
                    time.sleep(wait)
                return resp.read(), resp.status
        except urllib.error.HTTPError as e:
            if e.code in (404, 451):
                return None, e.code
            if e.code == 403:
                time.sleep(60)
                continue
            print(f"  [HTTP {e.code}] attempt {attempt+1}")
            time.sleep(5 * (attempt + 1))
        except Exception as e:
            print(f"  [Error] {e} attempt {attempt+1}")
            time.sleep(5 * (attempt + 1))
    return None, -1


def check_ps7(repo: str) -> tuple[bool, str]:
    """pyproject.toml に pytest の依存指定 (pytest==X.X / pytest>=X.X 等) があるか"""
    content, status = github_get(f"https://api.github.com/repos/{repo}/contents/pyproject.toml")
    if status == 200 and content:
        text = content.decode("utf-8", errors="replace")
        if bool(_PYTEST_DEP_RE.search(text)):
            return True, "pyproject.toml"
    return False, ""


def load_progress() -> dict[str, str]:
    done = {}
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
    OUTPUT_DIR.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS7 (Python): pytest 設定チェック")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["pytest_config"]
    out_exists = OUTPUT_CSV.exists() and OUTPUT_CSV.stat().st_size > 0
    outfile    = open(OUTPUT_CSV, "a", newline="", encoding="utf-8")
    writer     = csv.DictWriter(outfile, fieldnames=out_fields)
    if not out_exists:
        writer.writeheader()

    passed = failed = skipped = 0

    for i, row in enumerate(rows, 1):
        repo = row["name"]
        print(f"[{i}/{len(rows)}] {repo}", end=" ... ", flush=True)

        if repo in done:
            print(f"skip ({done[repo]})")
            skipped += 1
            if done[repo].startswith("pass"):
                passed += 1
            continue

        ok, reason = check_ps7(repo)

        if ok:
            row_out = dict(row)
            row_out["pytest_config"] = reason
            writer.writerow(row_out)
            outfile.flush()
            save_progress(repo, "pass")
            passed += 1
            print(f"PASS  ({reason})")
        else:
            save_progress(repo, "fail_no_pytest_config")
            failed += 1
            print("FAIL  (no pytest config)")

    outfile.close()

    print(f"\n=== PS7 (Python) 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""
ps7_filter.py

Input : ps6/ps6_filtered.csv
Check : package.json に実質的な test スクリプトがある（ダミー除外）
Output: ps7/ps7_filtered.csv  (通過分のみ)
        ps7/progress.log      (再開用)
"""

import csv
import itertools
import json
import os
import re
import time
import urllib.request
import urllib.error
from pathlib import Path

# ============================================================
# 設定
# ============================================================
BASE_DIR   = Path(__file__).parent
INPUT_CSV  = BASE_DIR / "ps6" / "ps6_filtered.csv"
OUTPUT_DIR = BASE_DIR / "ps7"
OUTPUT_CSV = OUTPUT_DIR / "ps7_filtered.csv"
PROGRESS   = OUTPUT_DIR / "progress.log"

RETRY_MAX            = 3
RATE_LIMIT_THRESHOLD = 50

_DUMMY_RE = re.compile(r"echo.*no test|exit 1", re.IGNORECASE)


# ============================================================
# トークンローテーション
# ============================================================
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


# ============================================================
# GitHub API
# ============================================================
def github_get(url: str) -> tuple[bytes | None, int]:
    for attempt in range(RETRY_MAX):
        token = get_token()
        headers = {
            "User-Agent": "ps7-filter",
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


# ============================================================
# PS7 判定
# ============================================================
def check_ps7(pkg_bytes: bytes) -> tuple[bool, str]:
    """(passed, test_script)"""
    try:
        pkg = json.loads(pkg_bytes.decode("utf-8", errors="replace"))
    except Exception:
        return False, ""
    test_script = (pkg.get("scripts") or {}).get("test", "")
    if not test_script:
        return False, ""
    return not bool(_DUMMY_RE.search(test_script)), test_script


# ============================================================
# 進捗管理
# ============================================================
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


# ============================================================
# メイン
# ============================================================
def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader     = csv.DictReader(f)
        fieldnames = list(reader.fieldnames)
        rows       = list(reader)

    print("=" * 60)
    print("PS7: test スクリプト チェック")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["test_script"]
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

        pkg_bytes, status = github_get(
            f"https://api.github.com/repos/{repo}/contents/package.json"
        )

        if status != 200 or pkg_bytes is None:
            save_progress(repo, f"fail_no_pkg(status={status})")
            print(f"no package.json (status={status})")
            failed += 1
            continue

        ok, test_script = check_ps7(pkg_bytes)

        if ok:
            row_out = dict(row)
            row_out["test_script"] = test_script
            writer.writerow(row_out)
            outfile.flush()
            save_progress(repo, f"pass")
            passed += 1
            print(f"PASS  test={test_script[:60]!r}")
        else:
            save_progress(repo, f"fail_dummy_or_missing")
            failed += 1
            print(f"FAIL  test={test_script[:60]!r}")

    outfile.close()

    print(f"\n=== PS7 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

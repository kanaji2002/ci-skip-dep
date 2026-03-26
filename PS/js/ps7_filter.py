#!/usr/bin/env python3
"""
ps7_filter.py

Input : ps6/ps6_filtered.csv
Check : README に coverage badge があり、かつ 70% 以上
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
BASE_DIR           = Path(__file__).parent
INPUT_CSV          = BASE_DIR / "ps6" / "ps6_filtered.csv"
OUTPUT_DIR         = BASE_DIR / "ps7"
OUTPUT_CSV         = OUTPUT_DIR / "ps7_filtered.csv"
PROGRESS           = OUTPUT_DIR / "progress.log"

COVERAGE_THRESHOLD   = 70.0
RETRY_MAX            = 3
RATE_LIMIT_THRESHOLD = 50

BADGE_PATTERNS = [
    re.compile(r"codecov\.io",                       re.IGNORECASE),
    re.compile(r"coveralls\.io",                     re.IGNORECASE),
    re.compile(r"img\.shields\.io[^\s'\"]*coverage", re.IGNORECASE),
]


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
# バッジ検出・数値取得
# ============================================================
def extract_badge_urls(text: str) -> list[str]:
    urls = []
    for m in re.finditer(r"!\[[^\]]*\]\(([^)\s]+)", text):
        url = m.group(1).strip()
        if any(p.search(url) for p in BADGE_PATTERNS):
            urls.append(url)
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE):
        url = m.group(1).strip()
        if any(p.search(url) for p in BADGE_PATTERNS):
            urls.append(url)
    return urls


def fetch_badge_pct(badge_url: str) -> float | None:
    # shields.io: .json エンドポイント
    if "img.shields.io" in badge_url:
        json_url = re.sub(r"\.(svg|png)(\?.*)?$", ".json", badge_url)
        try:
            req = urllib.request.Request(json_url, headers={"User-Agent": "ps7-filter"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(data.get("value", "")))
            if m:
                return float(m.group(1))
        except Exception:
            pass
    # SVG フォールバック
    try:
        req = urllib.request.Request(badge_url, headers={"User-Agent": "ps7-filter"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        for tm in re.finditer(r"<text[^>]*>([^<]*)</text>", content, re.IGNORECASE):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", tm.group(1))
            if m:
                return float(m.group(1))
    except Exception:
        pass
    return None


def check_ps7(readme_text: str) -> tuple[bool, float | None]:
    """(passed, coverage_pct)"""
    urls = extract_badge_urls(readme_text)
    if not urls:
        return False, None
    for url in urls:
        pct = fetch_badge_pct(url)
        if pct is not None:
            return pct >= COVERAGE_THRESHOLD, pct
    # バッジURLはあるが数値取得不可
    return False, None


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
    print(f"PS7: coverage badge >= {COVERAGE_THRESHOLD}% チェック")
    print(f"Input : {INPUT_CSV}  ({len(rows)} 件)")
    print(f"Output: {OUTPUT_CSV}")
    print("=" * 60 + "\n")

    done = load_progress()

    out_fields = fieldnames + ["coverage_badge_pct"]
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

        readme_bytes, status = github_get(
            f"https://api.github.com/repos/{repo}/readme"
        )

        if status != 200 or readme_bytes is None:
            save_progress(repo, f"fail_no_readme(status={status})")
            print(f"no readme (status={status})")
            failed += 1
            continue

        readme_text = readme_bytes.decode("utf-8", errors="replace")
        ok, pct = check_ps7(readme_text)

        if ok:
            row_out = dict(row)
            row_out["coverage_badge_pct"] = str(pct)
            writer.writerow(row_out)
            outfile.flush()
            save_progress(repo, f"pass({pct}%)")
            passed += 1
            print(f"PASS  {pct}%")
        else:
            pct_str = f"{pct}%" if pct is not None else "no_value"
            save_progress(repo, f"fail({pct_str})")
            failed += 1
            print(f"FAIL  {pct_str}")

    outfile.close()

    print(f"\n=== PS7 完了 ===")
    print(f"Total: {len(rows)}  Pass: {passed}  Fail: {failed}  Skip: {skipped}")
    print(f"Output: {OUTPUT_CSV}")


if __name__ == "__main__":
    main()

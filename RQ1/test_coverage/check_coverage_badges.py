#!/usr/bin/env python3
"""
Check GitHub repository README.md files for coverage badges.
Detects: codecov.io, coveralls.io, img.shields.io with coverage keyword.
"""

import csv
import os
import re
import time
import urllib.request
import urllib.error
import json
import itertools
from pathlib import Path

# --- Configuration ---
INPUT_CSV = Path("results_1-300k_step3.csv")
OUTPUT_DIR = Path("picked-up")
OUTPUT_CSV = OUTPUT_DIR / "results_with_coverage_badge.csv"
FILTERED_CSV = OUTPUT_DIR / "results_coverage_badge_true.csv"
HIGH_COVERAGE_CSV = OUTPUT_DIR / "results_coverage_75plus.csv"

COVERAGE_THRESHOLD = 75.0

# Load tokens from .env
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

_env = _load_env("/work/rintaro-k/research/.env")
GITHUB_TOKENS = [v for k, v in _env.items() if k.startswith("GITHUB_TOKEN") and v]
_token_cycle = itertools.cycle(GITHUB_TOKENS) if GITHUB_TOKENS else None

def get_token() -> str:
    if _token_cycle:
        return next(_token_cycle)
    return os.environ.get("GITHUB_TOKEN", "")

GITHUB_TOKEN = bool(GITHUB_TOKENS)  # used only for rate-limit sleep decision

# Coverage badge URL patterns (URL-based detection only)
COVERAGE_PATTERNS = [
    re.compile(r'codecov\.io', re.IGNORECASE),
    re.compile(r'coveralls\.io', re.IGNORECASE),
    re.compile(r'img\.shields\.io[^\s\'"]*coverage', re.IGNORECASE),
]


def has_coverage_badge(text: str) -> bool:
    """Return True if any coverage badge URL is found in the text."""
    for pattern in COVERAGE_PATTERNS:
        if pattern.search(text):
            return True
    return False


def extract_badge_urls(text: str) -> list[str]:
    """Extract coverage badge image URLs from README markdown/HTML."""
    urls = []
    # Markdown image: ![alt](url)
    for m in re.finditer(r'!\[[^\]]*\]\(([^)\s]+)', text):
        url = m.group(1).strip()
        if any(p.search(url) for p in COVERAGE_PATTERNS):
            urls.append(url)
    # HTML img: <img src="url" ...>
    for m in re.finditer(r'<img[^>]+src=["\']([^"\']+)["\']', text, re.IGNORECASE):
        url = m.group(1).strip()
        if any(p.search(url) for p in COVERAGE_PATTERNS):
            urls.append(url)
    return urls


def fetch_coverage_pct(badge_url: str) -> float | None:
    """
    Fetch a badge SVG and extract the numeric coverage percentage.
    Returns float (e.g. 85.0) or None if not parseable.
    """
    # shields.io supports a .json endpoint for structured data
    if "img.shields.io" in badge_url:
        json_url = re.sub(r"\.(svg|png)(\?.*)?$", ".json", badge_url)
        try:
            req = urllib.request.Request(json_url, headers={"User-Agent": "coverage-badge-checker"})
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read())
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(data.get("value", "")))
            if m:
                return float(m.group(1))
        except Exception:
            pass

    # Fall back to fetching the SVG and parsing the percentage from <text> elements only
    try:
        req = urllib.request.Request(badge_url, headers={"User-Agent": "coverage-badge-checker"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            content = resp.read().decode("utf-8", errors="replace")
        for text_m in re.finditer(r"<text[^>]*>([^<]*)</text>", content, re.IGNORECASE):
            m = re.search(r"(\d+(?:\.\d+)?)\s*%", text_m.group(1))
            if m:
                return float(m.group(1))
    except Exception:
        pass

    return None


def get_repo_coverage(readme_text: str) -> float | None:
    """Try to get a numeric coverage % from any coverage badge in the README."""
    for url in extract_badge_urls(readme_text):
        pct = fetch_coverage_pct(url)
        if pct is not None:
            return pct
    return None


def fetch_readme(repo_name: str) -> str | None:
    """
    Fetch README.md content for a GitHub repo.
    Returns the content string, or None if not found / error.
    """
    owner, repo = repo_name.split("/", 1)

    # Try GitHub API first (handles case-insensitive lookup)
    api_url = f"https://api.github.com/repos/{owner}/{repo}/readme"
    token = get_token()
    headers = {
        "Accept": "application/vnd.github.raw+json",
        "User-Agent": "coverage-badge-checker",
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(api_url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            if resp.status == 200:
                return resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None  # No README
        # Rate limit or other error — fall through
    except Exception:
        pass

    return None


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)

    # Read input CSV
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames
        rows = list(reader)

    print(f"Total repos: {len(rows)}")

    results = []
    coverage_true = []
    coverage_high = []

    for i, row in enumerate(rows, 1):
        repo_name = row["name"]
        print(f"[{i}/{len(rows)}] {repo_name}", end=" ... ", flush=True)

        readme_text = fetch_readme(repo_name)

        if readme_text is None:
            badge = False
            status = "no_readme"
            coverage_pct = None
        else:
            badge = has_coverage_badge(readme_text)
            status = "ok"
            coverage_pct = get_repo_coverage(readme_text) if badge else None

        row["coverage_badge"] = str(badge).lower()
        row["readme_status"] = status
        row["coverage_pct"] = "" if coverage_pct is None else str(coverage_pct)
        results.append(row)

        if badge:
            coverage_true.append(row)
            if coverage_pct is not None and coverage_pct >= COVERAGE_THRESHOLD:
                coverage_high.append(row)
                print(f"BADGE FOUND {coverage_pct}% >= {COVERAGE_THRESHOLD}% ({status})")
            else:
                pct_str = f"{coverage_pct}%" if coverage_pct is not None else "unknown"
                print(f"BADGE FOUND {pct_str} ({status})")
        else:
            print(f"none ({status})")

        # Respect GitHub API rate limit (60 req/h unauthenticated, 5000 authenticated)
        if not GITHUB_TOKEN:
            time.sleep(1.2)  # ~50 req/min to stay safe without token
        else:
            time.sleep(0.05)

    # Write full results
    out_fields = (fieldnames or []) + ["coverage_badge", "readme_status", "coverage_pct"]
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(results)

    # Write filtered results (badge = true only)
    with open(FILTERED_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(coverage_true)

    # Write high-coverage results (>= COVERAGE_THRESHOLD)
    with open(HIGH_COVERAGE_CSV, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=out_fields)
        writer.writeheader()
        writer.writerows(coverage_high)

    print(f"\n=== Done ===")
    print(f"Total:              {len(results)}")
    print(f"Coverage badge:     {len(coverage_true)}")
    print(f"Coverage >= {COVERAGE_THRESHOLD}%:  {len(coverage_high)}")
    print(f"Full results      → {OUTPUT_CSV}")
    print(f"Badge found       → {FILTERED_CSV}")
    print(f">= {COVERAGE_THRESHOLD}% coverage → {HIGH_COVERAGE_CSV}")


if __name__ == "__main__":
    main()

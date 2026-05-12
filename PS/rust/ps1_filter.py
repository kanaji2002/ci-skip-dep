"""
GitHub 上の Rust リポジトリを収集し、
ps1/ps1_filtered.csv に保存するスクリプト。

GitHub Search API は 1 クエリあたり最大 1000 件の制限があるため、
以下の3段階で再帰的に分割して全件取得する。
  第1段階: created 日付で二分割
  第2段階: 同一日で超過した場合は stars 範囲で二分割
  第3段階: 同一日・同一スター数で超過した場合は pushed 日付で二分割
"""

import csv
import itertools
import os
import time
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
load_dotenv(os.path.join(BASE_DIR, "..", "..", ".env"))

# ============================================================
# 設定
# ============================================================
# GITHUB_TOKEN_1 〜 GITHUB_TOKEN_N を優先して読み込む。
# 番号付きトークンがなければ GITHUB_TOKEN にフォールバック。
GITHUB_TOKENS = []
i = 1
while True:
    t = os.environ.get(f"GITHUB_TOKEN_{i}", "").strip()
    if not t:
        break
    GITHUB_TOKENS.append(t)
    i += 1

if not GITHUB_TOKENS:
    t = os.environ.get("GITHUB_TOKEN", "").strip()
    if t:
        GITHUB_TOKENS.append(t)

if not GITHUB_TOKENS:
    raise RuntimeError("GITHUB_TOKEN または GITHUB_TOKEN_1 が .env に見つかりません")

_token_cycle = itertools.cycle(GITHUB_TOKENS)

OUTPUT_CSV = os.path.join(BASE_DIR, "ps1", "ps1_filtered.csv")
SEARCH_URL = "https://api.github.com/search/repositories"

BASE_LANGUAGE = "language:Rust"
STAR_MIN = 5

# Search API: 認証済みで 30 req/min = 2秒/req per token
# N トークンで回すので SEARCH_INTERVAL = 2.0 / N まで短縮可能（最低 0.5 秒）
SEARCH_INTERVAL = max(0.5, 2.0 / len(GITHUB_TOKENS))

# GitHubサービス開始日
DATE_START = "2008-01-01"

FIELDNAMES = [
    "id", "name", "fork", "stars", "forks", "watchers",
    "language", "default_branch", "license", "homepage", "size",
    "open_issues", "created_at", "pushed_at", "updated_at",
    "description", "topics",
]


def build_query(date_start: str, date_end: str,
                star_min: int, star_max: int | None = None) -> str:
    """クエリ文字列を組み立てる。star_max=None は上限なし（stars:>=star_min）。"""
    stars_part = (
        f"stars:>={star_min}" if star_max is None else f"stars:{star_min}..{star_max}"
    )
    return f"{BASE_LANGUAGE} {stars_part} created:{date_start}..{date_end}"


# ============================================================
# GitHub Search API
# ============================================================
def github_search(query: str, page: int, per_page: int = 100):
    """
    GitHub Search API にリクエストを送る（トークンをラウンドロビン）。
    戻り値: (data, status_code)
    """
    token = next(_token_cycle)
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
    }
    params = {
        "q": query,
        "sort": "stars",
        "order": "asc",
        "per_page": per_page,
        "page": page,
    }
    for attempt in range(5):
        try:
            resp = requests.get(SEARCH_URL, headers=headers, params=params, timeout=30)

            remaining = int(resp.headers.get("X-RateLimit-Remaining", 9999))
            if remaining < 5:
                reset_at = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait_sec = max(reset_at - int(time.time()), 0) + 5
                print(f"  [Rate Limit] Remaining={remaining}. Waiting {wait_sec}s...")
                time.sleep(wait_sec)

            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 60))
                print(f"  [429] Retry after {retry_after}s...")
                time.sleep(retry_after)
                continue

            if resp.status_code == 422:
                print(f"  [422] Invalid query: {query}")
                return None, 422

            if resp.status_code == 200:
                return resp.json(), 200

            print(f"  [HTTP {resp.status_code}] (attempt {attempt+1}/5)")
            time.sleep(3 * (attempt + 1))

        except requests.exceptions.RequestException as e:
            print(f"  [Error] {e} (attempt {attempt+1}/5)")
            time.sleep(3 * (attempt + 1))

    return None, -1


def get_total_count(query: str) -> int:
    """クエリの total_count だけ取得する。失敗時は -1 を返す。"""
    data, status = github_search(query, page=1, per_page=1)
    time.sleep(SEARCH_INTERVAL)
    if status == 200 and data:
        return data.get("total_count", 0)
    return -1


# ============================================================
# データ変換
# ============================================================
def row_from_repo(repo: dict) -> dict:
    license_name = ""
    if repo.get("license"):
        license_name = repo["license"].get("name", "")
    topics = ",".join(repo.get("topics") or [])
    desc = (repo.get("description") or "").replace("\n", " ").replace("\r", "")
    return {
        "id": repo["id"],
        "name": repo["full_name"],
        "fork": repo.get("fork", False),
        "stars": repo.get("stargazers_count", 0),
        "forks": repo.get("forks_count", 0),
        "watchers": repo.get("watchers_count", 0),
        "language": repo.get("language", ""),
        "default_branch": repo.get("default_branch", ""),
        "license": license_name,
        "homepage": repo.get("homepage", ""),
        "size": repo.get("size", 0),
        "open_issues": repo.get("open_issues_count", 0),
        "created_at": repo.get("created_at", ""),
        "pushed_at": repo.get("pushed_at", ""),
        "updated_at": repo.get("updated_at", ""),
        "description": desc,
        "topics": topics,
    }


# ============================================================
# 収集ロジック
# ============================================================
def collect_query(query: str, seen_ids: set, writer) -> int:
    """
    1 クエリで最大 1000 件（100件 × 10ページ）を収集する。
    追加した件数を返す。
    """
    added = 0
    for page in range(1, 11):
        data, status = github_search(query, page=page, per_page=100)
        time.sleep(SEARCH_INTERVAL)

        if status != 200 or data is None:
            break

        items = data.get("items", [])
        if not items:
            break

        for repo in items:
            rid = repo["id"]
            if rid not in seen_ids:
                seen_ids.add(rid)
                writer.writerow(row_from_repo(repo))
                added += 1

        if len(items) < 100:
            break

    return added


def date_midpoint(start: str, end: str):
    """2つの日付文字列の中間日を返す。start == end の場合は None。"""
    d_start = datetime.strptime(start, "%Y-%m-%d")
    d_end = datetime.strptime(end, "%Y-%m-%d")
    if d_start >= d_end:
        return None
    mid = d_start + (d_end - d_start) // 2
    return mid.strftime("%Y-%m-%d")


def build_query_with_pushed(created_date: str, star_min: int, star_max: int | None,
                             pushed_start: str, pushed_end: str) -> str:
    """pushed: フィルタ付きのクエリを組み立てる。"""
    stars_part = (
        f"stars:>={star_min}" if star_max is None else f"stars:{star_min}..{star_max}"
    )
    return (
        f"{BASE_LANGUAGE} {stars_part}"
        f" created:{created_date}..{created_date}"
        f" pushed:{pushed_start}..{pushed_end}"
    )


def fetch_by_pushed(created_date: str, star_min: int, star_max: int | None,
                    pushed_start: str, pushed_end: str,
                    seen_ids: set, writer, depth: int = 0) -> int:
    """
    同一日・同一スター帯で 1000 件超のとき、pushed 日付を再帰的に二分割して全件収集する。
    pushed_start..pushed_end を中間日で分割し、total <= 1000 になるまで繰り返す。
    pushed が同一日まで絞っても 1000 件超の場合は最大 1000 件で打ち切り（Warning を出力）。
    """
    indent = "  " * depth
    query = build_query_with_pushed(created_date, star_min, star_max, pushed_start, pushed_end)
    total = get_total_count(query)
    print(f"{indent}  pushed:{pushed_start}..{pushed_end}  total={total}")

    if total <= 0:
        return 0

    if total <= 1000:
        n = collect_query(query, seen_ids, writer)
        print(f"{indent}    -> +{n} 件取得")
        return n

    mid = date_midpoint(pushed_start, pushed_end)
    if mid is None:
        # 同一日まで絞っても 1000 件超 → 打ち切り
        print(f"{indent}    [Warning] pushed:{pushed_start} 分割不可。最大1000件で取得。")
        n = collect_query(query, seen_ids, writer)
        print(f"{indent}    -> +{n} 件取得（打ち切り）")
        return n

    mid_next = (datetime.strptime(mid, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    n1 = fetch_by_pushed(created_date, star_min, star_max, pushed_start, mid,
                         seen_ids, writer, depth + 1)
    n2 = fetch_by_pushed(created_date, star_min, star_max, mid_next, pushed_end,
                         seen_ids, writer, depth + 1)
    return n1 + n2


def fetch_by_stars(date: str, star_min: int, star_max: int | None,
                   seen_ids: set, writer, depth: int = 0) -> int:
    """
    同一日内で stars 範囲を再帰的に二分割して全件収集する。
    star_max=None は上限なし（stars:>=star_min）。
    同一日・同一スター数で 1000 件超の場合は pushed 日付で三段階目の分割を行う。
    """
    indent = "  " * depth
    query = build_query(date, date, star_min, star_max)
    total = get_total_count(query)
    label = f"stars:>={star_min}" if star_max is None else f"stars:{star_min}..{star_max}"
    print(f"{indent}  [{label}]  total={total}")

    if total <= 0:
        return 0

    if total <= 1000:
        n = collect_query(query, seen_ids, writer)
        print(f"{indent}    -> +{n} 件取得")
        return n

    # 分割点を決める
    if star_max is None:
        # 指数的に上限を伸ばす: min=10→mid=21, min=22→mid=45, min=46→mid=93, ...
        mid = star_min * 2 + 1
        n1 = fetch_by_stars(date, star_min, mid, seen_ids, writer, depth + 1)
        n2 = fetch_by_stars(date, mid + 1, None, seen_ids, writer, depth + 1)
        return n1 + n2

    mid = (star_min + star_max) // 2
    if mid <= star_min:
        # 同一スター数でも 1000 件超 → pushed 日付で分割（第3段階）
        print(f"{indent}    [stars:{star_min} 分割不可] → pushed 日付で分割します。")
        date_end = datetime.utcnow().strftime("%Y-%m-%d")
        return fetch_by_pushed(date, star_min, star_max, DATE_START, date_end,
                               seen_ids, writer, depth + 1)

    n1 = fetch_by_stars(date, star_min, mid, seen_ids, writer, depth + 1)
    n2 = fetch_by_stars(date, mid + 1, star_max, seen_ids, writer, depth + 1)
    return n1 + n2


def fetch_recursive(date_start: str, date_end: str,
                    seen_ids: set, writer, depth: int = 0) -> int:
    """
    total_count > 1000 なら created 日付を二分割して再帰的に収集する。
    同一日で 1000 件を超える場合は stars 範囲で再帰的に二分割する。
    """
    indent = "  " * depth
    query = build_query(date_start, date_end, STAR_MIN)
    total = get_total_count(query)
    print(f"{indent}created:{date_start}..{date_end}  total={total}")

    if total <= 0:
        return 0

    if total <= 1000:
        n = collect_query(query, seen_ids, writer)
        print(f"{indent}  -> +{n} 件取得")
        return n

    # 1000件超 → 日付で二分割
    mid = date_midpoint(date_start, date_end)
    if mid is None:
        # 同一日 → stars 範囲で二分割して全件収集
        print(f"{indent}  [日付分割不可] {date_start} → stars 範囲で分割します。")
        return fetch_by_stars(date_start, STAR_MIN, None, seen_ids, writer, depth)

    mid_next = (datetime.strptime(mid, "%Y-%m-%d") + timedelta(days=1)).strftime("%Y-%m-%d")
    n1 = fetch_recursive(date_start, mid, seen_ids, writer, depth + 1)
    n2 = fetch_recursive(mid_next, date_end, seen_ids, writer, depth + 1)
    return n1 + n2


# ============================================================
# メイン
# ============================================================
def main():
    os.makedirs(os.path.join(BASE_DIR, "ps1"), exist_ok=True)

    print(f"[初期化] トークン {len(GITHUB_TOKENS)} 件 / SEARCH_INTERVAL={SEARCH_INTERVAL:.1f}s")

    # 既存 CSV があれば ID を読み込んで重複スキップ
    seen_ids: set = set()
    if os.path.exists(OUTPUT_CSV):
        with open(OUTPUT_CSV, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                try:
                    seen_ids.add(int(row["id"]))
                except (KeyError, ValueError):
                    pass
        print(f"既存 CSV から {len(seen_ids)} 件を読み込み（重複スキップ対象）")
        file_mode = "a"
    else:
        file_mode = "w"

    date_end = datetime.utcnow().strftime("%Y-%m-%d")

    print("=" * 60)
    print(f"クエリ  : {BASE_LANGUAGE} stars:>={STAR_MIN}")
    print(f"期間    : {DATE_START} .. {date_end}")
    print("=" * 60)

    with open(OUTPUT_CSV, file_mode, newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES)
        if file_mode == "w":
            writer.writeheader()

        total_added = fetch_recursive(DATE_START, date_end, seen_ids, writer)

    print("\n" + "=" * 60)
    print(f"収集完了: 合計 {total_added} 件")
    print(f"出力    : {OUTPUT_CSV}")
    print("=" * 60)


if __name__ == "__main__":
    main()

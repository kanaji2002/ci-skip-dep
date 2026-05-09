"""
ps8_filtered.csv の各リポジトリについて GitHub Contents API で package.json を取得し、
dependencies (runtime) と devDependencies の数を集計する。

出力: dependency_counts.csv
"""

import base64
import json
import os
import time
from pathlib import Path

import pandas as pd
import requests
from dotenv import load_dotenv

# --- 設定 ---
CSV_PATH = Path("/work/rintaro-k/research/PS/js/ps8/ps8_filtered.csv")
OUTPUT_PATH = Path(__file__).parent / "dependency_counts.csv"
ENV_PATH = Path("/work/rintaro-k/research/.env")

RATE_LIMIT_SLEEP = 0.5   # リクエスト間の待機秒数
MAX_RETRIES = 8          # 全トークン試行 + 余裕を持たせる
RETRY_DELAY = 5

# --- トークン読み込み ---
load_dotenv(ENV_PATH)
TOKENS = [
    os.environ[key]
    for key in ["GITHUB_TOKEN_1", "GITHUB_TOKEN_2", "GITHUB_TOKEN_3",
                "GITHUB_TOKEN_4", "GITHUB_TOKEN_5"]
    if key in os.environ
]
if not TOKENS:
    raise EnvironmentError(f".env にトークンが見つかりません: {ENV_PATH}")

print(f"トークン数: {len(TOKENS)}")

_token_index = 0
_bad_token_indices: set[int] = set()
_last_used_token_idx: int = -1


def _get_headers() -> dict:
    global _token_index, _last_used_token_idx
    for _ in range(len(TOKENS)):
        idx = _token_index % len(TOKENS)
        _token_index += 1
        if idx not in _bad_token_indices:
            _last_used_token_idx = idx
            return {
                "Authorization": f"token {TOKENS[idx]}",
                "Accept": "application/vnd.github.v3+json",
            }
    raise RuntimeError("有効なトークンがありません。全トークンが 401 エラーを返しました。")


def _mark_last_token_bad() -> None:
    if _last_used_token_idx >= 0:
        _bad_token_indices.add(_last_used_token_idx)
        remaining = len(TOKENS) - len(_bad_token_indices)
        print(f"  トークン[{_last_used_token_idx}] (GITHUB_TOKEN_{_last_used_token_idx + 1}) を無効としてマーク "
              f"(残り有効: {remaining})")


def fetch_package_json(owner: str, repo: str) -> tuple[dict | None, str]:
    """GitHub API で package.json の内容を取得して (dict, status) を返す。

    status: "ok" | "not_found" | "auth_error" | "error"
    """
    url = f"https://api.github.com/repos/{owner}/{repo}/contents/package.json"
    for attempt in range(MAX_RETRIES):
        try:
            resp = requests.get(url, headers=_get_headers(), timeout=15)
            if resp.status_code == 200:
                data = resp.json()
                content = base64.b64decode(data["content"]).decode("utf-8")
                return json.loads(content), "ok"
            elif resp.status_code == 404:
                return None, "not_found"  # package.json が存在しない
            elif resp.status_code == 401:
                # 無効なトークン → マークして次のトークンで再試行
                print(f"  401 認証エラー (attempt {attempt+1}): {owner}/{repo} — トークンを切り替えて再試行")
                _mark_last_token_bad()
                if len(_bad_token_indices) >= len(TOKENS):
                    return None, "auth_error"
                continue
            elif resp.status_code in (403, 429):
                reset = int(resp.headers.get("X-RateLimit-Reset", time.time() + 60))
                wait = max(reset - time.time(), 1)
                print(f"  Rate limit ({resp.status_code}), {wait:.0f}s 待機 ...")
                time.sleep(wait)
            else:
                print(f"  HTTP {resp.status_code}: {owner}/{repo}")
                return None, "error"
        except Exception as e:
            print(f"  エラー (attempt {attempt+1}): {e}")
            time.sleep(RETRY_DELAY)
    return None, "error"


def count_deps(pkg: dict | None) -> tuple[int, int]:
    """(runtime_deps, dev_deps) を返す。pkg が None の場合は (-1, -1)。"""
    if pkg is None:
        return -1, -1
    # dependencies が null のケースも安全に処理
    deps = pkg.get("dependencies") or {}
    dev_deps = pkg.get("devDependencies") or {}
    runtime = len(deps) if isinstance(deps, dict) else 0
    dev = len(dev_deps) if isinstance(dev_deps, dict) else 0
    return runtime, dev


def main():
    df = pd.read_csv(CSV_PATH)
    print(f"リポジトリ数: {len(df)}")

    # --- 既存結果の読み込み (再実行時に ok 済みをスキップ) ---
    existing: dict[str, dict] = {}
    if OUTPUT_PATH.exists():
        prev = pd.read_csv(OUTPUT_PATH)
        existing = {
            row["repo"]: row.to_dict()
            for _, row in prev.iterrows()
            if row["status"] == "ok"
        }
        skip_count = len(existing)
        retry_count = len(df) - skip_count
        print(f"既存結果: {skip_count} 件を再利用 / {retry_count} 件を再取得")

    results = []
    for i, row in df.iterrows():
        repo_full = row["name"]

        if repo_full in existing:
            results.append(existing[repo_full])
            print(f"[{i+1}/{len(df)}] {repo_full}: スキップ (ok 済み)")
            continue

        owner, repo = repo_full.split("/", 1)
        pkg, status = fetch_package_json(owner, repo)
        runtime_count, dev_count = count_deps(pkg)

        results.append({
            "repo": repo_full,
            "runtime_deps": runtime_count,
            "dev_deps": dev_count,
            "total_deps": max(runtime_count, 0) + max(dev_count, 0),
            "status": status,
        })

        print(f"[{i+1}/{len(df)}] {repo_full}: runtime={runtime_count}, dev={dev_count} ({status})")
        time.sleep(RATE_LIMIT_SLEEP)

    result_df = pd.DataFrame(results)
    result_df.to_csv(OUTPUT_PATH, index=False)
    print(f"\n完了: {OUTPUT_PATH}")

    # --- サマリー ---
    ok = result_df[result_df["status"] == "ok"]
    print(f"\n=== サマリー ===")
    print(f"取得成功:   {len(ok)} / {len(result_df)} リポジトリ")
    for s in ["not_found", "auth_error", "error"]:
        n = (result_df["status"] == s).sum()
        if n:
            print(f"{s:12s}: {n} 件")
    print(f"runtime_deps  — 平均: {ok['runtime_deps'].mean():.1f}, 中央値: {ok['runtime_deps'].median():.0f}, 最大: {ok['runtime_deps'].max()}")
    print(f"dev_deps      — 平均: {ok['dev_deps'].mean():.1f}, 中央値: {ok['dev_deps'].median():.0f}, 最大: {ok['dev_deps'].max()}")
    print(f"total_deps    — 平均: {ok['total_deps'].mean():.1f}, 中央値: {ok['total_deps'].median():.0f}, 最大: {ok['total_deps'].max()}")


if __name__ == "__main__":
    main()

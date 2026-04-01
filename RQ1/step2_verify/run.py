"""
Step 2: 未使用依存を削除してテスト実行
step1_results.csv を読み込み、各モデルが「未使用」と判定した依存を
実際に削除してテストが通るかを検証する。
出力: RQ1/output/step2_results.csv

使い方:
    python3 run.py
    python3 run.py --limit 5       # テスト用
    python3 run.py --skip 10       # 10件スキップして再開

判定ロジック:
    baseline=PASS, post_removal=PASS → 正しい検知 (削除しても壊れない)
    baseline=PASS, post_removal=FAIL → 誤検知   (削除すると壊れる)
    baseline=FAIL                    → 評価不能  (元々テストが壊れている)
    baseline=ERROR                   → 評価不能  (テスト実行自体が失敗)
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv
from git import Repo

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_ROOT, ".env"))

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
RQ1_DIR      = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR   = os.path.join(RQ1_DIR, "output")
CLONES_DIR   = os.path.join(OUTPUT_DIR, "clones")
STEP1_CSV    = os.path.join(OUTPUT_DIR, "step1_results.csv")
RESULTS_CSV  = os.path.join(OUTPUT_DIR, "step2_results.csv")

PS8_CSV = os.path.join(_ROOT, "PS", "js", "ps8",
                       "ps8_filtered_more_than_70%_linecoverage.csv")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
NPM_INSTALL_TIMEOUT = 180   # npm install タイムアウト (秒)
TEST_TIMEOUT        = 300   # npm test タイムアウト (秒)
UNINSTALL_TIMEOUT   = 120   # npm uninstall タイムアウト (秒)

MODELS = ["depcheck", "knip", "llama", "qwen", "deepseek"]

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def parse_list_col(val) -> List[str]:
    """CSV から読み込んだリスト列 (文字列 or float NaN) を List[str] に変換"""
    if not isinstance(val, str):
        return []
    val = val.strip()
    if not val or val in ("[]", "nan"):
        return []
    try:
        result = ast.literal_eval(val)
        return result if isinstance(result, list) else []
    except Exception:
        return []

def run_test(repo_path: str, test_cmd: Optional[str] = None) -> Tuple[str, float, str]:
    """
    テストを実行して結果を返す

    Returns:
        (result, duration_sec, output_tail)
        result: "PASS" | "FAIL" | "ERROR"
    """
    cmd = test_cmd if test_cmd else "npm test"
    t0 = time.time()
    try:
        proc = subprocess.run(
            cmd,
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
            shell=True,
        )
        duration = time.time() - t0
        # stdout + stderr の末尾 500 文字を保存
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        tail = combined[-500:] if len(combined) > 500 else combined
        result = "PASS" if proc.returncode == 0 else "FAIL"
        return result, duration, tail
    except subprocess.TimeoutExpired:
        return "ERROR", time.time() - t0, "timeout"
    except Exception as e:
        return "ERROR", time.time() - t0, str(e)

def npm_install(repo_path: str) -> bool:
    """npm install を実行して成否を返す"""
    try:
        proc = subprocess.run(
            ["npm", "install", "--prefer-offline"],
            cwd=repo_path,
            capture_output=True,
            timeout=NPM_INSTALL_TIMEOUT,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"  [npm install] error: {e}")
        return False

def npm_uninstall(repo_path: str, packages: List[str]) -> bool:
    """指定パッケージを npm uninstall で削除（package.json と node_modules を更新）"""
    if not packages:
        return True
    try:
        proc = subprocess.run(
            ["npm", "uninstall"] + packages,
            cwd=repo_path,
            capture_output=True,
            timeout=UNINSTALL_TIMEOUT,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"  [npm uninstall] error: {e}")
        return False

def git_restore_package_json(repo_path: str) -> bool:
    """package.json を git で元の状態に戻す"""
    try:
        subprocess.run(
            ["git", "checkout", "--", "package.json"],
            cwd=repo_path, capture_output=True, check=True,
        )
        # package-lock.json も可能なら戻す（失敗しても続行）
        subprocess.run(
            ["git", "checkout", "--", "package-lock.json"],
            cwd=repo_path, capture_output=True,
        )
        return True
    except Exception as e:
        print(f"  [git restore] error: {e}")
        return False

# ---------------------------------------------------------------------------
# リポジトリ単位の検証
# ---------------------------------------------------------------------------

def verify_repo(
    owner: str,
    repo: str,
    step1_row: Dict,
    test_cmd: Optional[str],
) -> List[Dict[str, Any]]:
    """
    1リポジトリに対して全モデルの検証を行い、結果行のリストを返す。

    Returns:
        List of rows, one per model
    """
    repo_path = os.path.join(CLONES_DIR, f"{owner}-{repo}")
    full_name = f"{owner}/{repo}"
    print(f"\n{'='*60}")
    print(f"[{full_name}]  test_cmd: {test_cmd or 'npm test'}")

    # ---- clone ----
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    try:
        print("  Cloning ...")
        Repo.clone_from(f"https://github.com/{owner}/{repo}", repo_path)
    except Exception as e:
        print(f"  [error] clone failed: {e}")
        return [_error_row(full_name, m, str(e)) for m in MODELS]

    # ---- npm install (ベースライン用) ----
    print("  npm install (baseline) ...")
    if not npm_install(repo_path):
        print("  [warn] npm install failed, proceeding anyway")

    # ---- baseline テスト ----
    print("  Running baseline test ...")
    baseline_result, baseline_dur, baseline_out = run_test(repo_path, test_cmd)
    print(f"  baseline: {baseline_result}  ({baseline_dur:.1f}s)")

    rows = []

    for model in MODELS:
        unused_dep     = parse_list_col(step1_row.get(f"{model}_unused_dep"))
        unused_dev_dep = parse_list_col(step1_row.get(f"{model}_unused_dev_dep"))
        to_remove = list(dict.fromkeys(unused_dep + unused_dev_dep))  # 重複除去

        print(f"\n  --- model: {model} ---")
        print(f"  to_remove: {to_remove}")

        row: Dict[str, Any] = {
            "repo":                    full_name,
            "model":                   model,
            "removed_deps":            unused_dep,
            "removed_dev_deps":        unused_dev_dep,
            "baseline_result":         baseline_result,
            "baseline_duration_sec":   round(baseline_dur, 2),
            "post_removal_result":     None,
            "post_removal_duration_sec": None,
            "post_removal_output":     None,
            "error":                   None,
        }

        # ベースラインが失敗している場合は評価不能
        if baseline_result in ("FAIL", "ERROR"):
            row["post_removal_result"] = "SKIP"
            row["error"] = f"baseline={baseline_result}"
            rows.append(row)
            print(f"  post_removal: SKIP (baseline {baseline_result})")
            continue

        # 削除対象がない場合はスキップ
        if not to_remove:
            row["post_removal_result"] = "SKIP"
            row["error"] = "no deps to remove"
            rows.append(row)
            print("  post_removal: SKIP (no deps to remove)")
            continue

        # ---- package.json をリセット ----
        if not git_restore_package_json(repo_path):
            row["post_removal_result"] = "ERROR"
            row["error"] = "git restore failed"
            rows.append(row)
            continue

        # ---- npm install (リセット後に node_modules を復元) ----
        print("  npm install (restore) ...")
        npm_install(repo_path)

        # ---- npm uninstall (対象パッケージを削除) ----
        print(f"  npm uninstall {to_remove} ...")
        if not npm_uninstall(repo_path, to_remove):
            print("  [warn] npm uninstall failed, proceeding with test anyway")

        # ---- テスト実行 ----
        print("  Running test after removal ...")
        post_result, post_dur, post_out = run_test(repo_path, test_cmd)
        print(f"  post_removal: {post_result}  ({post_dur:.1f}s)")

        row["post_removal_result"]       = post_result
        row["post_removal_duration_sec"] = round(post_dur, 2)
        row["post_removal_output"]       = post_out
        rows.append(row)

    # ---- clone 削除 ----
    try:
        shutil.rmtree(repo_path)
    except Exception:
        pass

    return rows

def _error_row(full_name: str, model: str, error: str) -> Dict[str, Any]:
    return {
        "repo": full_name, "model": model,
        "removed_deps": [], "removed_dev_deps": [],
        "baseline_result": "ERROR", "baseline_duration_sec": None,
        "post_removal_result": "ERROR", "post_removal_duration_sec": None,
        "post_removal_output": None, "error": error,
    }

# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--step1-results", default=STEP1_CSV)
    parser.add_argument("--repo-list",     default=PS8_CSV)
    parser.add_argument("--limit",  type=int, default=None)
    parser.add_argument("--skip",   type=int, default=0)
    parser.add_argument("--output", default=RESULTS_CSV)
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CLONES_DIR, exist_ok=True)

    # ---- step1 結果を読み込む ----
    if not os.path.exists(args.step1_results):
        print(f"[error] step1 results not found: {args.step1_results}")
        sys.exit(1)
    step1_df = pd.read_csv(args.step1_results)
    step1_map: Dict[str, Dict] = {
        row["repo"]: row for row in step1_df.to_dict("records")
    }
    print(f"step1 results loaded: {len(step1_map)} repos")

    # 処理対象 = step1 で解析済みのリポジトリ
    repos = list(step1_map.keys())
    if args.skip:
        repos = repos[args.skip:]
    if args.limit:
        repos = repos[:args.limit]

    # ---- 前回結果を削除して1から実行 ----
    done_repos: set = set()
    all_rows: List[Dict] = []
    if os.path.exists(args.output):
        os.remove(args.output)
        print(f"Removed previous results: {args.output}")

    print(f"Target: {len(repos)} repos  |  Output: {args.output}")

    for full_name in repos:

        parts = full_name.split("/")
        if len(parts) != 2:
            print(f"  [warn] unexpected format: {full_name}")
            continue
        owner, repo = parts

        step1_row = step1_map.get(full_name, {})
        test_cmd  = "npm test"

        new_rows = verify_repo(owner, repo, step1_row, test_cmd)
        all_rows.extend(new_rows)
        done_repos.add(full_name)

        pd.DataFrame(all_rows).to_csv(args.output, index=False)
        print(f"  Saved ({len(done_repos)} repos done)")

    print(f"\nFinished. Results: {args.output}")

    # ---- サマリー表示 ----
    result_df = pd.DataFrame(all_rows)
    if not result_df.empty:
        print("\n=== Summary ===")
        summary = result_df.groupby(["model", "post_removal_result"]).size().unstack(fill_value=0)
        print(summary.to_string())

if __name__ == "__main__":
    main()

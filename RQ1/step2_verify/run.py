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

def parse_package_json(repo_path: str) -> Dict:
    try:
        with open(os.path.join(repo_path, "package.json"), "r", encoding="utf-8") as f:
            content = f.read().replace("//", "")
        return json.loads(content)
    except Exception as e:
        print(f"  [warn] parse_package_json: {e}")
        return {}


def detect_nyc_cmd(pkg: Dict) -> str:
    scripts = pkg.get("scripts") or {}
    for key in ["coverage", "test:coverage", "test-coverage", "cov"]:
        if "nyc" in scripts.get(key, ""):
            return f"npm run {key}"
    if "nyc" in scripts.get("test", ""):
        return "npm test"
    deps: Dict = {}
    deps.update(pkg.get("dependencies") or {})
    deps.update(pkg.get("devDependencies") or {})
    if "nyc" in deps:
        return "npx nyc --reporter=json-summary npm test"
    return "npm test"


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


def iterative_removal(
    repo_path: str,
    candidates: List[str],
    test_cmd: Optional[str],
) -> Dict[str, Any]:
    """
    反復削除でパッケージ単位の安全性を検証する。

    手順:
      1. 候補を一括 uninstall してテスト (bulk)
      2. PASS → 全候補が安全
      3. FAIL → git restore して1件ずつ個別テスト
      4. safe が1件以上あれば、最後にまとめて削除して最終テスト

    Returns:
      bulk_result    : 一括削除時のテスト結果
      safe_deps      : 削除しても壊れなかったパッケージ
      must_keep_deps : 削除するとテストが失敗したパッケージ
      final_result   : safe_deps を全部削除した最終テスト結果
      n_iterations   : テスト実行回数の合計
    """
    n_iter = 0

    # ---- Step 1: 一括削除 ----
    npm_uninstall(repo_path, candidates)
    bulk_result, _, _ = run_test(repo_path, test_cmd)
    n_iter += 1
    print(f"  [iter] bulk ({len(candidates)} deps): {bulk_result}")

    if bulk_result == "PASS":
        return {
            "bulk_result":    bulk_result,
            "safe_deps":      candidates,
            "must_keep_deps": [],
            "final_result":   "PASS",
            "n_iterations":   n_iter,
        }

    # ---- Step 2: 1件ずつ個別テスト ----
    safe: List[str] = []
    must_keep: List[str] = []

    for pkg in candidates:
        # クリーンな状態に戻す
        git_restore_package_json(repo_path)
        npm_install(repo_path)

        npm_uninstall(repo_path, [pkg])
        result, _, _ = run_test(repo_path, test_cmd)
        n_iter += 1
        print(f"  [iter]   {pkg}: {result}")

        if result == "PASS":
            safe.append(pkg)
        else:
            must_keep.append(pkg)

    # ---- Step 3: safe を全部まとめて最終テスト ----
    if safe:
        git_restore_package_json(repo_path)
        npm_install(repo_path)
        npm_uninstall(repo_path, safe)
        final_result, _, _ = run_test(repo_path, test_cmd)
        n_iter += 1
        print(f"  [iter] final ({len(safe)} safe deps): {final_result}")
    else:
        final_result = "FAIL"

    return {
        "bulk_result":    bulk_result,
        "safe_deps":      safe,
        "must_keep_deps": must_keep,
        "final_result":   final_result,
        "n_iterations":   n_iter,
    }

# ---------------------------------------------------------------------------
# リポジトリ単位の検証
# ---------------------------------------------------------------------------

def verify_repo(
    owner: str,
    repo: str,
    step1_row: Dict,
) -> List[Dict[str, Any]]:
    """
    1リポジトリに対して全モデルの検証を行い、結果行のリストを返す。

    Returns:
        List of rows, one per model
    """
    repo_path = os.path.join(CLONES_DIR, f"{owner}-{repo}")
    full_name = f"{owner}/{repo}"
    print(f"\n{'='*60}")
    print(f"[{full_name}]")

    # ---- clone ----
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    try:
        print("  Cloning ...")
        Repo.clone_from(f"https://github.com/{owner}/{repo}", repo_path)
    except Exception as e:
        print(f"  [error] clone failed: {e}")
        return [_error_row(full_name, m, str(e)) for m in MODELS]

    # ---- PS8 と同じ方法でテストコマンドを検出 ----
    pkg = parse_package_json(repo_path)
    test_cmd = detect_nyc_cmd(pkg)
    print(f"  test_cmd: {test_cmd}")

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
        # devDependencies は CLIツール等が scripts で使われている可能性を検知しきれないため除外
        to_remove = list(dict.fromkeys(unused_dep))  # runtime dependencies のみ・重複除去

        print(f"\n  --- model: {model} ---")
        print(f"  to_remove (runtime deps only): {to_remove}")

        row: Dict[str, Any] = {
            "repo":                full_name,
            "model":               model,
            "candidates":          to_remove,
            "removed_deps":        [],
            "must_keep_deps":      [],
            "baseline_result":     baseline_result,
            "baseline_duration_sec": round(baseline_dur, 2),
            "bulk_result":         None,
            "post_removal_result": None,
            "n_iterations":        0,
            "error":               None,
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

        # ---- package.json をリセットして node_modules を復元 ----
        if not git_restore_package_json(repo_path):
            row["post_removal_result"] = "ERROR"
            row["error"] = "git restore failed"
            rows.append(row)
            continue

        print("  npm install (restore) ...")
        npm_install(repo_path)

        # ---- 反復削除 ----
        ir = iterative_removal(repo_path, to_remove, test_cmd)

        row["bulk_result"]         = ir["bulk_result"]
        row["removed_deps"]        = ir["safe_deps"]
        row["must_keep_deps"]      = ir["must_keep_deps"]
        row["post_removal_result"] = ir["final_result"]
        row["n_iterations"]        = ir["n_iterations"]
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

        new_rows = verify_repo(owner, repo, step1_row)
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

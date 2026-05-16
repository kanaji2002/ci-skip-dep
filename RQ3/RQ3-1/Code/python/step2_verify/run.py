"""
Step 2: 未使用依存を削除してテスト実行 (Python)
step1_results.csv を読み込み、各LLMが「未使用」と判定した依存を
実際に削除してpytestが通るかを検証する。
出力: output/step2_results.csv

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
import os
import shutil
import subprocess
import sys
import time
import venv
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
load_dotenv(os.path.join(_ROOT, ".env"))

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
LANG_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR   = os.path.join(LANG_DIR, "output")
CLONES_DIR   = os.path.join(OUTPUT_DIR, "clones")
STEP1_CSV    = os.path.join(OUTPUT_DIR, "step1_results.csv")
RESULTS_CSV  = os.path.join(OUTPUT_DIR, "step2_results.csv")

# ---------------------------------------------------------------------------
# 設定
# ---------------------------------------------------------------------------
INSTALL_TIMEOUT  = 300   # pip install タイムアウト (秒)
TEST_TIMEOUT     = 300   # pytest タイムアウト (秒)
UNINSTALL_TIMEOUT = 120  # pip uninstall タイムアウト (秒)

MODELS = ["llama", "qwen", "deepseek"]

# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def parse_list_col(val) -> List[str]:
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


def get_venv_python(venv_dir: str) -> str:
    for candidate in [
        os.path.join(venv_dir, "bin", "python3"),
        os.path.join(venv_dir, "bin", "python"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return "python3"


def get_venv_pip(venv_dir: str) -> str:
    for candidate in [
        os.path.join(venv_dir, "bin", "pip3"),
        os.path.join(venv_dir, "bin", "pip"),
    ]:
        if os.path.exists(candidate):
            return candidate
    return "pip3"


def create_venv(venv_dir: str) -> bool:
    try:
        if os.path.exists(venv_dir):
            shutil.rmtree(venv_dir)
        venv.create(venv_dir, with_pip=True, clear=True)
        return True
    except Exception as e:
        print(f"  [venv] create error: {e}")
        return False


def pip_install_project(repo_path: str, venv_dir: str) -> bool:
    pip = get_venv_pip(venv_dir)
    try:
        # まず pip 自体をアップグレード
        subprocess.run(
            [pip, "install", "--upgrade", "pip"],
            cwd=repo_path, capture_output=True, timeout=60,
        )
        # プロジェクトをインストール (pyproject.toml / setup.py 対応)
        proc = subprocess.run(
            [pip, "install", "-e", ".[dev,test]"],
            cwd=repo_path, capture_output=True, timeout=INSTALL_TIMEOUT,
        )
        if proc.returncode == 0:
            return True
        # フォールバック: extras なしでインストール
        proc = subprocess.run(
            [pip, "install", "-e", "."],
            cwd=repo_path, capture_output=True, timeout=INSTALL_TIMEOUT,
        )
        if proc.returncode == 0:
            return True
        # さらにフォールバック: requirements*.txt
        for req_name in [
            "requirements.txt",
            "requirements-dev.txt",
            "requirements-test.txt",
            "requirements-devel.txt",
            "test-requirements.txt",
        ]:
            req_file = os.path.join(repo_path, req_name)
            if os.path.exists(req_file):
                proc = subprocess.run(
                    [pip, "install", "-r", req_file],
                    cwd=repo_path, capture_output=True, timeout=INSTALL_TIMEOUT,
                )
                if proc.returncode == 0:
                    break
        return False
    except subprocess.TimeoutExpired:
        print("  [pip install] timeout")
        return False
    except Exception as e:
        print(f"  [pip install] error: {e}")
        return False


def pip_install_package(repo_path: str, venv_dir: str, packages: List[str]) -> bool:
    pip = get_venv_pip(venv_dir)
    if not packages:
        return True
    try:
        proc = subprocess.run(
            [pip, "install"] + packages,
            cwd=repo_path, capture_output=True, timeout=INSTALL_TIMEOUT,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"  [pip install pkg] error: {e}")
        return False


def pip_uninstall(repo_path: str, venv_dir: str, packages: List[str]) -> bool:
    pip = get_venv_pip(venv_dir)
    if not packages:
        return True
    try:
        proc = subprocess.run(
            [pip, "uninstall", "-y"] + packages,
            cwd=repo_path, capture_output=True, timeout=UNINSTALL_TIMEOUT,
        )
        return proc.returncode == 0
    except Exception as e:
        print(f"  [pip uninstall] error: {e}")
        return False


def run_pytest(repo_path: str, venv_dir: str) -> Tuple[str, float, str]:
    python = get_venv_python(venv_dir)
    t0 = time.time()
    try:
        proc = subprocess.run(
            [python, "-m", "pytest", "--tb=no", "-q", "--timeout=120"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TEST_TIMEOUT,
        )
        duration = time.time() - t0
        combined = (proc.stdout + "\n" + proc.stderr).strip()
        tail = combined[-500:] if len(combined) > 500 else combined
        result = "PASS" if proc.returncode == 0 else "FAIL"
        return result, duration, tail
    except subprocess.TimeoutExpired:
        return "ERROR", time.time() - t0, "timeout"
    except Exception as e:
        return "ERROR", time.time() - t0, str(e)


# ---------------------------------------------------------------------------
# 反復削除
# ---------------------------------------------------------------------------

def iterative_removal(
    repo_path: str,
    venv_dir: str,
    candidates: List[str],
) -> Dict[str, Any]:
    """
    反復削除でパッケージ単位の安全性を検証する。

    手順:
      1. 候補を一括 uninstall してテスト (bulk)
      2. PASS → 全候補が安全
      3. FAIL → 個別に1件ずつテスト
      4. safe が1件以上あれば最終テスト
    """
    n_iter = 0

    # Step 1: 一括削除
    pip_uninstall(repo_path, venv_dir, candidates)
    bulk_result, _, _ = run_pytest(repo_path, venv_dir)
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

    # Step 2: 1件ずつ個別テスト
    safe: List[str] = []
    must_keep: List[str] = []

    for pkg in candidates:
        # 一旦全部再インストールして元に戻す
        pip_install_package(repo_path, venv_dir, candidates)
        pip_uninstall(repo_path, venv_dir, [pkg])
        result, _, _ = run_pytest(repo_path, venv_dir)
        n_iter += 1
        print(f"  [iter]   {pkg}: {result}")
        if result == "PASS":
            safe.append(pkg)
        else:
            must_keep.append(pkg)

    # Step 3: safe を全部削除して最終テスト
    if safe:
        pip_install_package(repo_path, venv_dir, candidates)
        pip_uninstall(repo_path, venv_dir, safe)
        final_result, _, _ = run_pytest(repo_path, venv_dir)
        n_iter += 1
        print(f"  [iter] final ({len(safe)} safe deps): {final_result}")
        pip_install_package(repo_path, venv_dir, safe)  # 次モデルのために venv を元に戻す
    else:
        # 全部再インストールして環境を戻す (何も削除しないのでベースラインと同じ PASS)
        pip_install_package(repo_path, venv_dir, candidates)
        final_result = "PASS"

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

def verify_repo(owner: str, repo: str, step1_row: Dict) -> List[Dict[str, Any]]:
    repo_path = os.path.join(CLONES_DIR, f"{owner}-{repo}")
    venv_dir  = os.path.join(repo_path, ".venv_rq3")
    full_name = f"{owner}/{repo}"
    print(f"\n{'='*60}")
    print(f"[{full_name}]")

    # clone
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path)
    print("  Cloning ...")
    try:
        r = subprocess.run(
            ["git", "clone", "--depth=1",
             f"https://github.com/{owner}/{repo}.git", repo_path],
            capture_output=True, text=True, timeout=120,
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or "clone failed")
    except Exception as e:
        print(f"  [error] clone failed: {e}")
        return [_error_row(full_name, m, str(e)) for m in MODELS]

    # venv 作成
    print("  Creating venv ...")
    if not create_venv(venv_dir):
        err = "venv creation failed"
        shutil.rmtree(repo_path, ignore_errors=True)
        return [_error_row(full_name, m, err) for m in MODELS]

    # pip install
    print("  pip install ...")
    if not pip_install_project(repo_path, venv_dir):
        print("  [warn] pip install failed, proceeding anyway")
    # pytest を確実にインストール (extras 名の違いで漏れるケースを補完)
    pip_install_package(repo_path, venv_dir, ["pytest", "pytest-timeout"])

    # ベースライン pytest
    print("  Running baseline pytest ...")
    baseline_result, baseline_dur, baseline_out = run_pytest(repo_path, venv_dir)
    print(f"  baseline: {baseline_result}  ({baseline_dur:.1f}s)")

    rows = []

    for model in MODELS:
        unused_dep     = parse_list_col(step1_row.get(f"{model}_unused_dep"))
        unused_dev_dep = parse_list_col(step1_row.get(f"{model}_unused_dev_dep"))
        # runtime deps のみを対象 (dev deps は除外)
        to_remove = list(dict.fromkeys(unused_dep))

        print(f"\n  --- model: {model} ---")
        print(f"  to_remove (runtime deps only): {to_remove}")

        row: Dict[str, Any] = {
            "repo":                  full_name,
            "model":                 model,
            "candidates":            to_remove,
            "removed_deps":          [],
            "must_keep_deps":        [],
            "baseline_result":       baseline_result,
            "baseline_duration_sec": round(baseline_dur, 2),
            "bulk_result":           None,
            "post_removal_result":   None,
            "n_iterations":          0,
            "error":                 None,
        }

        if baseline_result in ("FAIL", "ERROR"):
            row["post_removal_result"] = "SKIP"
            row["error"] = f"baseline={baseline_result}"
            rows.append(row)
            print(f"  post_removal: SKIP (baseline {baseline_result})")
            continue

        if not to_remove:
            row["post_removal_result"] = "SKIP"
            row["error"] = "no deps to remove"
            rows.append(row)
            print("  post_removal: SKIP (no deps to remove)")
            continue

        # 環境をベースライン状態に戻してから反復削除
        pip_install_package(repo_path, venv_dir, to_remove)

        ir = iterative_removal(repo_path, venv_dir, to_remove)

        row["bulk_result"]         = ir["bulk_result"]
        row["removed_deps"]        = ir["safe_deps"]
        row["must_keep_deps"]      = ir["must_keep_deps"]
        row["post_removal_result"] = ir["final_result"]
        row["n_iterations"]        = ir["n_iterations"]
        rows.append(row)

    try:
        shutil.rmtree(repo_path)
    except Exception:
        pass

    return rows


def _error_row(full_name: str, model: str, error: str) -> Dict[str, Any]:
    return {
        "repo": full_name, "model": model,
        "candidates": [], "removed_deps": [], "must_keep_deps": [],
        "baseline_result": "ERROR", "baseline_duration_sec": None,
        "bulk_result": None, "post_removal_result": "ERROR",
        "n_iterations": 0, "error": error,
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

    if not os.path.exists(args.step1_results):
        print(f"[error] step1 results not found: {args.step1_results}")
        sys.exit(1)

    step1_df = pd.read_csv(args.step1_results)
    step1_map: Dict[str, Dict] = {
        row["repo"]: row for row in step1_df.to_dict("records")
    }
    print(f"step1 results loaded: {len(step1_map)} repos")

    repos = list(step1_map.keys())
    if args.skip:
        repos = repos[args.skip:]
    if args.limit:
        repos = repos[:args.limit]

    done_repos: set = set()
    all_rows: List[Dict] = []

    # 前回結果があれば再開
    if os.path.exists(args.output) and args.skip == 0:
        prev_df = pd.read_csv(args.output)
        done_repos = set(prev_df["repo"].unique().tolist())
        all_rows = prev_df.to_dict("records")
        repos = [r for r in repos if r not in done_repos]
        print(f"Resuming: {len(done_repos)} repos already done, {len(repos)} remaining")

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

    result_df = pd.DataFrame(all_rows)
    if not result_df.empty:
        print("\n=== Summary ===")
        summary = result_df.groupby(["model", "post_removal_result"]).size().unstack(fill_value=0)
        print(summary.to_string())


if __name__ == "__main__":
    main()

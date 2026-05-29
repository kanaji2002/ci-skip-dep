"""
Step 2: 未使用依存を削除してテスト実行 (Rust)
step1_results.csv を読み込み、各LLMが「未使用」と判定した依存を
Cargo.toml から削除して cargo test が通るかを検証する。
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
from pathlib import Path
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
# Singularity 設定
# ---------------------------------------------------------------------------
SIF_PATH    = "/work/rintaro-k/research/containers/rust-tarpaulin.sif"
SINGULARITY = "/opt/singularity/3.9.6/bin/singularity"
CARGO_HOME  = "/work/rintaro-k/.cargo"  # PS8 と同じ共有キャッシュ

TEST_TIMEOUT    = 600
REMOVE_TIMEOUT  = 120

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


def singularity_exec(cmd: list, cwd: str, timeout: int) -> subprocess.CompletedProcess:
    full_cmd = [
        SINGULARITY, "exec",
        "--bind", "/work/rintaro-k:/work/rintaro-k",
        "--env", f"CARGO_HOME={CARGO_HOME}",
        SIF_PATH,
    ] + cmd
    return subprocess.run(full_cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout)


def find_cargo_tomls(repo_path: str) -> List[str]:
    p = os.path.join(repo_path, "Cargo.toml")
    return [p] if os.path.exists(p) else []


def find_cargo_locks(repo_path: str) -> List[str]:
    p = os.path.join(repo_path, "Cargo.lock")
    return [p] if os.path.exists(p) else []


def backup_files(paths: List[str]) -> Dict[str, str]:
    backups = {}
    for p in paths:
        try:
            backups[p] = Path(p).read_text(encoding="utf-8", errors="replace")
        except Exception:
            pass
    return backups


def restore_files(backups: Dict[str, str]):
    for path, content in backups.items():
        try:
            Path(path).write_text(content, encoding="utf-8")
        except Exception as e:
            print(f"  [restore] error ({path}): {e}")


def cargo_remove(repo_path: str, dep: str) -> bool:
    try:
        r = singularity_exec(["cargo", "remove", dep], cwd=repo_path, timeout=REMOVE_TIMEOUT)
        return r.returncode == 0
    except Exception:
        return False


def run_cargo_test(repo_path: str) -> Tuple[str, float, str]:
    cov_dir = os.path.join(repo_path, "coverage")
    os.makedirs(cov_dir, exist_ok=True)
    t0 = time.time()
    try:
        r = singularity_exec(
            ["cargo", "tarpaulin",
             "--out", "Json",
             "--output-dir", cov_dir,
             "--timeout", "300",
             "--skip-clean"],
            cwd=repo_path,
            timeout=TEST_TIMEOUT,
        )
        duration = time.time() - t0
        combined = (r.stdout + "\n" + r.stderr).strip()
        tail = combined[-500:] if len(combined) > 500 else combined
        result = "PASS" if r.returncode == 0 else "FAIL"
        if result == "FAIL":
            print(f"  [cargo tarpaulin tail]\n{tail}")
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
    cargo_tomls: List[str],
    candidates: List[str],
) -> Dict[str, Any]:
    """
    反復削除でクレート単位の安全性を検証する。

    手順:
      1. 候補を一括削除してテスト (bulk)
      2. PASS → 全候補が安全
      3. FAIL → 個別に1件ずつテスト
      4. safe が1件以上あれば最終テスト
    """
    cargo_locks = find_cargo_locks(repo_path)
    n_iter = 0

    # Step 1: 一括削除
    backups = backup_files(cargo_tomls + cargo_locks)
    for dep in candidates:
        cargo_remove(repo_path, dep)
    bulk_result, _, _ = run_cargo_test(repo_path)
    n_iter += 1
    print(f"  [iter] bulk ({len(candidates)} deps): {bulk_result}")

    if bulk_result == "PASS":
        restore_files(backups)
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

    for dep in candidates:
        restore_files(backups)
        cargo_remove(repo_path, dep)
        result, _, _ = run_cargo_test(repo_path)
        n_iter += 1
        print(f"  [iter]   {dep}: {result}")
        if result == "PASS":
            safe.append(dep)
        else:
            must_keep.append(dep)

    # Step 3: safe を全部削除して最終テスト
    restore_files(backups)
    if safe:
        for dep in safe:
            cargo_remove(repo_path, dep)
        final_result, _, _ = run_cargo_test(repo_path)
        n_iter += 1
        print(f"  [iter] final ({len(safe)} safe deps): {final_result}")
        restore_files(backups)
    else:
        # 何も削除しないのでベースラインと同じ PASS
        final_result = "PASS"

    restore_files(backups)
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
        return [_error_row(full_name, m, str(e), step1_row) for m in MODELS]

    cargo_tomls = find_cargo_tomls(repo_path)
    if not cargo_tomls:
        err = "no Cargo.toml found"
        shutil.rmtree(repo_path, ignore_errors=True)
        return [_error_row(full_name, m, err, step1_row) for m in MODELS]

    # ベースライン cargo test
    print("  Running baseline cargo test ...")
    baseline_result, baseline_dur, baseline_out = run_cargo_test(repo_path)
    print(f"  baseline: {baseline_result}  ({baseline_dur:.1f}s)")

    rows = []

    for model in MODELS:
        unused_dep = parse_list_col(step1_row.get(f"{model}_unused_dep"))
        to_remove  = list(dict.fromkeys(unused_dep))  # runtime deps のみ

        print(f"\n  --- model: {model} ---")
        print(f"  to_remove (runtime deps only): {to_remove}")

        row: Dict[str, Any] = {
            "repo":                  full_name,
            "model":                 model,
            "all_dep":               parse_list_col(step1_row.get("all_dep")),
            "all_dev_dep":           parse_list_col(step1_row.get("all_dev_dep")),
            "missing_dep":           parse_list_col(step1_row.get(f"{model}_missing_dep")),
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

        ir = iterative_removal(repo_path, cargo_tomls, to_remove)

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


def _error_row(full_name: str, model: str, error: str, step1_row: Dict = None) -> Dict[str, Any]:
    step1_row = step1_row or {}
    return {
        "repo": full_name, "model": model,
        "all_dep":     parse_list_col(step1_row.get("all_dep")),
        "all_dev_dep": parse_list_col(step1_row.get("all_dev_dep")),
        "missing_dep": parse_list_col(step1_row.get(f"{model}_missing_dep")),
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
    parser.add_argument("--rerun-baseline-fails", action="store_true",
                        help="baseline=FAIL/ERROR のリポジトリを再実行する")
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CLONES_DIR, exist_ok=True)
    os.makedirs(CARGO_HOME, exist_ok=True)

    if not os.path.exists(SIF_PATH):
        print(f"[error] コンテナが見つかりません: {SIF_PATH}")
        sys.exit(1)

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
        try:
            prev_df = pd.read_csv(args.output)
            if args.rerun_baseline_fails:
                # baseline=FAIL/ERROR のリポジトリは再実行対象とする
                pass_mask = prev_df["baseline_result"] == "PASS"
                done_repos = set(prev_df.loc[pass_mask, "repo"].unique().tolist())
                all_rows = prev_df.loc[pass_mask].to_dict("records")
                rerun_count = prev_df[~pass_mask]["repo"].nunique()
                print(f"Resuming (--rerun-baseline-fails): {len(done_repos)} PASS repos kept, {rerun_count} FAIL/ERROR repos will be re-run, {len(repos) - len(done_repos)} remaining")
            else:
                done_repos = set(prev_df["repo"].unique().tolist())
                all_rows = prev_df.to_dict("records")
                print(f"Resuming: {len(done_repos)} repos already done, {len(repos) - len(done_repos)} remaining")
            repos = [r for r in repos if r not in done_repos]
        except pd.errors.EmptyDataError:
            print("Output file is empty, starting fresh")

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

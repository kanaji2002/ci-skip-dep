"""
Step 2: 未使用依存を削除してテスト実行 (C#)
step1_results.csv を読み込み、各LLMが「未使用」と判定した依存を
実際に .csproj から削除して dotnet test が通るかを検証する。
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
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from dotenv import load_dotenv

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
load_dotenv(os.path.join(_ROOT, ".env"))

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
LANG_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR  = os.path.join(LANG_DIR, "output")
CLONES_DIR  = os.path.join(OUTPUT_DIR, "clones")
STEP1_CSV   = os.path.join(OUTPUT_DIR, "step1_results.csv")
RESULTS_CSV = os.path.join(OUTPUT_DIR, "step2_results.csv")

# ---------------------------------------------------------------------------
# dotnet (Singularity) 設定
# ---------------------------------------------------------------------------
SIF_PATH    = "/work/rintaro-k/research/containers/dotnet-sdk8.sif"
SINGULARITY = "/opt/singularity/3.9.6/bin/singularity"

RESTORE_TIMEOUT = 300
TEST_TIMEOUT    = 600

MODELS = ["llama", "qwen", "deepseek"]

TEST_PKG_RE = re.compile(
    r'xunit|nunit|mstest\.testframework|microsoft\.net\.test\.sdk',
    re.IGNORECASE,
)

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
        "--pwd", cwd,
        "--env", "MSBUILDDISABLENODEREUSE=1",
        SIF_PATH,
    ] + cmd
    return subprocess.run(full_cmd, capture_output=True, text=True, timeout=timeout)


def find_solution_file(repo_path: str) -> Optional[str]:
    slns = list(Path(repo_path).rglob("*.sln"))
    if not slns:
        return None
    return str(min(slns, key=lambda p: len(p.relative_to(repo_path).parts)))


def find_test_csproj_files(repo_path: str) -> List[str]:
    """xUnit/NUnit/MSTest を参照しているテスト .csproj を返す"""
    EXCLUDE = {'.git', 'bin', 'obj', 'node_modules', 'packages'}
    result = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE]
        for f in filenames:
            if not f.endswith('.csproj'):
                continue
            fpath = os.path.join(dirpath, f)
            try:
                with open(fpath, encoding='utf-8', errors='replace') as fh:
                    if TEST_PKG_RE.search(fh.read()):
                        result.append(fpath)
            except Exception:
                pass
    return result


def ensure_coverlet(csproj_path: str, repo_path: str):
    """coverlet.collector が未参照のテスト .csproj に動的に追加する (PS8 と同じ)"""
    try:
        with open(csproj_path, encoding='utf-8', errors='replace') as f:
            content = f.read()
    except Exception:
        return
    if 'coverlet.collector' in content.lower():
        return
    singularity_exec(
        ["dotnet", "add", csproj_path, "package", "coverlet.collector", "--no-restore"],
        cwd=repo_path, timeout=60,
    )


_EXCLUDE_NAME_PATTERNS = ('test',)


def _is_non_production_csproj(repo_path: str, csproj_path: str) -> bool:
    """step1 と同じ判定: パス名に 'test' を含む csproj はテストプロジェクトとして除外"""
    rel = os.path.relpath(csproj_path, repo_path)
    return any(
        any(pat in part.lower() for pat in _EXCLUDE_NAME_PATTERNS)
        for part in rel.split(os.sep)
    )


def find_csproj_files(repo_path: str) -> List[str]:
    """プロダクション csproj のみを返す (テストプロジェクトは除外)"""
    EXCLUDE = {'.git', 'bin', 'obj', 'node_modules', 'packages'}
    result = []
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE]
        for f in filenames:
            fpath = os.path.join(dirpath, f)
            if f.endswith('.csproj') and not _is_non_production_csproj(repo_path, fpath):
                result.append(fpath)
    return result


def dotnet_restore(repo_path: str, sln: Optional[str]) -> bool:
    target = [sln] if sln else []
    try:
        r = singularity_exec(
            ["dotnet", "restore", "--nologo", "-v", "q",
             "--ignore-failed-sources", "-p:NuGetAudit=false",
             "-maxcpucount:1"] + target,
            cwd=repo_path, timeout=RESTORE_TIMEOUT,
        )
        return r.returncode == 0
    except subprocess.TimeoutExpired:
        return False


def run_dotnet_test(repo_path: str, sln: Optional[str]) -> Tuple[str, float, str]:
    """dotnet test を実行して (PASS/FAIL/ERROR, 秒, 出力末尾) を返す"""
    target = [sln] if sln else []
    results_dir = os.path.join(repo_path, "TestResults")
    t0 = time.time()
    try:
        r = singularity_exec(
            ["dotnet", "test"] + target + [
             "--no-restore", "--nologo", "-v", "q",
             "-maxcpucount:1",
             "--collect", "XPlat Code Coverage",
             "--results-directory", results_dir],
            cwd=repo_path, timeout=TEST_TIMEOUT,
        )
        duration = time.time() - t0
        combined = (r.stdout + "\n" + r.stderr).strip()
        tail = combined[-500:] if len(combined) > 500 else combined
        result = "PASS" if r.returncode == 0 else "FAIL"
        return result, duration, tail
    except subprocess.TimeoutExpired:
        return "ERROR", time.time() - t0, "timeout"
    except Exception as e:
        return "ERROR", time.time() - t0, str(e)


# ---------------------------------------------------------------------------
# .csproj からパッケージ参照を削除/復元
# ---------------------------------------------------------------------------

def remove_packages_from_csproj(csproj_path: str, packages: List[str]) -> Dict[str, str]:
    """
    指定パッケージを .csproj から削除し、削除前の内容を返す。
    戻り値: {csproj_path: original_content}
    """
    pkg_set = {p.lower() for p in packages}
    try:
        original = Path(csproj_path).read_text(encoding='utf-8', errors='replace')
        ET.register_namespace('', '')
        tree = ET.parse(csproj_path)
        root = tree.getroot()
        modified = False
        for parent in root.iter():
            to_remove = []
            for child in parent:
                tag = child.tag.split('}')[-1] if '}' in child.tag else child.tag
                if tag == 'PackageReference':
                    name = (child.get('Include') or child.get('include', '')).lower()
                    if name in pkg_set:
                        to_remove.append(child)
                        modified = True
            for child in to_remove:
                parent.remove(child)
        if modified:
            tree.write(csproj_path, encoding='unicode', xml_declaration=False)
        return {csproj_path: original}
    except Exception as e:
        print(f"  [csproj] remove error ({csproj_path}): {e}")
        return {}


def restore_csproj(backups: Dict[str, str]):
    """バックアップから .csproj を復元する"""
    for path, content in backups.items():
        try:
            Path(path).write_text(content, encoding='utf-8')
        except Exception as e:
            print(f"  [csproj] restore error ({path}): {e}")


# ---------------------------------------------------------------------------
# 反復削除
# ---------------------------------------------------------------------------

def iterative_removal(
    repo_path: str,
    sln: Optional[str],
    csproj_files: List[str],
    candidates: List[str],
) -> Dict[str, Any]:
    """
    反復削除でパッケージ単位の安全性を検証する。

    手順:
      1. 候補を一括削除してテスト (bulk)
      2. PASS → 全候補が安全
      3. FAIL → 個別に1件ずつテスト
      4. safe が1件以上あれば最終テスト
    """
    n_iter = 0

    # Step 1: 一括削除
    backups: Dict[str, str] = {}
    for csproj in csproj_files:
        backups.update(remove_packages_from_csproj(csproj, candidates))
    dotnet_restore(repo_path, sln)
    bulk_result, _, _ = run_dotnet_test(repo_path, sln)
    n_iter += 1
    print(f"  [iter] bulk ({len(candidates)} deps): {bulk_result}")

    if bulk_result == "PASS":
        restore_csproj(backups)
        return {
            "bulk_result":    bulk_result,
            "safe_deps":      candidates,
            "must_keep_deps": [],
            "final_result":   "PASS",
            "n_iterations":   n_iter,
        }

    # Step 2: 1件ずつ個別テスト
    restore_csproj(backups)
    safe: List[str] = []
    must_keep: List[str] = []

    for pkg in candidates:
        restore_csproj(backups)
        pkg_backups: Dict[str, str] = {}
        for csproj in csproj_files:
            pkg_backups.update(remove_packages_from_csproj(csproj, [pkg]))
        dotnet_restore(repo_path, sln)
        result, _, _ = run_dotnet_test(repo_path, sln)
        n_iter += 1
        print(f"  [iter]   {pkg}: {result}")
        if result == "PASS":
            safe.append(pkg)
        else:
            must_keep.append(pkg)

    # Step 3: safe を全部削除して最終テスト
    restore_csproj(backups)
    if safe:
        final_backups: Dict[str, str] = {}
        for csproj in csproj_files:
            final_backups.update(remove_packages_from_csproj(csproj, safe))
        dotnet_restore(repo_path, sln)
        final_result, _, _ = run_dotnet_test(repo_path, sln)
        n_iter += 1
        print(f"  [iter] final ({len(safe)} safe deps): {final_result}")
        restore_csproj(final_backups)
    else:
        # 何も削除しないのでベースラインと同じ PASS
        final_result = "PASS"

    restore_csproj(backups)
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

    sln          = find_solution_file(repo_path)
    csproj_files = find_csproj_files(repo_path)

    # PS8 と同じく coverlet.collector が未参照のテストプロジェクトに追加
    for csproj in find_test_csproj_files(repo_path):
        ensure_coverlet(csproj, repo_path)

    # dotnet restore
    print("  dotnet restore ...")
    if not dotnet_restore(repo_path, sln):
        err = "restore failed"
        print(f"  [error] {err}")
        shutil.rmtree(repo_path, ignore_errors=True)
        return [_error_row(full_name, m, err, step1_row) for m in MODELS]

    # ベースライン dotnet test
    print("  Running baseline dotnet test ...")
    baseline_result, baseline_dur, baseline_out = run_dotnet_test(repo_path, sln)
    print(f"  baseline: {baseline_result}  ({baseline_dur:.1f}s)")
    if baseline_result != "PASS":
        print(f"  [debug] baseline output:\n{baseline_out}")

    rows = []

    for model in MODELS:
        unused_dep = parse_list_col(step1_row.get(f"{model}_unused_dep"))
        to_remove  = list(dict.fromkeys(unused_dep))  # runtime deps のみ

        print(f"\n  --- model: {model} ---")
        print(f"  to_remove: {to_remove}")

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

        ir = iterative_removal(repo_path, sln, csproj_files, to_remove)

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
    args = parser.parse_args()

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CLONES_DIR, exist_ok=True)

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
            done_repos = set(prev_df["repo"].unique().tolist())
            all_rows = prev_df.to_dict("records")
            repos = [r for r in repos if r not in done_repos]
            print(f"Resuming: {len(done_repos)} repos already done, {len(repos)} remaining")
        except Exception as e:
            print(f"[warn] could not read existing output ({e}), starting fresh")

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

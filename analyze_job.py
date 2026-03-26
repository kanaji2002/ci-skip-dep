#!/usr/bin/env python3
"""
analyze_job.py - Slurmジョブの結果を固定フォーマットで出力する（全プロジェクト共通）

Usage:
    python3 analyze_job.py <job_id>
    python3 analyze_job.py <job_id> --dataset-only
"""

import sys
import os
import re
import subprocess
import ast
from pathlib import Path

import pandas as pd

BASE_DIR = Path(__file__).parent

PROJECTS = {
    "llama": {
        "dir": BASE_DIR / "DC" / "data-curation-llama",
        "dataset": "data_dependency_waste_project_llama/datasets/final_dataset.csv",
    },
    "knip": {
        "dir": BASE_DIR / "DC" / "data-curation-knip",
        "dataset": "data_dependency_waste_project_knip/datasets/final_dataset.csv",
    },
    "depc": {
        "dir": BASE_DIR / "DC" / "data-curation-depc",
        "dataset": "data_dependency_waste_project/datasets/final_dataset.csv",
    },
    "qwen": {
        "dir": BASE_DIR / "DC" / "data-curation-qwen",
        "dataset": "data_dependency_waste_project_qwen/datasets/final_dataset.csv",
    },
    "all": {
        "dir": BASE_DIR / "DC" / "data-curation-all",
        "dataset": "data_dependency_waste_project/datasets/final_dataset.csv",
        "multi_model": True,
    },
}


# ─── ジョブIDからプロジェクトを特定 ───────────────────────────────────────────

def find_projects_for_job(job_id: str) -> list[tuple[str, Path | None]]:
    """job_id に対応する (project_name, out_file_or_None) のリストを返す。
    out.out がなくてもジョブディレクトリが存在すればそのプロジェクトとみなす。"""
    matches = []
    for name, cfg in PROJECTS.items():
        batch_root = cfg["dir"] / "batch" / "output"
        if not batch_root.exists():
            continue
        for subdir in batch_root.iterdir():
            job_dir = subdir / job_id
            if not job_dir.is_dir():
                continue
            out_file = job_dir / "out.out"
            matches.append((name, out_file if out_file.exists() else None))
    return matches


# ─── sacct でジョブメタデータ取得 ────────────────────────────────────────────

def get_sacct_info(job_id: str) -> dict:
    try:
        result = subprocess.run(
            ["sacct", "-j", job_id,
             "--format=JobID,JobName,State,Elapsed,ExitCode,NodeList,Start,End",
             "--noheader", "--parsable2"],
            capture_output=True, text=True
        )
        lines = [l for l in result.stdout.strip().splitlines()
                 if not l.endswith(".batch") and not l.endswith(".extern")]
        if not lines:
            return {}
        fields = lines[0].split("|")
        keys = ["JobID", "JobName", "State", "Elapsed", "ExitCode", "NodeList", "Start", "End"]
        return dict(zip(keys, fields))
    except Exception:
        return {}


# ─── out.out パース ────────────────────────────────────────────────────────────

def parse_out_file(path: Path) -> dict:
    text = path.read_text()
    result = {
        "node": None,
        "stage": None,
        "gpu": None,
        "pipeline_success": None,
        "pipeline_errors": None,
        "projects": [],
        "total_commits_log": None,
        "skippable_commits_log": None,
        "timing": {},
    }

    for line in text.splitlines():
        if line.startswith("Node: "):
            result["node"] = line.split(": ", 1)[1].strip()
        elif line.startswith("Stage: "):
            result["stage"] = line.split(": ", 1)[1].strip()
        elif line.startswith("GPU: "):
            result["gpu"] = line.split(": ", 1)[1].strip()

    m = re.search(r"Success:\s*(\d+),\s*Errors:\s*(\d+)", text)
    if m:
        result["pipeline_success"] = int(m.group(1))
        result["pipeline_errors"] = int(m.group(2))

    for block in re.finditer(
        r"Processing:\s*(\S+/\S+).*?"
        r"(?:Extracted (\d+) dependency change commits|No dependency change commits found)",
        text, re.DOTALL
    ):
        proj = block.group(1)
        result["projects"].append(proj)

    m = re.search(r"Total commits:\s*(\d+)", text)
    if m:
        result["total_commits_log"] = int(m.group(1))
    m = re.search(r"Skippable commits:\s*(\d+)", text)
    if m:
        result["skippable_commits_log"] = int(m.group(1))

    timing_path = path.parent / "timing.log"
    if timing_path.exists():
        for line in timing_path.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                result["timing"][k.strip()] = v.strip()

    return result


# ─── データセット統計 ──────────────────────────────────────────────────────────

def parse_list_col(val):
    if pd.isna(val) or val in ("", "[]"):
        return []
    try:
        return ast.literal_eval(val)
    except Exception:
        return []


def calc_confusion_matrix(df: pd.DataFrame, sk_col: str) -> dict:
    """build_status が既知の行に限定して混同行列・精度指標を計算する。
    sk_col : is_skippable カラム名 (True/False を文字列または bool で保持)
    """
    if "build_status" not in df.columns or sk_col not in df.columns:
        return {}
    df_eval = df[df["build_status"] != "unknown"].copy()
    if len(df_eval) == 0:
        return {"eval_n": 0}

    is_skip = df_eval[sk_col].astype(str).str.lower() == "true"
    is_success = df_eval["build_status"] == "success"

    TP = int((is_skip &  is_success).sum())
    FP = int((is_skip & ~is_success).sum())
    FN = int((~is_skip &  is_success).sum())
    TN = int((~is_skip & ~is_success).sum())

    precision  = TP / (TP + FP) if (TP + FP) > 0 else None
    recall     = TP / (TP + FN) if (TP + FN) > 0 else None
    f1         = (2 * precision * recall / (precision + recall)
                  if precision is not None and recall is not None
                     and (precision + recall) > 0 else None)
    fpr        = FP / (FP + TN) if (FP + TN) > 0 else None  # 誤スキップ率

    bsec = df_eval["build_seconds"] if "build_seconds" in df_eval.columns else None
    tp_mask = is_skip &  is_success
    fp_mask = is_skip & ~is_success
    fn_mask = ~is_skip &  is_success

    tp_sec = float(df_eval.loc[tp_mask, "build_seconds"].dropna().sum()) if bsec is not None else 0.0
    fp_sec = float(df_eval.loc[fp_mask, "build_seconds"].dropna().sum()) if bsec is not None else 0.0
    fn_sec = float(df_eval.loc[fn_mask, "build_seconds"].dropna().sum()) if bsec is not None else 0.0

    return {
        "eval_n": len(df_eval),
        "TP": TP, "FP": FP, "FN": FN, "TN": TN,
        "precision": precision,
        "recall": recall,
        "f1": f1,
        "fpr": fpr,
        "tp_sec": tp_sec,   # 正しくスキップできた時間
        "fp_sec": fp_sec,   # 誤スキップによる被害時間
        "fn_sec": fn_sec,   # 見逃した削減可能時間
    }


def get_dataset_stats_multi(df: pd.DataFrame) -> dict:
    """マルチモデルデータセット (data-curation-all) 用の統計"""
    MODELS = ["depcheck", "knip", "llama", "qwen", "deepseek"]
    total = len(df)
    result = {
        "multi": True,
        "total": total,
        "models": {},
        "build_counts": df["build_status"].value_counts().to_dict() if "build_status" in df.columns else {},
        "parent_counts": df["parent_build_status"].value_counts().to_dict() if "parent_build_status" in df.columns else {},
        "ci_known": int((df["build_status"] != "unknown").sum()) if "build_status" in df.columns else 0,
        "parent_known": int((df["parent_build_status"] != "unknown").sum()) if "parent_build_status" in df.columns else 0,
    }
    for model in MODELS:
        sk_col = f"{model}_is_skippable"
        dep_col = f"{model}_dep_status"
        if sk_col not in df.columns:
            continue
        skippable = int(df[sk_col].astype(str).str.lower().eq("true").sum())
        dep_counts = df[dep_col].value_counts().to_dict() if dep_col in df.columns else {}
        sk_rows = df[df[sk_col].astype(str).str.lower() == "true"]
        wasted_sec = sk_rows["build_seconds"].dropna().sum() if "build_seconds" in df.columns else 0
        cm = calc_confusion_matrix(df, sk_col)
        result["models"][model] = {
            "skippable": skippable,
            "dep_counts": dep_counts,
            "wasted_sec": wasted_sec,
            "cm": cm,
        }
    return result


def get_dataset_stats(df: pd.DataFrame) -> dict:
    # マルチモデルデータセット (data-curation-all) を検出
    if "depcheck_is_skippable" in df.columns:
        return get_dataset_stats_multi(df)

    total = len(df)
    skippable = int(df["is_skippable"].astype(str).str.lower().eq("true").sum())

    dep_counts = df["dep_status"].value_counts().to_dict()
    build_counts = df["build_status"].value_counts().to_dict()
    parent_counts = df["parent_build_status"].value_counts().to_dict()

    ci_known = int((df["build_status"] != "unknown").sum())
    parent_known = int((df["parent_build_status"] != "unknown").sum())

    is_skip = df["is_skippable"].astype(str).str.lower() == "true"
    skippable_rows = df[is_skip]

    df_ci_known = df[df["build_status"] != "unknown"]
    skippable_in_ci_known = int(
        df_ci_known[df_ci_known["is_skippable"].astype(str).str.lower() == "true"].shape[0]
    )

    df_parent_known = df[df["parent_build_status"] != "unknown"]
    skippable_in_parent_known = int(
        df_parent_known[df_parent_known["is_skippable"].astype(str).str.lower() == "true"].shape[0]
    )

    wasted_sec = skippable_rows["build_seconds"].dropna().sum()
    cm = calc_confusion_matrix(df, "is_skippable")

    return {
        "total": total,
        "skippable": skippable,
        "dep_counts": dep_counts,
        "build_counts": build_counts,
        "parent_counts": parent_counts,
        "ci_known": ci_known,
        "parent_known": parent_known,
        "skippable_in_ci_known": skippable_in_ci_known,
        "skippable_in_parent_known": skippable_in_parent_known,
        "skippable_rows": skippable_rows,
        "wasted_sec": wasted_sec,
        "cm": cm,
    }


# ─── フォーマット出力 ─────────────────────────────────────────────────────────

def fmt_pct(n, total):
    if total == 0:
        return "N/A"
    return f"{n/total*100:.1f}%"


def fmt_status_dist(counts: dict, keys: list) -> str:
    parts = []
    for k in keys:
        v = counts.get(k, 0)
        parts.append(f"{k}={v}")
    return "  ".join(parts)


def print_report(job_id: str, project_name: str, sacct: dict, parsed: dict, stats: dict | None):
    SEP = "=" * 60
    sep = "-" * 60

    print(SEP)
    print(f" Job {job_id} Analysis  [{project_name}]")
    print(SEP)

    # ── Job ──
    print("\n[Job]")
    print(f"  ID      : {job_id}")
    print(f"  Project : {project_name}")
    print(f"  State   : {sacct.get('State', 'unknown')}")
    print(f"  Node    : {parsed.get('node') or sacct.get('NodeList', 'unknown')}")
    print(f"  GPU     : {parsed.get('gpu', 'unknown')}")
    elapsed = sacct.get("Elapsed", parsed["timing"].get("total_sec", "?"))
    total_sec = parsed["timing"].get("total_sec", "?")
    pipeline_sec = parsed["timing"].get("pipeline_sec", "?")
    print(f"  Elapsed : {elapsed}  (total={total_sec}s  pipeline={pipeline_sec}s)")
    start = parsed["timing"].get("start") or sacct.get("Start", "?")
    end   = parsed["timing"].get("end")   or sacct.get("End", "?")
    print(f"  Period  : {start}  →  {end}")

    # ── Pipeline ──
    print(f"\n[Pipeline]")
    success = parsed["pipeline_success"]
    errors  = parsed["pipeline_errors"]
    n_proj  = len(parsed["projects"])
    print(f"  Projects : {n_proj} attempted  (Success={success}  Errors={errors})")
    if parsed["projects"]:
        print(f"  List     : {', '.join(parsed['projects'])}")

    # ── Dataset ──
    if stats and stats.get("multi"):
        # マルチモデルデータセット (data-curation-all)
        total = stats["total"]
        ci_known = stats["ci_known"]
        parent_known = stats["parent_known"]
        print(f"\n[Dataset]  (final_dataset.csv 累計 / multi-model)")
        print(f"  Total commits   : {total}")
        print(f"  CI coverage     : {ci_known}/{total}  ({fmt_pct(ci_known, total)})")
        print(f"  Parent coverage : {parent_known}/{total}  ({fmt_pct(parent_known, total)})")
        print(f"  build_status : {fmt_status_dist(stats['build_counts'], ['success','failure','unknown'])}")
        print(f"  parent_build : {fmt_status_dist(stats['parent_counts'], ['success','failure','unknown'])}")
        print(f"\n  {'Model':<10}  {'Skippable':>10}  {'Rate':>8}  {'Wasted':>10}  dep_status")
        print(f"  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*10}  {'-'*30}")
        for model, ms in stats["models"].items():
            sk = ms["skippable"]
            wsec = ms["wasted_sec"]
            dep_str = fmt_status_dist(ms["dep_counts"], ["unused", "unused_dev", "in_use", "unknown"])
            print(f"  {model:<10}  {sk:>10}  {fmt_pct(sk, total):>8}  {wsec:>8.0f}s  {dep_str}")

        # 混同行列・精度指標 (CI既知行のみ)
        any_cm = any(ms["cm"].get("eval_n", 0) > 0 for ms in stats["models"].values())
        if any_cm:
            print(f"\n[Accuracy]  (build_status が既知の行のみ / eval_n 行)")
            print(f"  {'Model':<10}  {'eval_n':>7}  {'TP':>5}  {'FP':>5}  {'FN':>5}  {'TN':>5}  "
                  f"{'Prec':>7}  {'Recall':>7}  {'F1':>7}  {'FPR':>7}  "
                  f"{'SavedSec':>10}  {'UnsafeSec':>10}  {'MissedSec':>10}")
            print(f"  {'-'*10}  {'-'*7}  {'-'*5}  {'-'*5}  {'-'*5}  {'-'*5}  "
                  f"{'-'*7}  {'-'*7}  {'-'*7}  {'-'*7}  "
                  f"{'-'*10}  {'-'*10}  {'-'*10}")
            for model, ms in stats["models"].items():
                cm = ms["cm"]
                if not cm or cm.get("eval_n", 0) == 0:
                    print(f"  {model:<10}  (no CI data)")
                    continue
                fmt_rate = lambda v: f"{v*100:.1f}%" if v is not None else "N/A"
                print(f"  {model:<10}  {cm['eval_n']:>7}  {cm['TP']:>5}  {cm['FP']:>5}  "
                      f"{cm['FN']:>5}  {cm['TN']:>5}  "
                      f"{fmt_rate(cm['precision']):>7}  {fmt_rate(cm['recall']):>7}  "
                      f"{fmt_rate(cm['f1']):>7}  {fmt_rate(cm['fpr']):>7}  "
                      f"{cm['tp_sec']:>9.0f}s  {cm['fp_sec']:>9.0f}s  {cm['fn_sec']:>9.0f}s")
    elif stats:
        total = stats["total"]
        skippable = stats["skippable"]
        ci_known = stats["ci_known"]
        parent_known = stats["parent_known"]
        sk_in_ci = stats["skippable_in_ci_known"]
        sk_in_par = stats["skippable_in_parent_known"]

        print(f"\n[Dataset]  (final_dataset.csv 累計)")
        print(f"  Total commits     : {total}")
        print(f"  Skippable         : {skippable}  ({fmt_pct(skippable, total)})  ← 全体比")
        print(f"  CI coverage       : {ci_known}/{total}  ({fmt_pct(ci_known, total)})")
        print(f"  Parent coverage   : {parent_known}/{total}  ({fmt_pct(parent_known, total)})")
        print(f"  Skippable / CI known     : {sk_in_ci}/{ci_known}  ({fmt_pct(sk_in_ci, ci_known)})")
        print(f"  Skippable / Parent known : {sk_in_par}/{parent_known}  ({fmt_pct(sk_in_par, parent_known)})")

        print(f"\n  dep_status   : {fmt_status_dist(stats['dep_counts'], ['unused','unused_dev','in_use','missing','unknown'])}")
        print(f"  build_status : {fmt_status_dist(stats['build_counts'], ['success','failure','unknown'])}")
        print(f"  parent_build : {fmt_status_dist(stats['parent_counts'], ['success','failure','unknown'])}")

        if skippable > 0:
            wsec = stats["wasted_sec"]
            print(f"  Wasted CI time : {wsec:.0f}s  ({wsec/3600:.2f}h)")

        cm = stats.get("cm", {})
        if cm.get("eval_n", 0) > 0:
            fmt_rate = lambda v: f"{v*100:.1f}%" if v is not None else "N/A"
            print(f"\n[Accuracy]  (build_status が既知の {cm['eval_n']} 行のみ)")
            print(f"  TP={cm['TP']}  FP={cm['FP']}  FN={cm['FN']}  TN={cm['TN']}")
            print(f"  Precision : {fmt_rate(cm['precision'])}  ← スキップ可判定のうち実際に安全だった割合")
            print(f"  Recall    : {fmt_rate(cm['recall'])}  ← 安全なビルドをスキップ可と拾えた割合")
            print(f"  F1        : {fmt_rate(cm['f1'])}")
            print(f"  FPR       : {fmt_rate(cm['fpr'])}  ← 失敗ビルドを誤スキップした割合 (低いほど安全)")
            print(f"  Saved time (TP)  : {cm['tp_sec']:.0f}s  ({cm['tp_sec']/3600:.2f}h)  ← 正しく削減できた時間")
            print(f"  Unsafe time (FP) : {cm['fp_sec']:.0f}s  ({cm['fp_sec']/3600:.2f}h)  ← 誤スキップの被害時間")
            print(f"  Missed time (FN) : {cm['fn_sec']:.0f}s  ({cm['fn_sec']/3600:.2f}h)  ← 見逃した削減可能時間")

        sk_df = stats["skippable_rows"]
        if len(sk_df) > 0:
            print(f"\n[Skippable Commits]")
            print(f"  {'repo':<45}  {'sha':<9}  {'dep':<20}  {'build_sec':>10}")
            print(f"  {sep}")
            for _, row in sk_df.iterrows():
                repo = f"{row.get('owner','')}/{row.get('repo','')}"
                sha = str(row.get("sha", ""))[:8]
                dep = str(row.get("upgraded_dep", ""))
                bsec = row.get("build_seconds", "")
                try:
                    import math
                    bsec_str = f"{float(bsec):.0f}s" if bsec not in (None, "", float("nan")) and not (isinstance(bsec, float) and math.isnan(bsec)) else "N/A"
                except Exception:
                    bsec_str = "N/A"
                print(f"  {repo:<45}  {sha:<9}  {dep:<20}  {bsec_str:>10}")
    else:
        print(f"\n[Dataset]  (final_dataset.csv が見つかりません)")
        if parsed["total_commits_log"] is not None:
            print(f"  Total commits (log)     : {parsed['total_commits_log']}")
            print(f"  Skippable commits (log) : {parsed['skippable_commits_log']}")

    print(f"\n{SEP}")


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    if len(sys.argv) < 2:
        print("Usage: python3 analyze_job.py <job_id> [--dataset-only]")
        sys.exit(1)

    job_id = sys.argv[1]
    dataset_only = "--dataset-only" in sys.argv

    # どのプロジェクトか自動検出
    matches = find_projects_for_job(job_id)

    if not matches:
        # out.out が見つからない場合はsacctだけで出力
        print(f"[WARN] No out.out found for job {job_id} in any project.")
        sacct = get_sacct_info(job_id)
        if sacct:
            print(f"  State   : {sacct.get('State', 'unknown')}")
            print(f"  Node    : {sacct.get('NodeList', 'unknown')}")
            print(f"  Elapsed : {sacct.get('Elapsed', '?')}")
        sys.exit(1)

    sacct = get_sacct_info(job_id) if not dataset_only else {}

    for project_name, out_file in matches:
        cfg = PROJECTS[project_name]
        parsed = parse_out_file(out_file) if out_file else {
            "node": None, "stage": None, "gpu": None,
            "pipeline_success": None, "pipeline_errors": None,
            "projects": [], "total_commits_log": None,
            "skippable_commits_log": None, "timing": {}
        }

        stats = None
        if cfg.get("multi_model"):
            base = cfg["dir"] / "data_dependency_waste_project"
            repo_list_path = parsed["timing"].get("repo_list", "")
            batch_index = parsed["timing"].get("batch_index", "")
            if repo_list_path:
                stem = os.path.splitext(os.path.basename(repo_list_path))[0]
                if batch_index:
                    # 案A構造: {stem}/datasets/final_dataset_{index}.csv
                    target = base / stem / "datasets" / f"final_dataset_{batch_index}.csv"
                    csv_files = [target] if target.exists() else []
                else:
                    # batch_index なし: {stem}/datasets/final_dataset.csv
                    target = base / stem / "datasets" / "final_dataset.csv"
                    csv_files = [target] if target.exists() else []
            else:
                # timing.log がない場合は全サブディレクトリを収集
                csv_files = sorted(base.glob("*/datasets/final_dataset*.csv")) if base.exists() else []
            # 旧構造 (サブディレクトリなし) もフォールバックとして確認
            legacy = cfg["dir"] / cfg["dataset"]
            if not csv_files and legacy.exists():
                csv_files = [legacy]
            if csv_files:
                df = pd.concat([pd.read_csv(p) for p in csv_files], ignore_index=True)
                stats = get_dataset_stats(df)
        else:
            dataset_path = cfg["dir"] / cfg["dataset"]
            if dataset_path.exists():
                df = pd.read_csv(dataset_path)
                stats = get_dataset_stats(df)

        print_report(job_id, project_name, sacct, parsed, stats)
        if len(matches) > 1:
            print()


if __name__ == "__main__":
    main()

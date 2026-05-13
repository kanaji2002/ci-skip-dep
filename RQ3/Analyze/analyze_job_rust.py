#!/usr/bin/env rust
"""
analyze_job_rust.py - Rust ジョブの final_dataset.csv を集約して精度指標を出力する

Usage:
    python3 analyze_job_rust.py
    python3 analyze_job_rust.py --per-job   # ジョブ別の内訳も表示
"""

import sys
import os
import ast
from pathlib import Path

import pandas as pd

    # ─── 解析対象ジョブ番号 (Rust / rq3_rust   ) ────────────────────────────────────
JOB_IDS = [
    # Updated 154 csv files
    4262234,
    4262247,
    4262248,
    4262249,
    4262250,
    4262251,
    4262252,
    4262253,
    4262254,
    4262255,
    4262256,
    4262258,
    4262259,
    4262260,
    4262261,
    4262262,
    4262263,
    4262264,
    4262265,
    4262266,
    4262267,
    4262268,
    4262269,
    4262270,
    4262271,
    4262272,
    4262273,
    4262274,
    4262275,
    4262276,
    4262277,
    4262278,
    4262279,
    4262280,
    4262281,
    4262282,
    4262283,
    4262284,
    4262285,
    4262286,
    4262287,
    4262288,
    4262289,
    4262290,
    4262291,
    4262292,
    4262293,
    4262294,
    4262295,
    4262296,
    4262297,
    4262299,
    4262300,
    4262301,
    4262302,
    4262303,
    4262304,
    4262305,
    4262306,
    4262307,
    4262308,
    4262309,
    4262310,
    4262311,
    4262312,
    4262313,
    4262314,
    4262315,
    4262316,
    4262317,
    4262318,
    4262319,
    4262320,
    4262321,
    4262322,
    4262323,
    4262324,
    4262325,
    4262326,
    4262327,
    4262328,
    4262476,
    4262477,
    4262478,
    4262479,
    4262480,
    4262481,
    4262482,
    4262483,
    4262484,
    4262485,
    4262486,
    4262487,
    4262488,
    4262489,
    4262490,
    4262491,
    4262492,
    4262493,
    4262494,
    4262495,
    4262497,
    4262498,
    4262499,
    4262500,
    4262501,
    4262502,
    4262503,
    4262504,
    4262505,
    4262506,
    4262507,
    4262508,
    4262509,
    4262510,
    4262511,
    4262512,
    4262513,
    4262514,
    4262515,
    4262516,
    4262517,
    4262518,
    4262519,
    4262520,
    4262521,
    4262522,
    4262523,
    4262524,
    4262525,
    4262526,
    4262527,
    4262528,
    4262529,
    4262530,
    4262531,
    4262532,
    4262533,
    4262534,
    4262535,
    4262536,
    4262537,
    4262538,
    4262539,
    4262711,
    4262712,
    4262713,
    4262714,
    4262715,
    4262716,
    4262717,
    4262718,
    4262719,
    4262720,
    4262721,
]
# ──────────────────────────────────────────────────────────────────────────────

BASE_DIR    = Path(__file__).parent.parent.parent   # /work/rintaro-k/research
ALL_DIR     = BASE_DIR / "DC" / "data-curation-all"
BATCH_ROOT  = ALL_DIR / "batch" / "output" / "pipeline_all"
DATA_ROOT   = ALL_DIR / "data_dependency_waste_project"

RUST_LANG_DIR  = BASE_DIR / "RQ3" / "RQ3-2" / "rust"
RUST_BATCH_ROOT = RUST_LANG_DIR / "batch" / "output" / "rq3_rust"
RUST_DATA_ROOT  = RUST_LANG_DIR / "data_dependency_waste_project"

MODELS = ["llama", "qwen", "deepseek"]


# ─── ジョブからデータセット CSV を特定 ────────────────────────────────────────

def find_csv_for_job(job_id: int) -> list[Path]:
    rust_job_dir = RUST_BATCH_ROOT / str(job_id)
    js_job_dir     = BATCH_ROOT / str(job_id)

    if rust_job_dir.is_dir():
        job_dir   = rust_job_dir
        data_root = RUST_DATA_ROOT
    elif js_job_dir.is_dir():
        job_dir   = js_job_dir
        data_root = DATA_ROOT
    else:
        print(f"[WARN] job {job_id}: ディレクトリが見つかりません ({js_job_dir} / {rust_job_dir})")
        return []

    timing_path = job_dir / "timing.log"
    if not timing_path.exists():
        print(f"[WARN] job {job_id}: timing.log が見つかりません")
        return []

    timing = {}
    for line in timing_path.read_text().splitlines():
        if "=" in line:
            k, v = line.split("=", 1)
            timing[k.strip()] = v.strip()

    repo_list  = timing.get("repo_list", "")
    batch_idx  = timing.get("batch_index", "")

    if not repo_list:
        print(f"[WARN] job {job_id}: timing.log に repo_list なし")
        return []

    stem = os.path.splitext(os.path.basename(repo_list))[0]
    base = data_root / stem / "datasets"

    if batch_idx:
        target = base / f"final_dataset_{batch_idx}.csv"
    else:
        target = base / "final_dataset.csv"

    if not target.exists():
        print(f"[WARN] job {job_id}: CSV が見つかりません ({target})")
        return []

    return [target]


# ─── カラム計算ヘルパー ───────────────────────────────────────────────────────

def parse_list_col(val):
    if pd.isna(val) or val in ("", "[]"):
        return []
    try:
        return ast.literal_eval(val)
    except Exception:
        return []


def calc_confusion_matrix(df: pd.DataFrame, sk_col: str) -> dict:
    if "build_status" not in df.columns or sk_col not in df.columns:
        return {}
    df_eval = df[df["build_status"] != "unknown"].copy()
    if len(df_eval) == 0:
        return {"eval_n": 0}

    is_skip    = df_eval[sk_col].astype(str).str.lower() == "true"
    is_success = df_eval["build_status"] == "success"

    TP = int(( is_skip &  is_success).sum())
    FP = int(( is_skip & ~is_success).sum())
    FN = int((~is_skip &  is_success).sum())
    TN = int((~is_skip & ~is_success).sum())

    precision = TP / (TP + FP) if (TP + FP) > 0 else None
    recall    = TP / (TP + FN) if (TP + FN) > 0 else None
    f1        = (2 * precision * recall / (precision + recall)
                 if precision is not None and recall is not None
                    and (precision + recall) > 0 else None)
    fpr       = FP / (FP + TN) if (FP + TN) > 0 else None

    bsec_col = "build_seconds" if "build_seconds" in df_eval.columns else None
    tp_sec = float(df_eval.loc[ is_skip &  is_success, "build_seconds"].dropna().sum()) if bsec_col else 0.0
    fp_sec = float(df_eval.loc[ is_skip & ~is_success, "build_seconds"].dropna().sum()) if bsec_col else 0.0
    fn_sec = float(df_eval.loc[~is_skip &  is_success, "build_seconds"].dropna().sum()) if bsec_col else 0.0

    return {
        "eval_n": len(df_eval),
        "TP": TP, "FP": FP, "FN": FN, "TN": TN,
        "precision": precision, "recall": recall, "f1": f1, "fpr": fpr,
        "tp_sec": tp_sec, "fp_sec": fp_sec, "fn_sec": fn_sec,
    }


# ─── データセット統計 ──────────────────────────────────────────────────────────

def get_stats(df: pd.DataFrame) -> dict:
    total = len(df)
    ci_known     = int((df["build_status"]        != "unknown").sum()) if "build_status"        in df.columns else 0
    parent_known = int((df["parent_build_status"] != "unknown").sum()) if "parent_build_status" in df.columns else 0
    build_counts  = df["build_status"].value_counts().to_dict()        if "build_status"        in df.columns else {}
    parent_counts = df["parent_build_status"].value_counts().to_dict() if "parent_build_status" in df.columns else {}

    models_out = {}
    for model in MODELS:
        sk_col  = f"{model}_is_skippable"
        dep_col = f"{model}_dep_status"
        if sk_col not in df.columns:
            continue
        skippable = int(df[sk_col].astype(str).str.lower().eq("true").sum())
        dep_counts = df[dep_col].value_counts().to_dict() if dep_col in df.columns else {}
        sk_rows = df[df[sk_col].astype(str).str.lower() == "true"]
        wasted_sec = float(sk_rows["build_seconds"].dropna().sum()) if "build_seconds" in df.columns else 0.0
        cm = calc_confusion_matrix(df, sk_col)
        models_out[model] = {
            "skippable": skippable,
            "dep_counts": dep_counts,
            "wasted_sec": wasted_sec,
            "cm": cm,
        }

    return {
        "total": total,
        "ci_known": ci_known,
        "parent_known": parent_known,
        "build_counts": build_counts,
        "parent_counts": parent_counts,
        "models": models_out,
    }


# ─── フォーマット出力 ─────────────────────────────────────────────────────────

def fmt_pct(n, total):
    return f"{n/total*100:.1f}%" if total > 0 else "N/A"

def fmt_rate(v):
    return f"{v*100:.1f}%" if v is not None else "N/A"

def fmt_status_dist(counts, keys):
    return "  ".join(f"{k}={counts.get(k, 0)}" for k in keys)


def print_stats(stats: dict, title: str = ""):
    SEP = "=" * 70
    sep = "-" * 70
    total        = stats["total"]
    ci_known     = stats["ci_known"]
    parent_known = stats["parent_known"]

    if title:
        print(f"\n{SEP}")
        print(f" {title}")
    print(f"{SEP}")
    print(f"\n[Dataset Summary]")
    print(f"  Total commits   : {total}")
    print(f"  CI coverage     : {ci_known}/{total}  ({fmt_pct(ci_known, total)})")
    print(f"  Parent coverage : {parent_known}/{total}  ({fmt_pct(parent_known, total)})")
    print(f"  build_status    : {fmt_status_dist(stats['build_counts'], ['success','failure','unknown'])}")
    print(f"  parent_build    : {fmt_status_dist(stats['parent_counts'], ['success','failure','unknown'])}")

    print(f"\n[Skippable & Wasted CI]")
    print(f"  {'Model':<10}  {'Skippable':>10}  {'Rate':>8}  {'Wasted(h)':>10}  dep_status")
    print(f"  {'-'*10}  {'-'*10}  {'-'*8}  {'-'*10}  {'-'*35}")
    for model, ms in stats["models"].items():
        sk    = ms["skippable"]
        wsec  = ms["wasted_sec"]
        dstr  = fmt_status_dist(ms["dep_counts"], ["unused","unused_dev","in_use","unknown"])
        print(f"  {model:<10}  {sk:>10}  {fmt_pct(sk, total):>8}  {wsec/3600:>9.2f}h  {dstr}")

    any_cm = any(ms["cm"].get("eval_n", 0) > 0 for ms in stats["models"].values())
    if any_cm:
        print(f"\n[Accuracy]  (build_status が既知の行のみ)")
        hdr = (f"  {'Model':<10}  {'eval_n':>7}  {'TP':>5}  {'FP':>5}  {'FN':>5}  {'TN':>5}  "
               f"{'Prec':>7}  {'Recall':>7}  {'F1':>7}  {'FPR':>7}  "
               f"{'Saved(h)':>9}  {'Unsafe(h)':>10}  {'Missed(h)':>10}")
        print(hdr)
        print(f"  {sep}")
        for model, ms in stats["models"].items():
            cm = ms["cm"]
            if not cm or cm.get("eval_n", 0) == 0:
                print(f"  {model:<10}  (no CI data)")
                continue
            print(f"  {model:<10}  {cm['eval_n']:>7}  {cm['TP']:>5}  {cm['FP']:>5}  "
                  f"{cm['FN']:>5}  {cm['TN']:>5}  "
                  f"{fmt_rate(cm['precision']):>7}  {fmt_rate(cm['recall']):>7}  "
                  f"{fmt_rate(cm['f1']):>7}  {fmt_rate(cm['fpr']):>7}  "
                  f"{cm['tp_sec']/3600:>8.2f}h  {cm['fp_sec']/3600:>9.2f}h  {cm['fn_sec']/3600:>9.2f}h")
    print(f"\n{SEP}")


# ─── main ────────────────────────────────────────────────────────────────────

def main():
    per_job = "--per-job" in sys.argv

    print(f"解析ジョブ: {JOB_IDS}")

    # ジョブごとにCSVを収集
    job_csv_map: dict[int, list[Path]] = {}
    all_csvs: list[Path] = []
    for job_id in JOB_IDS:
        csvs = find_csv_for_job(job_id)
        job_csv_map[job_id] = csvs
        all_csvs.extend(csvs)

    if not all_csvs:
        print("[ERROR] 有効なデータセット CSV が見つかりませんでした。")
        sys.exit(1)

    # ジョブ別の出力
    if per_job:
        for job_id, csvs in job_csv_map.items():
            if not csvs:
                print(f"\n[SKIP] job {job_id}: CSV なし")
                continue
            df_job = pd.concat([pd.read_csv(p) for p in csvs], ignore_index=True)
            stats  = get_stats(df_job)
            print_stats(stats, title=f"Job {job_id}  ({', '.join(str(p) for p in csvs)})")

    # 全ジョブ合算
    print(f"\n合算 CSV 数: {len(all_csvs)}")
    df_all = pd.concat([pd.read_csv(p) for p in all_csvs], ignore_index=True)
    stats_all = get_stats(df_all)
    print_stats(stats_all, title=f"全ジョブ合算  (jobs={JOB_IDS})")


if __name__ == "__main__":
    main()

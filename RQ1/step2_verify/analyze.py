"""
Step 2 結果分析: 各ツールの「未使用依存削除の安全性」を評価する。

前提: baseline_result == "PASS" のリポジトリのみを対象とする。

指標:
  - n_total    : baselineがPASSのリポジトリ数
  - n_skip     : 削除対象なし (ツールが未使用依存を検知しなかった)
  - n_pass     : 削除後もテストがPASS → 安全に削除できた (true positive)
  - n_fail     : 削除後にテストがFAIL → 誤検知 (false positive)
  - n_error    : テスト実行エラー
  - precision  : n_pass / (n_pass + n_fail)  ← 削除提案の正確さ
  - avg_removed: 1リポジトリあたりの平均削除dep数 (PASS+FAILケースのみ)

使い方:
    python3 analyze.py
    python3 analyze.py --input /path/to/step2_results.csv
"""

import argparse
import ast
import os

import pandas as pd

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
_DIR = os.path.dirname(os.path.abspath(__file__))
RQ1_DIR = os.path.dirname(_DIR)
DEFAULT_INPUT = os.path.join(RQ1_DIR, "output", "step2_results.csv")

MODELS = ["depcheck", "knip", "llama", "qwen", "deepseek"]


# ---------------------------------------------------------------------------
# ユーティリティ
# ---------------------------------------------------------------------------

def parse_list_col(val) -> list:
    """CSV から読み込んだリスト列を List[str] に変換"""
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


def count_removed(row) -> int:
    n = len(parse_list_col(row["removed_deps"]))
    if "removed_dev_deps" in row.index:
        n += len(parse_list_col(row["removed_dev_deps"]))
    return n


# ---------------------------------------------------------------------------
# 分析
# ---------------------------------------------------------------------------

def pkg_precision(df_pass: pd.DataFrame, model: str) -> tuple:
    """
    パッケージ単位の精度を計算する。
    safe_deps の総数 / (safe_deps + must_keep_deps の総数)
    must_keep_deps カラムが存在する場合のみ有効。
    """
    if "must_keep_deps" not in df_pass.columns:
        return float("nan"), 0, 0
    sub = df_pass[df_pass["model"] == model]
    n_safe = sum(len(parse_list_col(v)) for v in sub["removed_deps"])
    n_must = sum(len(parse_list_col(v)) for v in sub["must_keep_deps"])
    if n_safe + n_must == 0:
        return float("nan"), n_safe, n_must
    return n_safe / (n_safe + n_must), n_safe, n_must


def analyze(df: pd.DataFrame) -> pd.DataFrame:
    """
    各モデルの結果を集計する。
    baseline_result == "PASS" のみを対象。
    SKIP(baseline失敗) と SKIP(no deps) を区別する。
    """
    # baseline PASS のみ
    df_pass = df[df["baseline_result"] == "PASS"].copy()

    # 削除dep数カラム (safe_deps のみ)
    df_pass["n_removed"] = df_pass.apply(count_removed, axis=1)

    rows = []
    for model in MODELS:
        sub = df_pass[df_pass["model"] == model]

        n_total = len(sub)
        n_skip_no_dep = len(sub[
            (sub["post_removal_result"] == "SKIP") &
            (sub["error"].fillna("") == "no deps to remove")
        ])
        n_skip_other = len(sub[
            (sub["post_removal_result"] == "SKIP") &
            (sub["error"].fillna("") != "no deps to remove")
        ])
        n_pass  = len(sub[sub["post_removal_result"] == "PASS"])
        n_fail  = len(sub[sub["post_removal_result"] == "FAIL"])
        n_error = len(sub[sub["post_removal_result"] == "ERROR"])

        # リポジトリ単位 precision
        repo_precision = n_pass / (n_pass + n_fail) if (n_pass + n_fail) > 0 else float("nan")

        # パッケージ単位 precision (must_keep_deps カラムがあれば)
        pkg_prec, n_safe_pkgs, n_must_pkgs = pkg_precision(df_pass, model)

        # 提案ありケースでの平均削除dep数 (safe_deps)
        sub_proposed = sub[sub["post_removal_result"].isin(["PASS", "FAIL", "ERROR"])]
        avg_removed = sub_proposed["n_removed"].mean() if len(sub_proposed) > 0 else float("nan")

        rows.append({
            "model":               model,
            "n_total(baseline=PASS)": n_total,
            "n_skip(no deps)":     n_skip_no_dep,
            "n_skip(other)":       n_skip_other,
            "n_pass":              n_pass,
            "n_fail":              n_fail,
            "n_error":             n_error,
            "repo_precision":      round(repo_precision, 3) if not pd.isna(repo_precision) else "N/A",
            "pkg_precision":       round(pkg_prec, 3) if not pd.isna(pkg_prec) else "N/A",
            "n_safe_pkgs":         n_safe_pkgs,
            "n_must_keep_pkgs":    n_must_pkgs,
            "avg_removed_deps":    round(avg_removed, 1) if not pd.isna(avg_removed) else "N/A",
        })

    return pd.DataFrame(rows)


def per_repo_detail(df: pd.DataFrame) -> pd.DataFrame:
    """リポジトリ×モデルの詳細テーブル (baseline=PASS のみ)"""
    df_pass = df[df["baseline_result"] == "PASS"].copy()
    df_pass["n_removed"] = df_pass.apply(count_removed, axis=1)
    cols = ["repo", "model", "n_removed", "removed_deps",
            "post_removal_result", "error"]
    if "removed_dev_deps" in df_pass.columns:
        cols.insert(4, "removed_dev_deps")
    return df_pass[cols].reset_index(drop=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", default=DEFAULT_INPUT)
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[error] file not found: {args.input}")
        return

    df = pd.read_csv(args.input)
    n_repos = df["repo"].nunique()
    n_baseline_pass = df[df["baseline_result"] == "PASS"]["repo"].nunique()

    print(f"Repos: {n_repos}  |  baseline=PASS: {n_baseline_pass}  |  baseline=FAIL/ERROR: {n_repos - n_baseline_pass}")

    print("\n=== Per-Model Summary (baseline=PASS only) ===")
    summary = analyze(df)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 120)
    print(summary.to_string(index=False))


if __name__ == "__main__":
    main()

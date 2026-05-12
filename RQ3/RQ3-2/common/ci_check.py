# common/ci_check.py
"""スキップ可能性判定 (LLM 3モデル版: llama / qwen / deepseek)"""

import os
import pandas as pd
from typing import Dict, List, Any, Optional
from datetime import datetime

import config

MODELS = ["llama", "qwen", "deepseek"]


def get_build_status_from_ci_data(ci_data: Any) -> str:
    if ci_data is None:
        return "unknown"
    try:
        check_runs, status_code = ci_data
        if status_code != 200 or check_runs is None or len(check_runs) == 0:
            return "unknown"
        conclusions = [r.get("conclusion") for r in check_runs if r.get("conclusion")]
        if not conclusions:
            return "unknown"
        if "failure" in conclusions:
            return "failure"
        if all(c == "success" for c in conclusions):
            return "success"
        return "unknown"
    except Exception:
        return "unknown"


def get_build_seconds_from_ci_data(ci_data: Any) -> Optional[float]:
    if ci_data is None:
        return None
    try:
        check_runs, status_code = ci_data
        if status_code != 200 or check_runs is None or len(check_runs) == 0:
            return None
        total_seconds = 0
        count = 0
        for run in check_runs:
            started_at = run.get("started_at")
            completed_at = run.get("completed_at")
            if started_at and completed_at:
                try:
                    start = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                    end = datetime.fromisoformat(completed_at.replace("Z", "+00:00"))
                    seconds = (end - start).total_seconds()
                    if seconds > 0:
                        total_seconds += seconds
                        count += 1
                except Exception:
                    continue
        return total_seconds if count > 0 else None
    except Exception:
        return None


def get_dependency_status(
    upgraded_dep: str,
    unused_deps: List[str],
    unused_dev_deps: List[str],
    missing_deps: List[str],
    dev_deps: Dict,
    runtime_deps: Dict,
) -> str:
    if upgraded_dep in (unused_deps or []):
        return "unused"
    if upgraded_dep in (unused_dev_deps or []):
        return "unused_dev"
    if upgraded_dep in (missing_deps or []):
        return "missing"
    if upgraded_dep in (dev_deps or {}) or upgraded_dep in (runtime_deps or {}):
        return "in_use"
    return "unknown"


def is_skippable(
    upgraded_dep: str,
    unused_deps: List[str],
    unused_dev_deps: List[str],
    parent_build_status: str,
) -> bool:
    all_unused = (unused_deps or []) + (unused_dev_deps or [])
    return (upgraded_dep in all_unused) and (parent_build_status == "success")


def analyze_skippability(data: pd.DataFrame) -> pd.DataFrame:
    data["build_status"] = data["ci_data"].map(get_build_status_from_ci_data)
    data["parent_build_status"] = data["parent_ci_data"].map(get_build_status_from_ci_data)
    data["build_seconds"] = data["ci_data"].map(get_build_seconds_from_ci_data)

    for model in MODELS:
        def _dep_status(row, m=model):
            return get_dependency_status(
                row.get("upgraded_dep", ""),
                row.get(f"{m}_unused_dep", []),
                row.get(f"{m}_unused_dev_dep", []),
                row.get(f"{m}_missing_dep", []),
                row.get("dev_dep", {}),
                row.get("runtime_dep", {}),
            )

        def _is_skippable(row, m=model):
            return is_skippable(
                row.get("upgraded_dep", ""),
                row.get(f"{m}_unused_dep", []),
                row.get(f"{m}_unused_dev_dep", []),
                row.get("parent_build_status", "unknown"),
            )

        data[f"{model}_dep_status"] = data.apply(_dep_status, axis=1)
        data[f"{model}_is_skippable"] = data.apply(_is_skippable, axis=1)

    return data


def create_final_dataset(output_path: str = None) -> pd.DataFrame:
    if output_path is None:
        output_path = config.get_final_dataset_path()

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    dependency_data_dir = config.PATHS["dependency_data"]
    all_data = []

    for filename in os.listdir(dependency_data_dir):
        if not filename.endswith("_dependency_data.json"):
            continue
        filepath = os.path.join(dependency_data_dir, filename)
        try:
            data = pd.read_json(filepath)
            if len(data) == 0:
                continue
            parts = filename.replace("_dependency_data.json", "").split("_")
            if len(parts) >= 2:
                data["owner"] = parts[0]
                data["repo"] = "_".join(parts[1:])
            data = analyze_skippability(data)
            all_data.append(data)
        except Exception as e:
            print(f"  Error reading {filename}: {e}")
            continue

    if not all_data:
        print("  No dependency data found")
        return pd.DataFrame()

    final_df = pd.concat(all_data, ignore_index=True)

    base_columns = [
        "owner", "repo", "sha", "parent_sha", "author", "email", "datetime",
        "upgraded_dep", "build_status", "parent_build_status", "build_seconds",
    ]
    model_columns = []
    for model in MODELS:
        model_columns += [f"{model}_dep_status", f"{model}_is_skippable"]
    raw_columns = []
    for model in MODELS:
        raw_columns += [
            f"{model}_unused_dep",
            f"{model}_unused_dev_dep",
            f"{model}_missing_dep",
        ]

    columns_to_keep = base_columns + model_columns + raw_columns
    available_columns = [c for c in columns_to_keep if c in final_df.columns]
    final_df = final_df[available_columns]

    final_df.to_csv(output_path, index=False)
    print(f"  Final dataset saved: {output_path}")
    print(f"  Total commits: {len(final_df)}")

    for model in MODELS:
        col = f"{model}_is_skippable"
        if col in final_df.columns:
            n = int(final_df[col].sum())
            print(f"  Skippable ({model}): {n} ({n/len(final_df)*100:.2f}%)")

    json_path = output_path.replace(".csv", ".json")
    final_df.to_json(json_path, orient="records", indent=2)

    return final_df

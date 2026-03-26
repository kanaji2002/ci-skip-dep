#!/usr/bin/env python3
# pipeline_main.py
"""
メインパイプライン - エントリーポイント

使用方法:
    python pipeline_main.py --repo-list /path/to/repos.csv
    python pipeline_main.py --repo-list /path/to/repos.csv --limit 5
    python pipeline_main.py --repo-list /path/to/repos.csv --final-only
"""

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import config
from step1_project_selection import get_project_list
from step2_data_curation.dc1_extract_commits import clone_and_extract_commits, extract_dependency_commits
from step2_data_curation.dc2_ci_data import fetch_ci_data_for_commits
from step2_data_curation.dc3_dependency_models import run_all_models_for_commits
from step3_skip_analysis.ci_check import create_final_dataset
from utils.git_utils import cleanup_repository, get_git_log


def process_single_repository(
    owner: str,
    repo: str,
    skip_clone: bool = False,
    skip_cleanup: bool = False,
) -> bool:
    """
    単一リポジトリを処理

    処理フロー:
    1. クローン → 2. DC1コミット抽出 → 3. DC3 CI取得 → 4. DC2全モデル (CI取得済みのみ) → 5. クリーンアップ
    """
    print(f"\n{'='*60}")
    print(f"Processing: {owner}/{repo}")
    print(f"{'='*60}")

    try:
        # Step 1: クローンとコミット抽出
        if skip_clone:
            print("  [DC1] Extracting commits from existing clone...")
            try:
                get_git_log(owner, repo)
            except Exception as e:
                print(f"  Warning: Failed to get git log: {e}")
            commits_df = extract_dependency_commits(owner, repo)
        else:
            print("  [DC1] Cloning and extracting commits...")
            commits_df = clone_and_extract_commits(owner, repo)

        if commits_df is None or len(commits_df) == 0:
            print(f"  No dependency change commits found for {owner}/{repo}")
            if not skip_cleanup:
                cleanup_repository(owner, repo)
            return True

        # Step 2: CIデータ取得 (DC3 を先に実行)
        print("  [DC3] Fetching CI data...")
        ci_df = fetch_ci_data_for_commits(owner, repo)

        if ci_df is None or len(ci_df) == 0:
            print(f"  No CI data retrieved for {owner}/{repo}")

        # Step 3: 全モデル実行 — CIデータが取得できたコミットのみ (DC2)
        print("  [DC2] Running all models (depcheck, knip, llama, qwen) for CI-available commits...")
        dep_df = run_all_models_for_commits(owner, repo)

        if dep_df is None or len(dep_df) == 0:
            print(f"  DC2 returned no results for {owner}/{repo}")

        # Step 4: クリーンアップ
        if not skip_cleanup:
            print("  [Cleanup] Removing cloned repository...")
            cleanup_repository(owner, repo)

        print(f"  Completed: {owner}/{repo}")
        return True

    except KeyboardInterrupt:
        print(f"\n  Interrupted while processing {owner}/{repo}")
        if not skip_cleanup:
            cleanup_repository(owner, repo)
        raise

    except Exception as e:
        print(f"  Error processing {owner}/{repo}: {e}")
        if not skip_cleanup:
            cleanup_repository(owner, repo)
        return False


def run_pipeline(
    limit: int = None,
    skip_clone: bool = False,
    skip_cleanup: bool = False,
    start_from: int = 0,
    csv_path: str = None,
):
    print("=" * 60)
    print("CI Waste Analysis Pipeline (depcheck / knip / llama / qwen)")
    print("=" * 60)

    config.ensure_directories()

    projects = get_project_list(limit, csv_path)
    total = len(projects)

    print(f"Total projects to process: {total}")
    print(f"Starting from index: {start_from}")
    print()

    success_count = 0
    error_count = 0

    for i, (owner, repo) in enumerate(projects):
        if i < start_from:
            continue

        print(f"\n[{i+1}/{total}] Processing {owner}/{repo}")

        try:
            if process_single_repository(owner, repo, skip_clone, skip_cleanup):
                success_count += 1
            else:
                error_count += 1
        except KeyboardInterrupt:
            print("\n\nPipeline interrupted by user.")
            print(f"Completed: {success_count}, Errors: {error_count}")
            print(f"Resume from index {i} with --start-from {i}")
            sys.exit(1)

        time.sleep(1)

    print("\n" + "=" * 60)
    print("Pipeline completed!")
    print(f"Success: {success_count}, Errors: {error_count}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(
        description="CI Waste Analysis Pipeline for npm projects"
    )
    parser.add_argument(
        "--repo-list",
        type=str,
        default=None,
        help="Input CSV path (e.g. /path/to/ps4_results_1.csv or /path/to/ps5_filtered.csv)",
    )
    parser.add_argument(
        "--limit", "-l",
        type=int,
        default=config.MAX_PROJECTS,
        help=f"Number of projects to process (default: all)",
    )
    parser.add_argument(
        "--skip-clone",
        action="store_true",
        help="Skip cloning (use existing clones)",
    )
    parser.add_argument(
        "--skip-cleanup",
        action="store_true",
        help="Skip cleanup (keep cloned repositories)",
    )
    parser.add_argument(
        "--start-from",
        type=int,
        default=0,
        help="Start from this project index (for resuming)",
    )
    parser.add_argument(
        "--batch-index",
        type=int,
        default=None,
        help="Batch index (0-based). Processes rows [N*100, N*100+99] of the repo list. "
             "Overrides --start-from and sets --limit to 100.",
    )
    parser.add_argument(
        "--final-only",
        action="store_true",
        help="Only create final dataset from existing data",
    )
    parser.add_argument(
        "--ci-only",
        action="store_true",
        help="Re-run only DC3 (CI data fetch) and recreate final dataset",
    )

    args = parser.parse_args()

    # --batch-index が指定された場合は start-from と limit を上書き
    if args.batch_index is not None:
        args.start_from = args.batch_index * 100
        args.limit = (args.batch_index + 1) * 100

    # --repo-list が指定されていれば config に反映
    csv_path = args.repo_list or config.PROJECT_LIST_PATH
    if csv_path:
        config.PROJECT_LIST_PATH = csv_path
        if args.batch_index is not None:
            config.set_output_dir(csv_path, batch_index=args.batch_index)
        else:
            config.set_output_dir(csv_path)

    if args.final_only:
        print("Creating final dataset...")
        config.ensure_directories()
        df = create_final_dataset()
        if len(df) > 0:
            _print_stats(df)
    elif args.ci_only:
        print("=" * 60)
        print("CI Data Re-fetch Mode (DC3 only)")
        print("=" * 60)
        config.ensure_directories()
        projects = get_project_list(args.limit, csv_path)
        total = len(projects)
        success_count = error_count = 0

        for i, (owner, repo) in enumerate(projects):
            if i < args.start_from:
                continue
            if not os.path.exists(config.get_one_dep_change_commits_path(owner, repo)):
                continue
            print(f"\n[{i+1}/{total}] [DC3] Fetching CI data for {owner}/{repo}")
            try:
                ci_df = fetch_ci_data_for_commits(owner, repo)
                if ci_df is not None and len(ci_df) > 0:
                    success_count += 1
                else:
                    error_count += 1
            except Exception as e:
                print(f"  Error: {e}")
                error_count += 1
            time.sleep(1)

        print(f"\nCI data fetch completed. Success: {success_count}, Errors: {error_count}")
        print("\nCreating final dataset...")
        df = create_final_dataset()
        if len(df) > 0:
            _print_stats(df)
    else:
        run_pipeline(
            limit=args.limit,
            skip_clone=args.skip_clone,
            skip_cleanup=args.skip_cleanup,
            start_from=args.start_from,
            csv_path=csv_path,
        )

        print("\nCreating final dataset...")
        df = create_final_dataset()
        if len(df) > 0:
            _print_stats(df)


def _print_stats(df):
    print(f"\nDataset statistics:")
    print(f"  Total commits: {len(df)}")
    for model in ["depcheck", "knip", "llama", "qwen"]:
        col = f"{model}_is_skippable"
        if col in df.columns:
            n = int(df[col].astype(str).str.lower().eq("true").sum())
            print(f"  Skippable ({model}): {n} ({n/len(df)*100:.2f}%)")


if __name__ == "__main__":
    main()

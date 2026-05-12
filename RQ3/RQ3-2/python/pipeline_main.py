#!/usr/bin/env python3
# python/pipeline_main.py

import argparse
import os
import sys
import time

LANG_DIR = os.path.dirname(os.path.abspath(__file__))
COMMON_DIR = os.path.normpath(os.path.join(LANG_DIR, "..", "common"))
sys.path.insert(0, COMMON_DIR)
sys.path.insert(0, LANG_DIR)  # config.py と dc1 は言語固有版を優先

import config
from dc1_extract_commits import clone_and_extract_commits, extract_dependency_commits
from dc2_ci_data import fetch_ci_data_for_commits
from dc3_dependency_models import run_all_models_for_commits
from ci_check import create_final_dataset
from git_utils import cleanup_repository, get_git_log
from select_projects import get_project_list


def process_single_repository(owner: str, repo: str, skip_cleanup: bool = False) -> bool:
    print(f"\n{'='*60}")
    print(f"Processing: {owner}/{repo}")
    print(f"{'='*60}")

    try:
        print("  [DC1] Cloning and extracting commits...")
        commits_df = clone_and_extract_commits(owner, repo)

        if commits_df is None or len(commits_df) == 0:
            print(f"  No dependency change commits found for {owner}/{repo}")
            # Remove stale dependency data from prior runs so create_final_dataset
            # doesn't pick up results that no longer correspond to valid DC1 commits.
            for stale_path in [
                config.get_ci_data_path(owner, repo),
                config.get_dependency_data_path(owner, repo),
            ]:
                if os.path.exists(stale_path):
                    os.remove(stale_path)
            if not skip_cleanup:
                cleanup_repository(owner, repo)
            return True

        print("  [DC2] Fetching CI data...")
        ci_df = fetch_ci_data_for_commits(owner, repo)

        if ci_df is None or len(ci_df) == 0:
            print(f"  No CI data retrieved for {owner}/{repo}")

        print("  [DC3] Running LLM models (llama, qwen, deepseek)...")
        dep_df = run_all_models_for_commits(owner, repo)

        if dep_df is None or len(dep_df) == 0:
            print(f"  DC3 returned no results for {owner}/{repo}")

        if not skip_cleanup:
            print("  [Cleanup] Removing cloned repository...")
            cleanup_repository(owner, repo)

        print(f"  Completed: {owner}/{repo}")
        return True

    except KeyboardInterrupt:
        if not skip_cleanup:
            cleanup_repository(owner, repo)
        raise
    except Exception as e:
        print(f"  Error processing {owner}/{repo}: {e}")
        if not skip_cleanup:
            cleanup_repository(owner, repo)
        return False


def run_pipeline(limit: int = None, skip_cleanup: bool = False,
                 start_from: int = 0, csv_path: str = None):
    print("=" * 60)
    print(f"RQ3 CI Waste Pipeline — Python (llama / qwen / deepseek)")
    print("=" * 60)

    config.ensure_directories()
    projects = get_project_list(limit, csv_path)
    total = len(projects)
    print(f"Total projects: {total}  (start from: {start_from})")

    success_count = error_count = 0

    for i, (owner, repo) in enumerate(projects):
        if i < start_from:
            continue
        print(f"\n[{i+1}/{total}] {owner}/{repo}")
        try:
            if process_single_repository(owner, repo, skip_cleanup):
                success_count += 1
            else:
                error_count += 1
        except KeyboardInterrupt:
            print(f"\nInterrupted. Resume with --start-from {i}")
            print(f"Success: {success_count}, Errors: {error_count}")
            sys.exit(1)
        time.sleep(1)

    print("\n" + "=" * 60)
    print(f"Pipeline completed!  Success: {success_count}, Errors: {error_count}")
    print("=" * 60)


def main():
    parser = argparse.ArgumentParser(description="RQ3 CI Waste Pipeline — Python")
    parser.add_argument("--repo-list", type=str, default=None)
    parser.add_argument("--limit", "-l", type=int, default=config.MAX_PROJECTS)
    parser.add_argument("--skip-cleanup", action="store_true")
    parser.add_argument("--start-from", type=int, default=0)
    parser.add_argument("--batch-index", type=int, default=None)
    parser.add_argument("--final-only", action="store_true")
    args = parser.parse_args()

    if args.batch_index is not None:
        args.start_from = args.batch_index * 100
        args.limit = (args.batch_index + 1) * 100

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
        create_final_dataset()
    else:
        run_pipeline(
            limit=args.limit,
            skip_cleanup=args.skip_cleanup,
            start_from=args.start_from,
            csv_path=csv_path,
        )
        print("\nCreating final dataset...")
        create_final_dataset()


if __name__ == "__main__":
    main()

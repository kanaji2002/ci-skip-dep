#!/usr/bin/env python3
"""Delete stale CI/dependency data files where DC1 now produces 0 commits."""
import os
import json
import sys

LANG_DIR = os.path.dirname(os.path.abspath(__file__))
COMMON_DIR = os.path.normpath(os.path.join(LANG_DIR, "..", "common"))
sys.path.insert(0, COMMON_DIR)
sys.path.insert(0, LANG_DIR)

import config

def is_empty_json(path):
    try:
        with open(path) as f:
            d = json.load(f)
        # {"sha": {}, ...} is empty
        if isinstance(d, dict):
            return all(v == {} or v == [] for v in d.values())
        return len(d) == 0
    except Exception:
        return False

def main():
    dc1_dir = config.PATHS["one_dependency_version_change_commits"]
    ci_dir = config.PATHS["ci_data"]
    dep_dir = config.PATHS["dependency_data"]

    removed = 0
    for fname in os.listdir(dc1_dir):
        if not fname.startswith("commits_") or not fname.endswith(".json"):
            continue
        dc1_path = os.path.join(dc1_dir, fname)
        if not is_empty_json(dc1_path):
            continue
        # DC1 is empty → delete CI data and dependency data
        # fname: commits_{owner}_{repo}.json → {owner}_{repo}
        stem = fname[len("commits_"):-len(".json")]
        ci_path = os.path.join(ci_dir, f"{stem}_ci_data.json")
        dep_path = os.path.join(dep_dir, f"{stem}_dependency_data.json")
        for p in [ci_path, dep_path]:
            if os.path.exists(p):
                os.remove(p)
                print(f"  Removed stale: {p}")
                removed += 1
    print(f"Done. Removed {removed} stale file(s).")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-list", required=True)
    parser.add_argument("--batch-index", type=int, default=None)
    args = parser.parse_args()
    config.set_output_dir(args.repo_list, args.batch_index)
    main()

# common/dc2_ci_data.py
"""DC2: CIデータ取得 (DC1出力を入力とし、DC3より先に実行)"""

import os
import json
import pandas as pd
from typing import Optional

import config
from github_api import get_check_runs_for_commit


def fetch_ci_data_for_commits(owner: str, repo: str, check_previous: bool = False) -> Optional[pd.DataFrame]:
    commits_path = config.get_one_dep_change_commits_path(owner, repo)
    output_path = config.get_ci_data_path(owner, repo)
    error_path = os.path.join(config.PATHS["ci_data_missing_files"], f"{owner}_{repo}_ci_data.json")

    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.makedirs(config.PATHS["ci_data_missing_files"], exist_ok=True)

    if check_previous and os.path.exists(output_path):
        print(f"  CI data already exists for {owner}/{repo}")
        return pd.read_json(output_path)

    if not os.path.exists(commits_path):
        error_msg = {
            "error": "commits file not found",
            "expected_path": commits_path,
            "owner": owner,
            "repo": repo
        }
        print(f"  Warning: commits file not found for {owner}/{repo}")
        with open(error_path, 'w') as f:
            json.dump(error_msg, f, indent=2)
        return None

    try:
        data = pd.read_json(commits_path)
    except Exception as e:
        error_msg = {"error": str(e), "owner": owner, "repo": repo}
        print(f"  Error reading commits for {owner}/{repo}: {e}")
        with open(error_path, 'w') as f:
            json.dump(error_msg, f, indent=2)
        return None

    if len(data) == 0:
        print(f"  No commits in dependency_data for {owner}/{repo}")
        return None

    # upgraded_dep: DC1 が直接格納している言語では再抽出不要
    if 'upgraded_dep' not in data.columns:
        # JS互換フォールバック: insertions から抽出
        try:
            data['upgraded_dep'] = data['insertions'].map(
                lambda x: x[0].replace('"', '').split(':')[0].strip()
                if len(x) > 0 and isinstance(x[0], str) else ""
            )
        except Exception as e:
            print(f"  Error extracting upgraded_dep for {owner}/{repo}: {e}")
            return None

    data['upgraded_dep_remove'] = data['upgraded_dep'].map(
        lambda token: token in config.NOT_PACKAGES or not token
    )
    data = data[~data['upgraded_dep_remove']]

    if len(data) == 0:
        print(f"  No valid dependencies after filtering for {owner}/{repo}")
        return None

    print(f"  Fetching CI data for {len(data)} commits...")

    ci_data_list = []
    parent_ci_data_list = []
    total_commits = len(data)

    for i, (idx, row) in enumerate(data.iterrows()):
        sha = row['sha']
        parent_sha = row['parent_sha']

        if i % 10 == 0 or i == total_commits - 1:
            print(f"    [{i+1}/{total_commits}] Processing {sha[:7]}...")

        ci_data_list.append(get_check_runs_for_commit(owner, repo, sha))
        parent_ci_data_list.append(get_check_runs_for_commit(owner, repo, parent_sha))

    data['ci_data'] = ci_data_list
    data['parent_ci_data'] = parent_ci_data_list

    try:
        data.to_json(output_path)
        print(f"  Saved CI data for {owner}/{repo}")
    except Exception as e:
        print(f"  Error saving CI data for {owner}/{repo}: {e}")
        return None

    return data

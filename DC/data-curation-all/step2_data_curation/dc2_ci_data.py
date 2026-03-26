# step2_data_curation/dc2_ci_data.py
"""DC2: CIデータ取得 (DC1出力を入力とし、DC3より先に実行)"""

import os
import json
import pandas as pd
from typing import Optional
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from utils.github_api import get_check_runs_for_commit


def fetch_ci_data_for_commits(owner: str, repo: str, check_previous: bool = False) -> Optional[pd.DataFrame]:
    """
    全コミットのCIデータを取得 (DC1出力を入力とする)

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名
        check_previous: 以前のダウンロードをチェックするか

    Returns:
        CIデータを含むDataFrame
    """
    commits_path = config.get_one_dep_change_commits_path(owner, repo)
    output_path = config.get_ci_data_path(owner, repo)
    error_path = os.path.join(config.PATHS["ci_data_missing_files"], f"{owner}_{repo}_ci_data.json")

    # 出力ディレクトリを確保
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    os.makedirs(config.PATHS["ci_data_missing_files"], exist_ok=True)

    # 以前のダウンロードをチェック
    if check_previous and os.path.exists(output_path):
        print(f"  CI data already exists for {owner}/{repo}")
        return pd.read_json(output_path)

    # DC1コミットデータを確認
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

    # DC1コミットデータを読み込み
    try:
        data = pd.read_json(commits_path)
    except Exception as e:
        error_msg = {
            "error": "Error reading commits file",
            "error_message": str(e),
            "owner": owner,
            "repo": repo
        }
        print(f"  Error reading commits for {owner}/{repo}: {e}")
        with open(error_path, 'w') as f:
            json.dump(error_msg, f, indent=2)
        return None

    if len(data) == 0:
        print(f"  No commits in dependency_data for {owner}/{repo}")
        return None

    # upgraded_depを抽出
    try:
        data['upgraded_dep'] = data['insertions'].map(
            lambda x: x[0].replace('"', '').split(':')[0] if len(x) > 0 and isinstance(x[0], str) else ""
        )
    except Exception as e:
        print(f"  Error extracting upgraded_dep for {owner}/{repo}: {e}")
        return None

    # 不要なパッケージをフィルタリング
    data['upgraded_dep_remove'] = data['upgraded_dep'].map(lambda token: token in config.NOT_PACKAGES)
    data = data[~data['upgraded_dep_remove']]

    if len(data) == 0:
        print(f"  No valid dependencies after filtering for {owner}/{repo}")
        return None

    print(f"  Fetching CI data for {len(data)} commits...")

    # 各コミットのCIデータを取得
    ci_data_list = []
    parent_ci_data_list = []
    total_commits = len(data)

    for i, (idx, row) in enumerate(data.iterrows()):
        sha = row['sha']
        parent_sha = row['parent_sha']

        # 進捗表示（10件ごと、または最初と最後）
        if i % 10 == 0 or i == total_commits - 1:
            print(f"    [{i+1}/{total_commits}] Processing {sha[:7]}...")

        # コミットのCI
        ci_result = get_check_runs_for_commit(owner, repo, sha)
        ci_data_list.append(ci_result)

        # 親コミットのCI
        parent_ci_result = get_check_runs_for_commit(owner, repo, parent_sha)
        parent_ci_data_list.append(parent_ci_result)

    data['ci_data'] = ci_data_list
    data['parent_ci_data'] = parent_ci_data_list

    # 保存
    try:
        data.to_json(output_path)
        print(f"  Saved CI data for {owner}/{repo}")
    except Exception as e:
        print(f"  Error saving CI data for {owner}/{repo}: {e}")
        return None

    return data


if __name__ == "__main__":
    # テスト実行
    import sys
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        df = fetch_ci_data_for_commits(owner, repo)
        if df is not None:
            print(df[['sha', 'upgraded_dep']].head())

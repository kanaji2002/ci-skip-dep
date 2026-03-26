# step1_project_selection/select_projects.py
"""プロジェクト選定（CSVから読み込み）"""

import pandas as pd
from typing import List, Tuple
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def load_projects(limit: int = None, csv_path: str = None) -> pd.DataFrame:
    """
    プロジェクトリストをCSVから読み込み

    Args:
        limit: 読み込むプロジェクト数の上限
        csv_path: CSVファイルパス（Noneの場合は config.PROJECT_LIST_PATH）

    Returns:
        プロジェクトのDataFrame
    """
    if limit is None:
        limit = config.MAX_PROJECTS

    path = csv_path or config.PROJECT_LIST_PATH
    if path is None:
        raise ValueError("No project list path specified. Use --repo-list or set REPO_LIST_PATH.")

    # フィルタリング済みCSVが存在すればそちらを優先
    filtered_path = config.get_filtered_project_list_path(path)
    if os.path.exists(filtered_path):
        print(f"  Using filtered project list: {filtered_path}")
        projects = pd.read_csv(filtered_path)
    else:
        print(f"  Using project list: {path}")
        projects = pd.read_csv(path)

    return projects[:limit]


def get_project_list(limit: int = None, csv_path: str = None) -> List[Tuple[str, str]]:
    """
    (owner, repo)のタプルリストを取得

    Args:
        limit: 読み込むプロジェクト数の上限
        csv_path: CSVファイルパス

    Returns:
        [(owner, repo), ...] のリスト
    """
    projects = load_projects(limit, csv_path)
    project_names = projects['name'].unique().tolist()

    result = []
    for name in project_names:
        if name in config.SKIP_REPOS:
            print(f"  Skipping {name} (in skip list)")
            continue

        parts = name.split("/")
        if len(parts) == 2:
            owner, repo = parts
            result.append((owner, repo))
        else:
            print(f"  Invalid project name format: {name}")

    return result


if __name__ == "__main__":
    projects = get_project_list(limit=5)
    print(f"Loaded {len(projects)} projects:")
    for owner, repo in projects:
        print(f"  - {owner}/{repo}")

# step2_data_curation/dc1_extract_commits.py
"""DC1: 依存関係変更コミット抽出"""

import os
import re
import difflib
import json
import pandas as pd
from typing import List, Dict, Tuple, Any
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from utils.git_utils import clone_repository, get_git_log


def extract_commits_with_data(loginfo: str) -> Tuple[List, List, List]:
    """
    git logから依存関係変更コミットを抽出

    Args:
        loginfo: git logの出力

    Returns:
        (コミットデータリスト, 追加行リスト, 削除行リスト)
    """
    li_commit_data = []
    li_insertions = []
    li_deletions = []

    for line in loginfo.split("<start>"):
        try:
            sha = line.split(",")[0]
            parent_commit = line.split(",")[1]
            author = line.split(",")[2]
            email = line.split(",")[3]
            date = line.split(",")[4].split("<end>")[0]

            status = False
            has_other_files = False
            insertions = []
            deletions = []
            b_lines = line.split("<end>")[1].split("\n")

            for l in b_lines:
                # 新しいファイルの差分ヘッダを検出
                if re.search(r"^---\s", l):
                    if re.search(r"---.*package\.json$", l):
                        # package.json の差分 → 収集対象
                        status = True
                    elif re.search(r"---.*package-lock\.json$", l):
                        # package-lock.json の差分 → スキップ（許容するが収集しない）
                        status = False
                    else:
                        # その他のファイルが変更されている → 除外対象
                        status = False
                        has_other_files = True
                    continue
                if status:
                    if re.search(r"^\+[^+].*$", l):
                        insertions.append(l[1:-1].strip())
                    elif re.search(r"^\-[^-].*$", l):
                        deletions.append(l[1:-1].strip())

            # ソースコード等の変更を含むコミットは除外
            if has_other_files:
                continue

            # 単一依存関係変更のみを抽出
            # 条件: 1つの追加と1つの削除のみ
            if len(insertions) == 1 and len(deletions) == 1:
                # バージョン差分が7文字以内（例: "1.0.0" → "1.0.1"）
                output_list = [li for li in difflib.ndiff(insertions[0], deletions[0]) if li[0] != ' ']
                new_list = []
                for o in output_list:
                    new_list += re.findall(r'\d+', o)
                if len(new_list) / 2 <= 7:
                    li_commit_data.append([sha, parent_commit, author, email, date])
                    li_insertions.append(insertions)
                    li_deletions.append(deletions)

        except Exception as e:
            continue

    return li_commit_data, li_insertions, li_deletions


def extract_dependency_commits(owner: str, repo: str) -> pd.DataFrame:
    """
    単一依存関係変更コミットを抽出してJSONに保存

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        抽出されたコミットのDataFrame
    """
    dump_path = config.get_commits_dump_path(owner, repo)
    output_path = config.get_one_dep_change_commits_path(owner, repo)

    # 出力ディレクトリを確保
    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    # ダンプファイルが存在しない場合は空のDataFrameを返す
    if not os.path.exists(dump_path):
        df = pd.DataFrame(columns=['sha', 'parent_sha', 'author', 'email', 'datetime', 'insertions', 'deletions'])
        df.to_json(output_path)
        return df

    with open(dump_path, encoding='utf-8', errors='ignore') as f:
        loginfo = f.read()

    li_commit_data, li_insertions, li_deletions = extract_commits_with_data(loginfo)

    df = pd.DataFrame()

    if len(li_commit_data) == 0:
        df = pd.DataFrame(columns=['sha', 'parent_sha', 'author', 'email', 'datetime', 'insertions', 'deletions'])
        df.to_json(output_path)
        return df

    df['commit_data'] = li_commit_data
    df['insertions'] = li_insertions
    df['deletions'] = li_deletions

    df['sha'] = df['commit_data'].map(lambda x: x[0])
    df['parent_sha'] = df['commit_data'].map(lambda x: x[1])
    df['author'] = df['commit_data'].map(lambda x: x[2])
    df['email'] = df['commit_data'].map(lambda x: x[3])
    df['datetime'] = df['commit_data'].map(lambda x: x[4])

    df = df[['sha', 'parent_sha', 'author', 'email', 'datetime', 'insertions', 'deletions']]

    df.to_json(output_path)
    print(f"  Extracted {len(df)} dependency change commits")

    return df


def clone_and_extract_commits(owner: str, repo: str) -> pd.DataFrame:
    """
    リポジトリをクローンし、git logを取得し、コミットを抽出

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        抽出されたコミットのDataFrame
    """
    # クローン
    success, message = clone_repository(owner, repo)
    if not success:
        print(f"  Failed to clone {owner}/{repo}: {message}")
        return pd.DataFrame()

    # git log取得
    try:
        get_git_log(owner, repo)
    except Exception as e:
        print(f"  Failed to get git log for {owner}/{repo}: {e}")
        return pd.DataFrame()

    # コミット抽出
    return extract_dependency_commits(owner, repo)


if __name__ == "__main__":
    # テスト実行
    import sys
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        df = clone_and_extract_commits(owner, repo)
        print(df)

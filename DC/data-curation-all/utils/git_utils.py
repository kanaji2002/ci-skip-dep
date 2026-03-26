# utils/git_utils.py
"""Git操作ユーティリティ"""

import os
import shutil
import git
from git import Repo
from typing import Tuple
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def clone_repository(owner: str, repo: str) -> Tuple[bool, str]:
    """
    リポジトリをクローン

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        (成功フラグ, パスまたはエラーメッセージ)
    """
    gh_url = f"https://github.com/{owner}/{repo}"
    clone_path = config.get_clone_path(owner, repo)

    # 既存のクローンがあれば削除
    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)

    try:
        print(f"  Cloning {owner}/{repo}...")
        Repo.clone_from(gh_url, clone_path)
        return True, clone_path
    except Exception as e:
        return False, str(e)


def get_repo(path: str) -> Repo:
    """リポジトリオブジェクトを取得"""
    return Repo(path)


def get_git_log(owner: str, repo: str) -> str:
    """
    git logを取得し、ダンプファイルに保存

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        git log出力
    """
    clone_path = config.get_clone_path(owner, repo)
    dump_path = config.get_commits_dump_path(owner, repo)

    g = git.Git(clone_path)

    # 日付範囲とフォーマットを指定してgit logを取得
    loginfo = g.log(
        f'--since={config.DATE_SINCE}',
        f'--until={config.DATE_UNTIL}',
        '--pretty=format:<start>%H,%P,%an,%ae,%ad<end>',
        '--numstat',
        '--stat',
        '-p'
    )

    # ダンプファイルに保存
    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, 'w', encoding='utf-8', errors='ignore') as f:
        try:
            f.write(loginfo)
        except:
            f.write(loginfo.encode('utf-8', 'ignore').decode('utf-8'))

    return loginfo


def checkout_commit(repo_path: str, sha: str) -> bool:
    """
    指定したコミットにチェックアウト

    Args:
        repo_path: リポジトリパス
        sha: コミットSHA

    Returns:
        成功フラグ
    """
    try:
        r = get_repo(repo_path)
        try:
            r.git.checkout(sha)
        except:
            r.git.stash()
            r.git.checkout(sha)
            try:
                r.git.stash('apply')
            except:
                pass  # stashが空の場合は無視
        return True
    except Exception as e:
        print(f"  Checkout failed for {sha[:7]}: {e}")
        return False


def cleanup_repository(owner: str, repo: str) -> bool:
    """
    クローンしたリポジトリを削除

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        成功フラグ
    """
    clone_path = config.get_clone_path(owner, repo)

    if os.path.exists(clone_path):
        try:
            shutil.rmtree(clone_path)
            print(f"  Cleaned up: {clone_path}")
            return True
        except Exception as e:
            print(f"  Failed to cleanup {clone_path}: {e}")
            return False
    return True

# common/git_utils.py
"""Git操作ユーティリティ (共通)"""

import os
import shutil
import git
from git import Repo
from typing import Tuple

import config


def clone_repository(owner: str, repo: str) -> Tuple[bool, str]:
    gh_url = f"https://github.com/{owner}/{repo}"
    clone_path = config.get_clone_path(owner, repo)

    if os.path.exists(clone_path):
        shutil.rmtree(clone_path)

    try:
        print(f"  Cloning {owner}/{repo}...")
        Repo.clone_from(gh_url, clone_path)
        return True, clone_path
    except Exception as e:
        return False, str(e)


def get_repo(path: str) -> Repo:
    return Repo(path)


def get_git_log(owner: str, repo: str) -> str:
    clone_path = config.get_clone_path(owner, repo)
    dump_path = config.get_commits_dump_path(owner, repo)

    g = git.Git(clone_path)
    loginfo = g.log(
        f'--since={config.DATE_SINCE}',
        f'--until={config.DATE_UNTIL}',
        '--pretty=format:<start>%H,%P,%an,%ae,%ad<end>',
        '--numstat',
        '--stat',
        '-p'
    )

    os.makedirs(os.path.dirname(dump_path), exist_ok=True)
    with open(dump_path, 'w', encoding='utf-8', errors='ignore') as f:
        try:
            f.write(loginfo)
        except Exception:
            f.write(loginfo.encode('utf-8', 'ignore').decode('utf-8'))

    return loginfo


def checkout_commit(repo_path: str, sha: str) -> bool:
    try:
        r = get_repo(repo_path)
        try:
            r.git.checkout(sha)
        except Exception:
            r.git.stash()
            r.git.checkout(sha)
            try:
                r.git.stash('apply')
            except Exception:
                pass
        return True
    except Exception as e:
        print(f"  Checkout failed for {sha[:7]}: {e}")
        return False


def cleanup_repository(owner: str, repo: str) -> bool:
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

# utils/github_api.py
"""GitHub API操作ユーティリティ"""

import requests
import time
import random
import json
from typing import Optional, List, Tuple, Any
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config


def get_tokens() -> List[str]:
    """GitHubトークンリストを取得"""
    return config.GITHUB_TOKENS


def get_random_token() -> str:
    """ランダムにトークンを選択"""
    tokens = get_tokens()
    return random.choice(tokens)


def get_check_runs_for_commit(
    owner: str,
    repo: str,
    sha: str
) -> Tuple[Optional[List[Any]], int]:
    """
    指定コミットのCheck Runsを取得

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名
        sha: コミットSHA

    Returns:
        (check_runsリスト, ステータスコード)
    """
    max_retries = config.API_RETRY_MAX
    retry_delay = config.API_RETRY_DELAY

    i = 0
    response = None

    while i < max_retries:
        try:
            token = get_random_token()
            url = f"https://api.github.com/repos/{owner}/{repo}/commits/{sha}/check-runs"
            headers = {
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "X-GitHub-Api-Version": "2022-11-28"
            }

            response = requests.get(url, headers=headers, timeout=config.API_TIMEOUT)
            data = None

            if response.status_code == 200:
                try:
                    build_info = response.json()
                    data = build_info.get('check_runs', [])
                    return data, response.status_code
                except (json.JSONDecodeError, KeyError) as e:
                    print(f"  Warning: Failed to parse JSON for {owner}/{repo}/{sha[:7]}: {e}")
                    return None, response.status_code

            elif response.status_code == 429:  # Rate limit
                retry_after = int(response.headers.get('Retry-After', 60))
                print(f"  Rate limit exceeded. Waiting {retry_after} seconds...")
                time.sleep(retry_after)
                i += 1
                continue

            elif response.status_code in [401, 403]:  # Auth errors - try different token
                i += 1
                if i < max_retries:
                    time.sleep(retry_delay)
                    continue
                else:
                    return None, response.status_code

            elif response.status_code == 404:  # Not found
                return None, response.status_code

            else:
                return None, response.status_code

        except requests.exceptions.Timeout:
            print(f"  Timeout for {owner}/{repo}/{sha[:7]}. Retrying...")
            i += 1
            if i < max_retries:
                time.sleep(retry_delay * i)
            else:
                return None, 0

        except requests.exceptions.ConnectionError:
            print(f"  Connection error for {owner}/{repo}/{sha[:7]}. Retrying...")
            i += 1
            if i < max_retries:
                time.sleep(retry_delay * i)
            else:
                return None, 0

        except Exception as e:
            print(f"  Unexpected error for {owner}/{repo}/{sha[:7]}: {e}")
            return None, 0

        i += 1

    return None, response.status_code if response else 0

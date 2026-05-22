# python/dc1_extract_commits.py
"""DC1: pyproject.toml の依存関係変更コミット抽出"""

import os
import re
import difflib
import json
import pandas as pd
from typing import List, Dict, Tuple, Any, Optional

import config
from git_utils import clone_repository, get_git_log

# pyproject.toml の変更のみ許容するファイル
_LOCK_FILES = re.compile(r"---.*(?:poetry\.lock|uv\.lock|pdm\.lock|pip\.lock)$")
_TARGET_FILE = re.compile(r"---.*pyproject\.toml$")

# TOML key=value 形式を使う依存関係セクション
_DEP_SECTION_KEYVAL = frozenset({
    "tool.poetry.dependencies",
    "tool.poetry.dev-dependencies",
    "tool.pdm.dev-dependencies",
    "tool.uv.dev-dependencies",
})


def _is_poetry_group_dep_section(section: str) -> bool:
    return bool(re.match(r'^tool\.poetry\.group\.[^.]+\.dependencies$', section))


def _extract_dep_name(line: str, current_section: Optional[str] = None) -> str:
    """pyproject.toml の差分行からパッケージ名を抽出する。

    - TOML key=value 形式 (requests = "^2.28.0") は Poetry / PDM / uv の
      dep セクション内のみ対象とし、[tool.ruff] 等の設定キーを除外する。
    - PEP 508 文字列形式 ("requests>=2.28.0") はセクション不問で認識する。
    """
    line = line.strip().rstrip(',')
    if not line or line.startswith('#') or line.startswith('['):
        return ""

    # TOML key = value 形式: Poetry / PDM / uv の dep セクションのみ
    in_keyval_section = (
        current_section in _DEP_SECTION_KEYVAL
        or (current_section is not None and _is_poetry_group_dep_section(current_section))
    )
    if in_keyval_section and '=' in line and not line.lstrip().startswith('"') and not line.lstrip().startswith("'"):
        name = line.split('=')[0].strip()
        value = line.split('=', 1)[1].strip().strip(',')
        # リスト値はグループ定義や設定リスト → パッケージ名ではない
        if value.startswith('['):
            return ""
        if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$', name):
            return name

    # PEP 508 文字列形式 (PEP 621 配列要素・全セクション対象)
    # 例: "requests>=2.28.0" / requests>=2.28.0
    stripped = line.strip('"\'')
    for sep in ['>=', '<=', '~=', '==', '!=', '>', '<', '[', '@', ';']:
        idx = stripped.find(sep)
        if idx > 0:
            name = stripped[:idx].strip()
            if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-\.]*$', name):
                return name

    return ""


def extract_commits_with_data(loginfo: str) -> Tuple[List, List, List, List]:
    li_commit_data = []
    li_insertions = []
    li_deletions = []
    li_upgraded_dep = []

    for line in loginfo.split("<start>"):
        try:
            sha = line.split(",")[0]
            parent_commit = line.split(",")[1]
            author = line.split(",")[2]
            email = line.split(",")[3]
            date = line.split(",")[4].split("<end>")[0]

            status = False
            has_other_files = False
            # (diff_line, section_at_that_line) のタプルで収集
            insertions: List[Tuple[str, Optional[str]]] = []
            deletions: List[Tuple[str, Optional[str]]] = []
            b_lines = line.split("<end>")[1].split("\n")

            current_section: Optional[str] = None

            for l in b_lines:
                if re.search(r"^---\s", l):
                    current_section = None  # ファイルが変わったらセクションをリセット
                    if _TARGET_FILE.search(l):
                        status = True
                    elif _LOCK_FILES.search(l):
                        status = False
                    else:
                        status = False
                        has_other_files = True
                    continue

                # TOML セクションヘッダを追跡 (コンテキスト行・追加行・削除行すべて対象)
                # [[array.of.tables]] は除外し [section.name] のみ対象
                stripped_l = l.lstrip(' +-')
                if not stripped_l.startswith('[['):
                    section_m = re.match(r'^\[([a-zA-Z][a-zA-Z0-9_\-\.]*)\]\s*$', stripped_l)
                    if section_m:
                        current_section = section_m.group(1).strip()

                if status:
                    if re.search(r"^\+[^+].*$", l):
                        insertions.append((l[1:].strip(), current_section))
                    elif re.search(r"^\-[^-].*$", l):
                        deletions.append((l[1:].strip(), current_section))

            if has_other_files:
                continue

            if len(insertions) == 1 and len(deletions) == 1:
                ins_line, ins_section = insertions[0]
                del_line, del_section = deletions[0]

                output_list = [li for li in difflib.ndiff(ins_line, del_line) if li[0] != ' ']
                new_list = []
                for o in output_list:
                    new_list += re.findall(r'\d+', o)
                if len(new_list) / 2 <= 7:
                    dep_name = _extract_dep_name(ins_line, ins_section)
                    if not dep_name:
                        dep_name = _extract_dep_name(del_line, del_section)
                    if not dep_name:
                        continue  # パッケージ名を特定できないコミットは除外
                    li_commit_data.append([sha, parent_commit, author, email, date])
                    li_insertions.append([ins_line])
                    li_deletions.append([del_line])
                    li_upgraded_dep.append(dep_name)

        except Exception:
            continue

    return li_commit_data, li_insertions, li_deletions, li_upgraded_dep


def extract_dependency_commits(owner: str, repo: str) -> pd.DataFrame:
    dump_path = config.get_commits_dump_path(owner, repo)
    output_path = config.get_one_dep_change_commits_path(owner, repo)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    empty = pd.DataFrame(columns=['sha', 'parent_sha', 'author', 'email', 'datetime',
                                   'insertions', 'deletions', 'upgraded_dep'])

    if not os.path.exists(dump_path):
        empty.to_json(output_path)
        return empty

    with open(dump_path, encoding='utf-8', errors='ignore') as f:
        loginfo = f.read()

    li_commit_data, li_insertions, li_deletions, li_upgraded_dep = extract_commits_with_data(loginfo)

    if not li_commit_data:
        empty.to_json(output_path)
        return empty

    df = pd.DataFrame()
    df['commit_data'] = li_commit_data
    df['insertions'] = li_insertions
    df['deletions'] = li_deletions
    df['upgraded_dep'] = li_upgraded_dep

    df['sha'] = df['commit_data'].map(lambda x: x[0])
    df['parent_sha'] = df['commit_data'].map(lambda x: x[1])
    df['author'] = df['commit_data'].map(lambda x: x[2])
    df['email'] = df['commit_data'].map(lambda x: x[3])
    df['datetime'] = df['commit_data'].map(lambda x: x[4])

    df = df[['sha', 'parent_sha', 'author', 'email', 'datetime', 'insertions', 'deletions', 'upgraded_dep']]
    df.to_json(output_path)
    print(f"  Extracted {len(df)} dependency change commits")
    return df


def clone_and_extract_commits(owner: str, repo: str) -> pd.DataFrame:
    success, message = clone_repository(owner, repo)
    if not success:
        print(f"  Failed to clone {owner}/{repo}: {message}")
        return pd.DataFrame()
    try:
        get_git_log(owner, repo)
    except Exception as e:
        print(f"  Failed to get git log for {owner}/{repo}: {e}")
        return pd.DataFrame()
    return extract_dependency_commits(owner, repo)

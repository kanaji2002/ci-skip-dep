# rust/dc1_extract_commits.py
"""DC1: Cargo.toml の依存関係変更コミット抽出"""

import os
import re
import difflib
import pandas as pd
from typing import List, Optional, Tuple

import config
from git_utils import clone_repository, get_git_log

_TARGET_FILE = re.compile(r"---.*Cargo\.toml$")
_LOCK_FILE   = re.compile(r"---.*Cargo\.lock$")

# Cargo.toml の dep セクション
_DEP_SECTIONS = frozenset({"dependencies", "dev-dependencies", "build-dependencies"})


def _is_dep_section(section: Optional[str]) -> bool:
    if not section:
        return False
    s = section.strip()
    # [dependencies], [dev-dependencies], [build-dependencies]
    if s in _DEP_SECTIONS:
        return True
    # [target.'cfg(...)'.dependencies] 等のターゲット依存セクション
    if re.search(r'\.(dev-)?dependencies$', s):
        return True
    return False


def _extract_dep_name(line: str) -> str:
    """Cargo.toml の差分行からクレート名を抽出する"""
    line = line.strip()
    if not line or line.startswith('#') or line.startswith('['):
        return ""
    # TOML key = value 形式
    # 例: serde = "1.0.197"  /  serde = { version = "1.0.197", features = ["derive"] }
    if '=' in line:
        name = line.split('=')[0].strip()
        if re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]*$', name):
            return name
    return ""


def _extract_version(line: str) -> Optional[str]:
    """Cargo.toml の依存行からバージョン文字列を抽出する。なければ None。
    単純形式: name = "version"
    テーブル形式: name = { version = "...", ... }
    """
    line = line.strip()
    m = re.match(r'^[a-zA-Z0-9][a-zA-Z0-9_\-]* *= *"([^"]+)"', line)
    if m:
        return m.group(1)
    m = re.search(r'\bversion *= *"([^"]+)"', line)
    if m:
        return m.group(1)
    return None


def extract_commits_with_data(loginfo: str) -> Tuple[List, List, List, List]:
    li_commit_data = []
    li_insertions  = []
    li_deletions   = []
    li_upgraded_dep = []

    for line in loginfo.split("<start>"):
        try:
            sha           = line.split(",")[0]
            parent_commit = line.split(",")[1]
            author        = line.split(",")[2]
            email         = line.split(",")[3]
            date          = line.split(",")[4].split("<end>")[0]

            status          = False
            has_other_files = False
            insertions: List[Tuple[str, Optional[str]]] = []
            deletions:  List[Tuple[str, Optional[str]]] = []
            b_lines         = line.split("<end>")[1].split("\n")

            current_section: Optional[str] = None

            for l in b_lines:
                if re.search(r"^---\s", l):
                    current_section = None
                    if _TARGET_FILE.search(l):
                        status = True
                    elif _LOCK_FILE.search(l):
                        status = False
                    else:
                        status = False
                        has_other_files = True
                    continue

                # TOML セクションヘッダを追跡
                stripped_l = l.lstrip(' +-')
                if not stripped_l.startswith('[['):
                    section_m = re.match(r'^\[([^\[\]]+)\]\s*$', stripped_l)
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

                if not _is_dep_section(ins_section) and not _is_dep_section(del_section):
                    continue

                # dep名が両辺で一致することを確認
                ins_dep = _extract_dep_name(ins_line)
                del_dep = _extract_dep_name(del_line)
                if not ins_dep or ins_dep != del_dep:
                    continue

                # バージョンが両辺に存在し、かつ変更されていることを確認
                ins_ver = _extract_version(ins_line)
                del_ver = _extract_version(del_line)
                if not ins_ver or not del_ver or ins_ver == del_ver:
                    continue

                li_commit_data.append([sha, parent_commit, author, email, date])
                li_insertions.append([ins_line])
                li_deletions.append([del_line])
                li_upgraded_dep.append(ins_dep)

        except Exception:
            continue

    return li_commit_data, li_insertions, li_deletions, li_upgraded_dep


def extract_dependency_commits(owner: str, repo: str) -> pd.DataFrame:
    dump_path   = config.get_commits_dump_path(owner, repo)
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
    df['commit_data']  = li_commit_data
    df['insertions']   = li_insertions
    df['deletions']    = li_deletions
    df['upgraded_dep'] = li_upgraded_dep

    df['sha']        = df['commit_data'].map(lambda x: x[0])
    df['parent_sha'] = df['commit_data'].map(lambda x: x[1])
    df['author']     = df['commit_data'].map(lambda x: x[2])
    df['email']      = df['commit_data'].map(lambda x: x[3])
    df['datetime']   = df['commit_data'].map(lambda x: x[4])

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

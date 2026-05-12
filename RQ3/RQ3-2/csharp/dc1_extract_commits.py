# csharp/dc1_extract_commits.py
"""DC1: *.csproj の依存関係変更コミット抽出"""

import os
import re
import difflib
import pandas as pd
from typing import List, Tuple

import config
from git_utils import clone_repository, get_git_log

# .csproj ファイルの差分を対象にする
_TARGET_FILE = re.compile(r"---.*\.csproj$")
_LOCK_FILE   = re.compile(r"---.*packages\.lock\.json$")


def _extract_dep_name(line: str) -> str:
    """csproj の差分行から NuGet パッケージ名を抽出する"""
    # 例: <PackageReference Include="Newtonsoft.Json" Version="13.0.3" />
    m = re.search(r'Include\s*=\s*["\']([^"\']+)["\']', line, re.IGNORECASE)
    if m:
        return m.group(1)
    # 例: <PackageVersion Include="SomePackage" Version="1.0.0" />
    m = re.search(r'<PackageVersion[^>]+Include\s*=\s*["\']([^"\']+)["\']', line, re.IGNORECASE)
    if m:
        return m.group(1)
    return ""


def extract_commits_with_data(loginfo: str) -> Tuple[List, List, List, List]:
    li_commit_data  = []
    li_insertions   = []
    li_deletions    = []
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
            insertions      = []
            deletions       = []
            b_lines         = line.split("<end>")[1].split("\n")

            for l in b_lines:
                if re.search(r"^---\s", l):
                    if _TARGET_FILE.search(l):
                        status = True
                    elif _LOCK_FILE.search(l):
                        status = False
                    else:
                        status = False
                        has_other_files = True
                    continue
                if status:
                    if re.search(r"^\+[^+].*$", l):
                        insertions.append(l[1:].strip())
                    elif re.search(r"^\-[^-].*$", l):
                        deletions.append(l[1:].strip())

            if has_other_files:
                continue

            if len(insertions) == 1 and len(deletions) == 1:
                output_list = [li for li in difflib.ndiff(insertions[0], deletions[0]) if li[0] != ' ']
                new_list = []
                for o in output_list:
                    new_list += re.findall(r'\d+', o)
                if len(new_list) / 2 <= 7:
                    dep_name = _extract_dep_name(insertions[0]) or _extract_dep_name(deletions[0])
                    if not dep_name:
                        continue
                    li_commit_data.append([sha, parent_commit, author, email, date])
                    li_insertions.append(insertions)
                    li_deletions.append(deletions)
                    li_upgraded_dep.append(dep_name)

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

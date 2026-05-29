"""
Step 1: 未使用依存検知 (Python)
PS8 CSV に記載の各リポジトリを clone し、3つのLLMで未使用依存を検知する。
出力: output/step1_results.csv

使い方:
    python3 run.py
    python3 run.py --limit 5       # テスト用
    python3 run.py --skip 10       # 10件スキップして再開
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv
from git import Repo

# .env 読み込み
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))))
load_dotenv(os.path.join(_ROOT, ".env"))

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
LANG_DIR    = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR  = os.path.join(LANG_DIR, "output")
CLONES_DIR  = os.path.join(OUTPUT_DIR, "clones")
RESULTS_CSV = os.path.join(OUTPUT_DIR, "step1_results.csv")

PS8_CSV = os.path.join(_ROOT, "PS", "python", "ps8", "ps8_filtered.csv")
BATCH_SIZE = 100

# RQ3 共通プロンプト (path指定でアクセス)
PROMPTS_BASE = "/work/rintaro-k/research/RQ3/RQ3-2/common/prompts"

# ---------------------------------------------------------------------------
# Ollama 設定
# ---------------------------------------------------------------------------
OLLAMA_URL            = "http://localhost:11434"
OLLAMA_MODEL_LLAMA    = "llama3.1:8b"
OLLAMA_MODEL_QWEN     = "qwen3.5:4b"
OLLAMA_MODEL_DEEPSEEK = "deepseek-coder:6.7b-instruct"

LLM_TIMEOUT = 300

LANGUAGE = "python"

# ---------------------------------------------------------------------------
# TOML ローダー (Python 3.10 対応)
# ---------------------------------------------------------------------------

def _load_toml(path: str) -> Dict:
    try:
        if sys.version_info >= (3, 11):
            import tomllib
            with open(path, 'rb') as f:
                return tomllib.load(f)
    except ImportError:
        pass
    try:
        import tomli
        with open(path, 'rb') as f:
            return tomli.load(f)
    except ImportError:
        pass
    try:
        import toml
        with open(path, 'r', encoding='utf-8') as f:
            return toml.load(f)
    except ImportError:
        pass
    raise RuntimeError("No TOML library found. Install tomli: pip install tomli")


# ---------------------------------------------------------------------------
# pyproject.toml パース
# ---------------------------------------------------------------------------

def _parse_pep508(dep_str: str) -> Tuple[str, str]:
    dep_str = dep_str.strip().strip('"\'')
    for sep in ['>=', '<=', '~=', '==', '!=', '>', '<', '[', '@', ';']:
        idx = dep_str.find(sep)
        if idx > 0:
            name = dep_str[:idx].strip()
            ver = dep_str[idx:].split(';')[0].strip()
            return name, ver
    return dep_str.split(';')[0].strip(), '*'


def parse_pyproject_toml(repo_path: str) -> Dict:
    path = os.path.join(repo_path, "pyproject.toml")
    if not os.path.exists(path):
        return {}
    try:
        raw = _load_toml(path)
    except Exception as e:
        print(f"  [warn] parse pyproject.toml: {e}")
        return {}

    tool    = raw.get('tool', {})
    poetry  = tool.get('poetry', {})
    project = raw.get('project', {})

    deps: Dict[str, str] = {}
    dev_deps: Dict[str, str] = {}
    extra_deps: Dict[str, str] = {}

    # Poetry: [tool.poetry.dependencies]
    for name, ver in poetry.get('dependencies', {}).items():
        if name == 'python':
            continue
        deps[name] = ver if isinstance(ver, str) else str(ver)

    # Poetry: [tool.poetry.dev-dependencies]
    for name, ver in poetry.get('dev-dependencies', {}).items():
        dev_deps[name] = ver if isinstance(ver, str) else str(ver)

    # Poetry: [tool.poetry.group.*.dependencies]
    for group_data in poetry.get('group', {}).values():
        if isinstance(group_data, dict):
            for name, ver in group_data.get('dependencies', {}).items():
                dev_deps[name] = ver if isinstance(ver, str) else str(ver)

    # PEP 621: [project.dependencies]
    for dep_str in project.get('dependencies', []):
        name, ver = _parse_pep508(str(dep_str))
        if name and name not in deps:
            deps[name] = ver

    # PEP 621: [project.optional-dependencies]
    DEV_GROUPS = {'dev', 'test', 'lint', 'typing', 'type-checking', 'docs'}
    for group_name, dep_list in project.get('optional-dependencies', {}).items():
        for dep_str in dep_list:
            name, ver = _parse_pep508(str(dep_str))
            if not name:
                continue
            if group_name.lower() in DEV_GROUPS:
                dev_deps[name] = ver
            else:
                extra_deps[name] = ver

    # PEP 735: [dependency-groups]
    for group_name, items in raw.get('dependency-groups', {}).items():
        for item in items:
            if isinstance(item, str):
                name, ver = _parse_pep508(item)
                if name:
                    dev_deps[name] = ver

    scripts = {**project.get('scripts', {}), **poetry.get('scripts', {})}

    return {
        'dependencies': deps,
        'dev_dependencies': dev_deps,
        'extra_dependencies': extra_deps,
        'scripts': scripts,
    }


# ---------------------------------------------------------------------------
# ソースコード抽出 (import 文)
# ---------------------------------------------------------------------------

def _project_tree(repo_path: str) -> str:
    EXCLUDE = {'.git', '__pycache__', '.venv', 'venv', 'env', 'node_modules',
               'dist', 'build', '.tox', '.mypy_cache', 'target'}
    try:
        entries = []
        for e in sorted(os.scandir(repo_path), key=lambda e: (not e.is_dir(), e.name)):
            if e.name.startswith('.') or e.name in EXCLUDE:
                continue
            entries.append(e.name + ("/" if e.is_dir() else ""))
        return "\n".join(entries) or "(empty)"
    except Exception:
        return "(unavailable)"


def _import_lines(repo_path: str) -> str:
    EXTENSIONS = {'.py'}
    EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'env',
                    'node_modules', 'dist', 'build', '.tox', '.mypy_cache'}
    MAX_FILES = 200
    MAX_LINES_TOTAL = 600

    import_re = re.compile(
        r"^[ \t]*(?:import\s+[a-zA-Z_]\w*|from\s+[a-zA-Z_]\w*\s+import).*$",
        re.MULTILINE,
    )

    parts = []
    total_lines = 0
    file_count = 0

    for dirpath, dirnames, filenames in os.walk(repo_path):
        if file_count >= MAX_FILES or total_lines >= MAX_LINES_TOTAL:
            break
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for filename in filenames:
            if os.path.splitext(filename)[1] not in EXTENSIONS:
                continue
            filepath = os.path.join(dirpath, filename)
            rel_path = os.path.relpath(filepath, repo_path)
            try:
                with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                continue
            matches = import_re.findall(content)
            if not matches:
                continue
            remaining = MAX_LINES_TOTAL - total_lines
            lines = [m.strip() for m in matches[:remaining]]
            parts.append(f"### {rel_path}\n" + "\n".join(lines))
            total_lines += len(lines)
            file_count += 1
            if file_count >= MAX_FILES or total_lines >= MAX_LINES_TOTAL:
                break

    return "\n\n".join(parts) or "(no import statements found)"


def _config_refs(repo_path: str) -> str:
    CONFIG_FILES = [
        "pyproject.toml", "setup.cfg", "pytest.ini", ".flake8",
        "mypy.ini", ".mypy.ini", "tox.ini", ".coveragerc", ".pylintrc",
        "docs/conf.py", ".pre-commit-config.yaml",
    ]
    MAX_FILE_SIZE = 3000
    parts = []
    for filename in CONFIG_FILES:
        filepath = os.path.join(repo_path, filename)
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read(MAX_FILE_SIZE)
            parts.append(f"### {filename}\n{content.strip()}")
        except Exception:
            continue
    return "\n\n".join(parts) or "(no config files found)"


# ---------------------------------------------------------------------------
# LLM (Ollama)
# ---------------------------------------------------------------------------

_prompt_cache: Dict = {}


def _load_prompt_template() -> Tuple[str, Dict]:
    if _prompt_cache:
        return _prompt_cache["t"], _prompt_cache["p"]
    template_path = os.path.join(PROMPTS_BASE, "template.md")
    params_path   = os.path.join(PROMPTS_BASE, "params", f"{LANGUAGE}.json")
    with open(template_path, 'r', encoding='utf-8') as f:
        t = f.read()
    with open(params_path, 'r', encoding='utf-8') as f:
        p = json.load(f)
    _prompt_cache["t"] = t
    _prompt_cache["p"] = p
    return t, p


def _build_prompt(template: str, variables: Dict) -> str:
    def rep(m):
        k = m.group(1)
        return str(variables[k]) if k in variables else m.group(0)
    return re.sub(r"\{(\w+)\}", rep, template)


def run_llm(repo_path: str, model: str, pkg: Dict) -> Tuple[List[str], List[str], bool]:
    deps = pkg.get('dependencies', {})

    if not deps:
        return [], [], False

    template, params = _load_prompt_template()

    def fmt_rule(r):
        ls = r.splitlines()
        if not ls:
            return ""
        out = f"- {ls[0]}"
        for l in ls[1:]:
            out += f"\n  {l}"
        return out

    variables = {
        **params,
        "dependencies":       "\n".join(f"- {k}: {v}" for k, v in deps.items()),
        "dev_dependencies":   "(none)",
        "extra_dependencies": "(none)",
        "scripts":            "(none)",
        "project_tree":       _project_tree(repo_path),
        "source_code":        _import_lines(repo_path),
        "config_references":  _config_refs(repo_path),
        "language_rules":     "\n".join(fmt_rule(r) for r in params.get("language_rules", [])),
    }
    prompt = _build_prompt(template, variables)

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0, "num_predict": 8000, "num_ctx": 16384},
    }
    if model.startswith("qwen3"):
        payload["think"] = False

    last_err = None
    for attempt in range(3):
        try:
            resp = requests.post(
                f"{OLLAMA_URL}/api/chat",
                json=payload,
                timeout=LLM_TIMEOUT,
            )
            resp.raise_for_status()
            raw_resp = resp.json()
            text = raw_resp.get("message", {}).get("content", "")
            print(f"  [llm:{model}] eval_count={raw_resp.get('eval_count')} prompt_eval_count={raw_resp.get('prompt_eval_count')}")
            break
        except Exception as e:
            last_err = e
            print(f"  [llm:{model}] API error (attempt {attempt+1}/3): {e}")
            if attempt < 2:
                time.sleep(15)
    else:
        return [], [], False

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e <= s:
        print(f"  [llm:{model}] no JSON found | response={repr(text[:300])}")
        return [], [], False
    try:
        r = json.loads(text[s:e+1])
    except json.JSONDecodeError as ex:
        print(f"  [llm:{model}] JSON parse error: {ex}")
        return [], [], False

    return (
        r.get("unused_dependencies", []),
        r.get("missing_dependencies", []),
        True,
    )


# ---------------------------------------------------------------------------
# リポジトリ単位の解析
# ---------------------------------------------------------------------------

def _empty(owner: str, repo: str, error: str = None) -> Dict[str, Any]:
    return {
        "repo": f"{owner}/{repo}", "error": error,
        "all_dep": [], "all_dev_dep": [],
        "llama_unused_dep":    [], "llama_missing_dep":    [], "llama_success":    False,
        "qwen_unused_dep":     [], "qwen_missing_dep":     [], "qwen_success":     False,
        "deepseek_unused_dep": [], "deepseek_missing_dep": [], "deepseek_success": False,
    }


def analyze_repo(owner: str, repo: str) -> Dict[str, Any]:
    repo_path = os.path.join(CLONES_DIR, f"{owner}-{repo}")
    print(f"\n{'='*60}")
    print(f"[{owner}/{repo}]")

    if os.path.exists(repo_path):
        shutil.rmtree(repo_path, ignore_errors=True)
    try:
        print("  Cloning ...")
        Repo.clone_from(f"https://github.com/{owner}/{repo}", repo_path)
    except Exception as e:
        print(f"  [error] clone failed: {e}")
        return _empty(owner, repo, error=str(e))

    pkg      = parse_pyproject_toml(repo_path)
    deps     = pkg.get('dependencies', {})
    dev_deps = pkg.get('dev_dependencies', {})

    row: Dict[str, Any] = {
        "repo": f"{owner}/{repo}",
        "error": None,
        "all_dep":     list(deps.keys()),
        "all_dev_dep": list(dev_deps.keys()),
    }

    for label, model in [
        ("llama",    OLLAMA_MODEL_LLAMA),
        ("qwen",     OLLAMA_MODEL_QWEN),
        ("deepseek", OLLAMA_MODEL_DEEPSEEK),
    ]:
        t0 = time.time()
        unused_dep, missing, ok = run_llm(repo_path, model, pkg)
        elapsed = time.time() - t0
        print(f"  {label:<10}: {len(unused_dep)} unused_dep  ({elapsed:.1f}s) ok={ok}")
        row[f"{label}_unused_dep"]  = unused_dep
        row[f"{label}_missing_dep"] = missing
        row[f"{label}_success"]     = ok

    try:
        shutil.rmtree(repo_path)
    except Exception:
        pass

    return row


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-list", default=PS8_CSV)
    parser.add_argument("--index", type=int, default=None,
                        help=f"バッチインデックス (各{BATCH_SIZE}件). --skip/--limit より優先")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip",  type=int, default=0)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    if args.index is not None:
        args.skip  = args.index * BATCH_SIZE
        args.limit = BATCH_SIZE
        if args.output is None:
            args.output = os.path.join(OUTPUT_DIR, f"step1_results-{args.index}.csv")
    else:
        if args.output is None:
            args.output = RESULTS_CSV

    os.makedirs(OUTPUT_DIR, exist_ok=True)
    os.makedirs(CLONES_DIR, exist_ok=True)

    df = pd.read_csv(args.repo_list)
    repos: List[str] = df["name"].tolist()

    if args.skip:
        repos = repos[args.skip:]
    if args.limit:
        repos = repos[:args.limit]

    done: set = set()
    rows: List[Dict] = []

    # 前回の途中結果を読み込んで再開 (全LLMがFalseの行は再実行)
    if os.path.exists(args.output):
        try:
            prev_df = pd.read_csv(args.output)
            success_mask = (
                prev_df[["llama_success", "qwen_success", "deepseek_success"]].any(axis=1)
                | prev_df["error"].notna()
            )
            done = set(prev_df.loc[success_mask, "repo"].tolist())
            rows = prev_df.loc[success_mask].to_dict("records")
            failed_count = int((~success_mask).sum())
            print(f"Resuming: {len(done)} repos already done, {failed_count} all-failed repos will be re-run")
        except pd.errors.EmptyDataError:
            print("Output file is empty, starting fresh")

    print(f"Target: {len(repos)} repos  |  Output: {args.output}")

    for full_name in repos:
        if full_name in done:
            print(f"  Skip (done): {full_name}")
            continue
        parts = full_name.split("/")
        if len(parts) != 2:
            print(f"  [warn] unexpected format: {full_name}")
            continue
        owner, repo = parts
        row = analyze_repo(owner, repo)
        rows.append(row)
        done.add(full_name)
        pd.DataFrame(rows).to_csv(args.output, index=False)
        print(f"  Saved ({len(rows)} done)")

    print(f"\nFinished. Results: {args.output}")


if __name__ == "__main__":
    main()

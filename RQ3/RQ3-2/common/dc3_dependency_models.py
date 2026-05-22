# common/dc3_dependency_models.py
"""DC3: LLMで未使用依存を特定 (llama / qwen / deepseek、言語対応版)
対象言語: python / rust / csharp
"""

import json
import os
import re
import sys
import time
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests

import config
from git_utils import checkout_commit

TOOL_TIMEOUT = 120
LLM_TIMEOUT = 300

_PROMPTS_BASE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts")


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
    raise RuntimeError("No TOML library found. Install tomli or toml: pip install tomli")


# ---------------------------------------------------------------------------
# 言語別パッケージファイル解析
# ---------------------------------------------------------------------------

def _parse_pep508(dep_str: str) -> Tuple[str, str]:
    """PEP 508 依存文字列から (名前, バージョン指定) を返す"""
    dep_str = dep_str.strip().strip('"\'')
    for sep in ['>=', '<=', '~=', '==', '!=', '>', '<', '[', '@', ';']:
        idx = dep_str.find(sep)
        if idx > 0:
            name = dep_str[:idx].strip()
            ver = dep_str[idx:].split(';')[0].strip()
            return name, ver
    return dep_str.split(';')[0].strip(), '*'


def _parse_pyproject_toml(repo_path: str) -> Dict:
    path = os.path.join(repo_path, "pyproject.toml")
    if not os.path.exists(path):
        return {}
    try:
        raw = _load_toml(path)
    except Exception as e:
        print(f"  Error parsing pyproject.toml: {e}")
        return {}

    tool = raw.get('tool', {})
    poetry = tool.get('poetry', {})
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


def _parse_cargo_toml(repo_path: str) -> Dict:
    path = os.path.join(repo_path, "Cargo.toml")
    if not os.path.exists(path):
        return {}
    try:
        raw = _load_toml(path)
    except Exception as e:
        print(f"  Error parsing Cargo.toml: {e}")
        return {}

    def _extract(dep_dict: dict) -> dict:
        result = {}
        for name, ver in dep_dict.items():
            if isinstance(ver, str):
                result[name] = ver
            elif isinstance(ver, dict):
                result[name] = ver.get('version', '*')
            else:
                result[name] = str(ver)
        return result

    return {
        'dependencies': _extract(raw.get('dependencies', {})),
        'dev_dependencies': _extract(raw.get('dev-dependencies', {})),
        'extra_dependencies': _extract(raw.get('build-dependencies', {})),
        'scripts': {},
    }


def _parse_csproj(repo_path: str) -> Dict:
    csproj_files = []
    EXCLUDE_DIRS = {'.git', 'bin', 'obj', 'node_modules', 'packages'}
    for dirpath, dirnames, filenames in os.walk(repo_path):
        dirnames[:] = [d for d in dirnames if d not in EXCLUDE_DIRS]
        for fname in filenames:
            if fname.endswith('.csproj'):
                csproj_files.append(os.path.join(dirpath, fname))

    deps: Dict[str, str] = {}
    dev_deps: Dict[str, str] = {}

    for fname in csproj_files:
        try:
            tree = ET.parse(fname)
            root = tree.getroot()
            for ref in root.iter('PackageReference'):
                name = ref.get('Include') or ref.get('include', '')
                version = ref.get('Version') or ref.get('version', '*')
                private_assets = ref.get('PrivateAssets', '').lower()
                if not name:
                    continue
                if 'all' in private_assets:
                    dev_deps[name] = version
                else:
                    deps[name] = version
            for ref in root.iter('PackageVersion'):
                name = ref.get('Include') or ref.get('include', '')
                version = ref.get('Version') or ref.get('version', '*')
                if name and name not in deps:
                    deps[name] = version
        except Exception as e:
            print(f"  Error parsing {fname}: {e}")

    return {
        'dependencies': deps,
        'dev_dependencies': dev_deps,
        'extra_dependencies': {},
        'scripts': {},
    }


def parse_package_file(repo_path: str) -> Dict:
    """config.LANGUAGE に応じてパッケージファイルを解析する"""
    lang = getattr(config, 'LANGUAGE', 'python')
    if lang == 'python':
        return _parse_pyproject_toml(repo_path)
    elif lang == 'rust':
        return _parse_cargo_toml(repo_path)
    elif lang == 'csharp':
        return _parse_csproj(repo_path)
    return {}


# ---------------------------------------------------------------------------
# プロジェクトツリー / ソースコード抽出
# ---------------------------------------------------------------------------

def _get_project_tree(repo_path: str) -> str:
    EXCLUDE = {'.git', 'node_modules', 'dist', 'build', 'target',
               '__pycache__', '.venv', 'venv', '.mypy_cache', '.tox'}
    try:
        entries = []
        for entry in sorted(os.scandir(repo_path), key=lambda e: (not e.is_dir(), e.name)):
            if entry.name.startswith('.') or entry.name in EXCLUDE:
                continue
            entries.append(entry.name + ("/" if entry.is_dir() else ""))
        return "\n".join(entries) or "(empty)"
    except Exception:
        return "(unavailable)"


def _extract_python_import_lines(repo_path: str) -> str:
    EXTENSIONS = {'.py'}
    EXCLUDE_DIRS = {'.git', '__pycache__', '.venv', 'venv', 'env',
                    'node_modules', 'dist', 'build', '.tox', '.mypy_cache'}
    MAX_FILES = 200
    MAX_LINES_TOTAL = 600

    # 相対 import (from . or from ..) を除外
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


def _extract_rust_import_lines(repo_path: str) -> str:
    EXTENSIONS = {'.rs'}
    EXCLUDE_DIRS = {'.git', 'target'}
    MAX_FILES = 200
    MAX_LINES_TOTAL = 600

    import_re = re.compile(
        r"^[ \t]*(?:use\s+\w|extern\s+crate\s+\w).*$",
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

    return "\n\n".join(parts) or "(no use statements found)"


def _extract_csharp_import_lines(repo_path: str) -> str:
    EXTENSIONS = {'.cs'}
    EXCLUDE_DIRS = {'.git', 'bin', 'obj', 'node_modules'}
    MAX_FILES = 200
    MAX_LINES_TOTAL = 600

    import_re = re.compile(
        r"^[ \t]*using\s+[\w.]+\s*;.*$",
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

    return "\n\n".join(parts) or "(no using statements found)"


def _extract_import_lines(repo_path: str) -> str:
    lang = getattr(config, 'LANGUAGE', 'python')
    if lang == 'python':
        return _extract_python_import_lines(repo_path)
    elif lang == 'rust':
        return _extract_rust_import_lines(repo_path)
    elif lang == 'csharp':
        return _extract_csharp_import_lines(repo_path)
    return "(unsupported language)"


_CONFIG_FILES_BY_LANGUAGE: Dict[str, List[str]] = {
    "python": [
        "pyproject.toml", "setup.cfg", "pytest.ini", ".flake8",
        "mypy.ini", ".mypy.ini", "tox.ini", ".coveragerc", ".pylintrc",
        "docs/conf.py", ".pre-commit-config.yaml",
    ],
    "rust": [
        "Cargo.toml", "build.rs", ".cargo/config.toml", "rust-toolchain.toml",
    ],
    "csharp": [
        "Directory.Build.props", "Directory.Build.targets",
        "Directory.Packages.props", "global.json",
        ".editorconfig", "appsettings.json", "NuGet.Config",
    ],
}


def _extract_config_references(repo_path: str) -> str:
    lang = getattr(config, 'LANGUAGE', 'python')
    config_files = _CONFIG_FILES_BY_LANGUAGE.get(lang, [])
    MAX_FILE_SIZE = 3000

    parts = []
    for filename in config_files:
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

_prompt_template_cache: Dict = {}


def _load_prompt_template() -> Tuple[str, Dict]:
    if _prompt_template_cache:
        return _prompt_template_cache["template"], _prompt_template_cache["params"]

    template_path = os.path.join(_PROMPTS_BASE, "template.md")
    lang = getattr(config, 'LANGUAGE', 'python')
    params_path = os.path.join(_PROMPTS_BASE, "params", f"{lang}.json")

    with open(template_path, 'r', encoding='utf-8') as f:
        template = f.read()
    with open(params_path, 'r', encoding='utf-8') as f:
        params = json.load(f)

    _prompt_template_cache["template"] = template
    _prompt_template_cache["params"] = params
    return template, params


def _build_prompt(template: str, variables: Dict) -> str:
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)
    return re.sub(r"\{(\w+)\}", replacer, template)


def run_llm(repo_path: str, model: str) -> Tuple[List[str], List[str], List[str], bool]:
    file_content = parse_package_file(repo_path)
    deps = file_content.get('dependencies', {})
    dev_deps = file_content.get('dev_dependencies', {})
    extra_deps = file_content.get('extra_dependencies', {})
    scripts = file_content.get('scripts', {})

    if not deps and not dev_deps:
        return [], [], [], False

    template, params = _load_prompt_template()

    dep_lines = "\n".join(f"- {k}: {v}" for k, v in deps.items()) or "(none)"
    dev_dep_lines = "\n".join(f"- {k}: {v}" for k, v in dev_deps.items()) or "(none)"
    extra_dep_lines = "\n".join(f"- {k}: {v}" for k, v in extra_deps.items()) or "(none)"
    scripts_lines = "\n".join(f"  \"{k}\": \"{v}\"" for k, v in scripts.items()) or "(none)"

    def _format_rule(rule: str) -> str:
        lines = rule.splitlines()
        if not lines:
            return ""
        result = f"- {lines[0]}"
        for line in lines[1:]:
            result += f"\n  {line}"
        return result

    language_rules = "\n".join(_format_rule(r) for r in params.get("language_rules", []))

    variables = {
        **params,
        "dependencies": dep_lines,
        "dev_dependencies": dev_dep_lines,
        "extra_dependencies": extra_dep_lines,
        "scripts": scripts_lines,
        "project_tree": _get_project_tree(repo_path),
        "source_code": _extract_import_lines(repo_path),
        "config_references": _extract_config_references(repo_path),
        "language_rules": language_rules,
    }

    prompt = _build_prompt(template, variables)

    try:
        chat_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "stream": False,
            "options": {
                "temperature": 0,
                "num_predict": 8000,
                "num_ctx": 16384,
            },
            "format": "json",
        }
        if model.startswith("qwen3"):
            chat_payload["think"] = False

        response = requests.post(
            f"{config.OLLAMA_URL}/api/chat",
            json=chat_payload,
            timeout=LLM_TIMEOUT,
        )
        response.raise_for_status()
        response_text = response.json().get("message", {}).get("content", "")
    except Exception as e:
        print(f"  Ollama ({model}) API error: {e}")
        return [], [], [], False

    response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)

    start = response_text.find("{")
    end = response_text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        print(f"  Ollama ({model}): failed to extract JSON from response")
        return [], [], [], False

    try:
        result = json.loads(response_text[start:end + 1])
    except json.JSONDecodeError as e:
        print(f"  Ollama ({model}) JSON parse error: {e}")
        return [], [], [], False

    unused_dep = result.get("unused_dependencies", [])
    unused_dev_dep = result.get("unused_dev_dependencies", [])
    missing_dep = result.get("missing_dependencies", [])

    return unused_dep, unused_dev_dep, missing_dep, True


# ---------------------------------------------------------------------------
# コミット単位の解析
# ---------------------------------------------------------------------------

_LIST_KEYS = [
    "llama_unused_dep", "llama_unused_dev_dep", "llama_missing_dep",
    "qwen_unused_dep", "qwen_unused_dev_dep", "qwen_missing_dep",
    "deepseek_unused_dep", "deepseek_unused_dev_dep", "deepseek_missing_dep",
]
_DICT_KEYS = ["dev_dep", "runtime_dep"]
_BOOL_KEYS = ["llama_success", "qwen_success", "deepseek_success"]


def analyze_commit(repo_path: str, sha: str) -> Dict[str, Any]:
    print(f"    Analyzing {sha[:7]}...")

    if not checkout_commit(repo_path, sha):
        return {k: [] for k in _LIST_KEYS} | {k: {} for k in _DICT_KEYS} | {k: False for k in _BOOL_KEYS}

    file_content = parse_package_file(repo_path)
    dev_dep = file_content.get('dev_dependencies', {})
    runtime_dep = file_content.get('dependencies', {})

    # --- llama ---
    t0 = time.time()
    llama_unused, llama_unused_dev, llama_missing, llama_ok = run_llm(repo_path, config.OLLAMA_MODEL_LLAMA)
    print(f"      llama:    {len(llama_unused)} unused, {len(llama_unused_dev)} unused_dev ({time.time()-t0:.1f}s)")

    # --- qwen ---
    t0 = time.time()
    qwen_unused, qwen_unused_dev, qwen_missing, qwen_ok = run_llm(repo_path, config.OLLAMA_MODEL_QWEN)
    print(f"      qwen:     {len(qwen_unused)} unused, {len(qwen_unused_dev)} unused_dev ({time.time()-t0:.1f}s)")

    # --- deepseek ---
    t0 = time.time()
    ds_unused, ds_unused_dev, ds_missing, ds_ok = run_llm(repo_path, config.OLLAMA_MODEL_DEEPSEEK)
    print(f"      deepseek: {len(ds_unused)} unused, {len(ds_unused_dev)} unused_dev ({time.time()-t0:.1f}s)")

    return {
        "dev_dep": dev_dep,
        "runtime_dep": runtime_dep,
        "llama_unused_dep": llama_unused,
        "llama_unused_dev_dep": llama_unused_dev,
        "llama_missing_dep": llama_missing,
        "llama_success": llama_ok,
        "qwen_unused_dep": qwen_unused,
        "qwen_unused_dev_dep": qwen_unused_dev,
        "qwen_missing_dep": qwen_missing,
        "qwen_success": qwen_ok,
        "deepseek_unused_dep": ds_unused,
        "deepseek_unused_dev_dep": ds_unused_dev,
        "deepseek_missing_dep": ds_missing,
        "deepseek_success": ds_ok,
    }


def _get_build_status_simple(ci_data) -> str:
    if ci_data is None:
        return "unknown"
    try:
        check_runs, status_code = ci_data
        if status_code != 200 or check_runs is None or len(check_runs) == 0:
            return "unknown"
        conclusions = [r.get("conclusion") for r in check_runs if r.get("conclusion")]
        if not conclusions:
            return "unknown"
        if "failure" in conclusions:
            return "failure"
        if all(c == "success" for c in conclusions):
            return "success"
        return "unknown"
    except Exception:
        return "unknown"


def run_all_models_for_commits(owner: str, repo: str) -> pd.DataFrame:
    repo_path = config.get_clone_path(owner, repo)
    ci_data_path = config.get_ci_data_path(owner, repo)
    output_path = config.get_dependency_data_path(owner, repo)

    os.makedirs(os.path.dirname(output_path), exist_ok=True)

    if not os.path.exists(ci_data_path):
        print(f"  No CI data found for {owner}/{repo}")
        return pd.DataFrame()

    try:
        data = pd.read_json(ci_data_path)
    except Exception as e:
        print(f"  Error reading CI data for {owner}/{repo}: {e}")
        return pd.DataFrame()

    if len(data) == 0:
        print(f"  No commits in CI data for {owner}/{repo}")
        data.to_json(output_path)
        return data

    for key in _LIST_KEYS:
        data[key] = [[] for _ in range(len(data))]
    for key in _DICT_KEYS:
        data[key] = [{} for _ in range(len(data))]
    for key in _BOOL_KEYS:
        data[key] = False

    build_statuses = data["ci_data"].map(_get_build_status_simple)
    parent_build_statuses = data["parent_ci_data"].map(_get_build_status_simple)
    ci_available_mask = (build_statuses != "unknown") & (parent_build_statuses != "unknown")
    data_to_analyze = data[ci_available_mask]

    print(f"  CI available: {len(data_to_analyze)}/{len(data)} commits")

    if len(data_to_analyze) == 0:
        data.to_json(output_path)
        print(f"  No commits with CI data — saved empty model results for {owner}/{repo}")
        return data

    all_result_keys = _DICT_KEYS + _LIST_KEYS + _BOOL_KEYS

    for idx in data_to_analyze.index:
        sha = data.at[idx, "sha"]
        result = analyze_commit(repo_path, sha)
        for key in all_result_keys:
            if key in result:
                data.at[idx, key] = result[key]

    data.to_json(output_path)
    print(f"  Saved dependency data for {owner}/{repo}")

    return data

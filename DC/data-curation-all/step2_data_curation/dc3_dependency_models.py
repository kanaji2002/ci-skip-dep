# step2_data_curation/dc3_dependency_models.py
"""DC3: depcheck / knip / llama / qwen で未使用依存を特定 (DC2でCIが取得できたコミットのみ)"""

import json
import os
import re
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from utils.git_utils import checkout_commit

# depcheck / knip のサブプロセスタイムアウト（秒）
TOOL_TIMEOUT = 120
# Ollama API タイムアウト（秒）
LLM_TIMEOUT = 300

# prompts ディレクトリのベースパス
_PROMPTS_BASE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "prompts")


# ---------------------------------------------------------------------------
# package.json ユーティリティ
# ---------------------------------------------------------------------------

def parse_package_json(repo_path: str) -> Dict:
    try:
        path = os.path.join(repo_path, "package.json")
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().replace("//", "")
        return json.loads(content)
    except Exception as e:
        print(f"  Error parsing package.json: {e}")
        return {}


def get_dependencies_from_package(file_content: Dict) -> Tuple[Dict, Dict, Dict, bool]:
    dev_deps = file_content.get("devDependencies", {})
    peer_deps = file_content.get("peerDependencies", {})
    runtime_deps = file_content.get("dependencies", {})
    success = bool(dev_deps or peer_deps or runtime_deps)
    return dev_deps, peer_deps, runtime_deps, success


# ---------------------------------------------------------------------------
# depcheck
# ---------------------------------------------------------------------------

def run_depcheck(repo_path: str) -> Tuple[List[str], List[str], bool]:
    """
    depcheck を実行して未使用依存を返す

    Returns:
        (unused_dep, unused_dev_dep, success)
    """
    try:
        result = subprocess.run(
            ["depcheck", ".", "--json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        # depcheck は未使用依存があると exit code 1 を返すので stderr は無視
        raw = result.stdout.strip()
        if not raw:
            return [], [], False

        data = json.loads(raw)
        unused_dep = data.get("dependencies", [])
        unused_dev_dep = data.get("devDependencies", [])
        return unused_dep, unused_dev_dep, True

    except subprocess.TimeoutExpired:
        print(f"  depcheck timeout at {repo_path}")
        return [], [], False
    except json.JSONDecodeError as e:
        print(f"  depcheck JSON parse error: {e}")
        return [], [], False
    except FileNotFoundError:
        print("  depcheck not found in PATH")
        return [], [], False
    except Exception as e:
        print(f"  depcheck error: {e}")
        return [], [], False


# ---------------------------------------------------------------------------
# knip
# ---------------------------------------------------------------------------

def run_knip(repo_path: str) -> Tuple[List[str], List[str], bool]:
    """
    knip を実行して未使用依存を返す

    Returns:
        (unused_dep, unused_dev_dep, success)
    """
    try:
        # .prettierrc.cjs 等が require() を使う場合、node_modules がないと
        # knip が設定ファイルのロードに失敗して JSON を出力できない。
        # --ignore-scripts でポストインストールスクリプトをスキップしてインストール。
        node_modules_path = os.path.join(repo_path, "node_modules")
        if not os.path.isdir(node_modules_path):
            subprocess.run(
                ["npm", "install", "--ignore-scripts", "--prefer-offline"],
                cwd=repo_path,
                capture_output=True,
                timeout=TOOL_TIMEOUT,
            )

        result = subprocess.run(
            ["npx", "--yes", "knip", "--reporter", "json"],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=TOOL_TIMEOUT,
        )
        raw = result.stdout.strip()
        if not raw:
            return [], [], False

        data = json.loads(raw)

        unused_dep: List[str] = []
        unused_dev_dep: List[str] = []

        # knip の JSON 出力は {files:[], issues:[{file, dependencies:[{name}], devDependencies:[{name}], ...}]}
        issues = data.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                for entry in issue.get("dependencies", []):
                    name = entry.get("name") if isinstance(entry, dict) else entry
                    if name:
                        unused_dep.append(name)
                for entry in issue.get("devDependencies", []):
                    name = entry.get("name") if isinstance(entry, dict) else entry
                    if name:
                        unused_dev_dep.append(name)
        elif isinstance(issues, dict):
            # バージョンによっては {dependencies: {pkg: [...]}, devDependencies: {...}} 形式
            for pkg in issues.get("dependencies", {}):
                unused_dep.append(pkg)
            for pkg in issues.get("devDependencies", {}):
                unused_dev_dep.append(pkg)

        # 重複除去
        unused_dep = list(dict.fromkeys(unused_dep))
        unused_dev_dep = list(dict.fromkeys(unused_dev_dep))

        return unused_dep, unused_dev_dep, True

    except subprocess.TimeoutExpired:
        print(f"  knip timeout at {repo_path}")
        return [], [], False
    except json.JSONDecodeError as e:
        print(f"  knip JSON parse error: {e}")
        return [], [], False
    except Exception as e:
        print(f"  knip error: {e}")
        return [], [], False


# ---------------------------------------------------------------------------
# LLM (Ollama)
# ---------------------------------------------------------------------------

# テンプレートキャッシュ（初回ロード後は再読み込みしない）
_prompt_template_cache: Dict = {}


def _load_prompt_template() -> tuple:
    """prompts/template.md と params/javascript.json を読み込む"""
    if _prompt_template_cache:
        return _prompt_template_cache["template"], _prompt_template_cache["params"]

    template_path = os.path.join(_PROMPTS_BASE, "template.md")
    params_path = os.path.join(_PROMPTS_BASE, "params", "javascript.json")

    with open(template_path, "r", encoding="utf-8") as f:
        template = f.read()
    with open(params_path, "r", encoding="utf-8") as f:
        params = json.load(f)

    _prompt_template_cache["template"] = template
    _prompt_template_cache["params"] = params
    return template, params


def _build_prompt(template: str, variables: Dict) -> str:
    """{variable} 形式のプレースホルダーを variables で置換する。
    単語以外の { } (JSONリテラル等) はそのまま残す。"""
    def replacer(match: re.Match) -> str:
        key = match.group(1)
        return str(variables[key]) if key in variables else match.group(0)
    return re.sub(r"\{(\w+)\}", replacer, template)


def _get_project_tree(repo_path: str) -> str:
    """トップレベルのディレクトリ構造を返す"""
    EXCLUDE = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".cache"}
    try:
        entries = []
        for entry in sorted(os.scandir(repo_path), key=lambda e: (not e.is_dir(), e.name)):
            if entry.name.startswith(".") or entry.name in EXCLUDE:
                continue
            entries.append(entry.name + ("/" if entry.is_dir() else ""))
        return "\n".join(entries) or "(empty)"
    except Exception:
        return "(unavailable)"


_CONFIG_FILES_BY_LANGUAGE: Dict[str, List[str]] = {
    "javascript": [
        ".babelrc", ".babelrc.js", ".babelrc.ts",
        "babel.config.js", "babel.config.ts", "babel.config.json",
        "webpack.config.js", "webpack.config.ts",
        "jest.config.js", "jest.config.ts", "jest.config.json",
        ".eslintrc", ".eslintrc.js", ".eslintrc.ts", ".eslintrc.json", ".eslintrc.yml",
        "eslint.config.js", "eslint.config.ts", "eslint.config.mjs",
        "tsconfig.json",
        "vite.config.js", "vite.config.ts",
        "rollup.config.js", "rollup.config.ts",
        ".stylelintrc", ".stylelintrc.json", "stylelint.config.js",
        "prettier.config.js", ".prettierrc.js",
        "vitest.config.js", "vitest.config.ts",
    ],
    "python": [
        "pyproject.toml",
        "setup.cfg",
        "pytest.ini",
        ".flake8",
        "mypy.ini",
        ".mypy.ini",
        "tox.ini",
        ".coveragerc",
        ".pylintrc",
        "docs/conf.py",
        ".pre-commit-config.yaml",
    ],
    "rust": [
        "Cargo.toml",
        "build.rs",
        ".cargo/config.toml",
        "rust-toolchain.toml",
    ],
    "csharp": [
        "Directory.Build.props",
        "Directory.Build.targets",
        "Directory.Packages.props",
        "global.json",
        ".editorconfig",
        "appsettings.json",
        "NuGet.Config",
    ],
}


def _extract_config_references(repo_path: str, language: str = "javascript") -> str:
    """設定ファイルの内容を返す (プラグイン・型定義などの間接参照を LLM に渡す)"""
    lang_key = language.lower().split("/")[0]  # "JavaScript/Node.js" → "javascript"
    config_files = _CONFIG_FILES_BY_LANGUAGE.get(lang_key, _CONFIG_FILES_BY_LANGUAGE["javascript"])
    MAX_FILE_SIZE = 3000  # characters per file

    parts = []
    for filename in config_files:
        filepath = os.path.join(repo_path, filename)
        if not os.path.exists(filepath):
            continue
        try:
            with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                content = f.read(MAX_FILE_SIZE)
            parts.append(f"### {filename}\n{content.strip()}")
        except Exception:
            continue

    return "\n\n".join(parts) or "(no config files found)"


def _extract_import_lines(repo_path: str) -> str:
    """ソースファイルから import/require 行をファイルパス付きで抽出して返す"""
    EXTENSIONS = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
    EXCLUDE_DIRS = {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    MAX_FILES = 200
    MAX_LINES_TOTAL = 600

    import_line_re = re.compile(
        r"^[ \t]*(?:"
        r"import\s"                                        # import foo / import 'pkg'
        r"|export\s.*from\s"                               # export { foo } from 'pkg'
        r"|(?:const|let|var)\s+.*=\s*require\s*\("        # const x = require('pkg')
        r"|require\s*\("                                   # require('pkg') standalone
        r"|.*=\s*await\s+import\s*\("                     # x = await import('pkg')
        r").*$",
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
                with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
                    content = f.read()
            except Exception:
                continue
            matches = import_line_re.findall(content)
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


def run_llm(repo_path: str, model: str) -> Tuple[List[str], List[str], List[str], bool]:
    """
    Ollama モデルで未使用依存を特定

    Returns:
        (unused_dep, unused_dev_dep, missing_dep, success)
    """
    file_content = parse_package_json(repo_path)
    dev_deps = file_content.get("devDependencies", {})
    peer_deps = file_content.get("peerDependencies", {})
    runtime_deps = file_content.get("dependencies", {})

    if not dev_deps and not runtime_deps:
        return [], [], [], False

    template, params = _load_prompt_template()

    dep_lines = "\n".join(f"- {k}: {v}" for k, v in runtime_deps.items()) or "(none)"
    dev_dep_lines = "\n".join(f"- {k}: {v}" for k, v in dev_deps.items()) or "(none)"
    peer_dep_lines = "\n".join(f"- {k}: {v}" for k, v in peer_deps.items()) or "(none)"
    scripts = file_content.get("scripts", {})
    scripts_lines = "\n".join(f"  \"{k}\": \"{v}\"" for k, v in scripts.items()) or "(none)"
    # 各ルールは複数行を含む場合がある。先頭行に "- " を付け、続行行はインデント
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
        "extra_dependencies": peer_dep_lines,
        "scripts": scripts_lines,
        "project_tree": _get_project_tree(repo_path),
        "source_code": _extract_import_lines(repo_path),
        "config_references": _extract_config_references(repo_path, params.get("language", "javascript")),
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
        }
        # Qwen3系のthinkingモードを無効化
        if model.startswith("qwen3"):
            chat_payload["think"] = False
        # deepseek-coderはJSONを返さないことがあるため、constrained decodingを強制
        if "deepseek" in model:
            chat_payload["format"] = "json"

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

    # <think>...</think> ブロックを除去 (Qwen3等のthinkingモデル対策)
    response_text = re.sub(r"<think>.*?</think>", "", response_text, flags=re.DOTALL)

    # 最外殻の { } を探す (greedy: 最初の { から最後の } まで)
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
    # unused_extra_dependencies (peer deps) は unused_dep に合算しない
    missing_dep = result.get("missing_dependencies", [])

    return unused_dep, unused_dev_dep, missing_dep, True


# ---------------------------------------------------------------------------
# コミット単位の解析
# ---------------------------------------------------------------------------

def analyze_commit(repo_path: str, sha: str) -> Dict[str, Any]:
    """
    単一コミットに対して全モデルを実行

    Args:
        repo_path: リポジトリパス
        sha: コミットSHA

    Returns:
        全モデルの解析結果
    """
    print(f"    Analyzing {sha[:7]}...")

    if not checkout_commit(repo_path, sha):
        empty: Dict[str, Any] = {
            "dev_dep": {}, "peer_dep": {}, "runtime_dep": {},
            "depcheck_unused_dep": [], "depcheck_unused_dev_dep": [], "depcheck_success": False,
            "knip_unused_dep": [], "knip_unused_dev_dep": [], "knip_success": False,
            "llama_unused_dep": [], "llama_unused_dev_dep": [], "llama_missing_dep": [], "llama_success": False,
            "qwen_unused_dep": [], "qwen_unused_dev_dep": [], "qwen_missing_dep": [], "qwen_success": False,
            "deepseek_unused_dep": [], "deepseek_unused_dev_dep": [], "deepseek_missing_dep": [], "deepseek_success": False,
        }
        return empty

    # package.json 解析（全モデル共通）
    file_content = parse_package_json(repo_path)
    dev_dep, peer_dep, runtime_dep, _ = get_dependencies_from_package(file_content)

    # --- depcheck ---
    t0 = time.time()
    dc_unused_dep, dc_unused_dev_dep, dc_success = run_depcheck(repo_path)
    print(f"      depcheck: {len(dc_unused_dep)} unused, {len(dc_unused_dev_dep)} unused_dev ({time.time()-t0:.1f}s)")

    # --- knip ---
    t0 = time.time()
    knip_unused_dep, knip_unused_dev_dep, knip_success = run_knip(repo_path)
    print(f"      knip:     {len(knip_unused_dep)} unused, {len(knip_unused_dev_dep)} unused_dev ({time.time()-t0:.1f}s)")

    # --- llama ---
    t0 = time.time()
    llama_unused_dep, llama_unused_dev_dep, llama_missing_dep, llama_success = run_llm(
        repo_path, config.OLLAMA_MODEL_LLAMA
    )
    print(f"      llama:    {len(llama_unused_dep)} unused, {len(llama_unused_dev_dep)} unused_dev ({time.time()-t0:.1f}s)")

    # --- qwen ---
    t0 = time.time()
    qwen_unused_dep, qwen_unused_dev_dep, qwen_missing_dep, qwen_success = run_llm(
        repo_path, config.OLLAMA_MODEL_QWEN
    )
    print(f"      qwen:     {len(qwen_unused_dep)} unused, {len(qwen_unused_dev_dep)} unused_dev ({time.time()-t0:.1f}s)")

    # --- deepseek ---
    t0 = time.time()
    deepseek_unused_dep, deepseek_unused_dev_dep, deepseek_missing_dep, deepseek_success = run_llm(
        repo_path, config.OLLAMA_MODEL_DEEPSEEK
    )
    print(f"      deepseek: {len(deepseek_unused_dep)} unused, {len(deepseek_unused_dev_dep)} unused_dev ({time.time()-t0:.1f}s)")

    return {
        "dev_dep": dev_dep,
        "peer_dep": peer_dep,
        "runtime_dep": runtime_dep,
        # depcheck
        "depcheck_unused_dep": dc_unused_dep,
        "depcheck_unused_dev_dep": dc_unused_dev_dep,
        "depcheck_success": dc_success,
        # knip
        "knip_unused_dep": knip_unused_dep,
        "knip_unused_dev_dep": knip_unused_dev_dep,
        "knip_success": knip_success,
        # llama
        "llama_unused_dep": llama_unused_dep,
        "llama_unused_dev_dep": llama_unused_dev_dep,
        "llama_missing_dep": llama_missing_dep,
        "llama_success": llama_success,
        # qwen
        "qwen_unused_dep": qwen_unused_dep,
        "qwen_unused_dev_dep": qwen_unused_dev_dep,
        "qwen_missing_dep": qwen_missing_dep,
        "qwen_success": qwen_success,
        # deepseek
        "deepseek_unused_dep": deepseek_unused_dep,
        "deepseek_unused_dev_dep": deepseek_unused_dev_dep,
        "deepseek_missing_dep": deepseek_missing_dep,
        "deepseek_success": deepseek_success,
    }


def _get_build_status_simple(ci_data) -> str:
    """CIデータからビルドステータスを判定 (ci-check と同ロジック)"""
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


# モデルカラムのデフォルト値定義
_LIST_KEYS = [
    "depcheck_unused_dep", "depcheck_unused_dev_dep",
    "knip_unused_dep", "knip_unused_dev_dep",
    "llama_unused_dep", "llama_unused_dev_dep", "llama_missing_dep",
    "qwen_unused_dep", "qwen_unused_dev_dep", "qwen_missing_dep",
    "deepseek_unused_dep", "deepseek_unused_dev_dep", "deepseek_missing_dep",
]
_DICT_KEYS = ["dev_dep", "peer_dep", "runtime_dep"]
_BOOL_KEYS = [
    "depcheck_success", "knip_success", "llama_success", "qwen_success", "deepseek_success",
]


def run_all_models_for_commits(owner: str, repo: str) -> pd.DataFrame:
    """
    CIデータが取得できたコミットに対してのみ全モデルを実行

    Args:
        owner: リポジトリオーナー
        repo: リポジトリ名

    Returns:
        解析結果を含む DataFrame (CIなし行はモデルカラムが空)
    """
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

    # モデルカラムをデフォルト値で初期化
    for key in _LIST_KEYS:
        data[key] = [[] for _ in range(len(data))]
    for key in _DICT_KEYS:
        data[key] = [{} for _ in range(len(data))]
    for key in _BOOL_KEYS:
        data[key] = False

    # コミット本体・親の両方でCIデータが取得できた行のみ抽出
    build_statuses = data["ci_data"].map(_get_build_status_simple)
    parent_build_statuses = data["parent_ci_data"].map(_get_build_status_simple)
    ci_available_mask = (build_statuses != "unknown") & (parent_build_statuses != "unknown")
    data_to_analyze = data[ci_available_mask]

    print(f"  CI available: {len(data_to_analyze)}/{len(data)} commits (both commit and parent have CI data)")

    if len(data_to_analyze) == 0:
        data.to_json(output_path)
        print(f"  No commits with CI data — saved empty model results for {owner}/{repo}")
        return data

    print(f"  Running all models for {len(data_to_analyze)} commits...")

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


if __name__ == "__main__":
    if len(sys.argv) >= 3:
        owner, repo = sys.argv[1], sys.argv[2]
        df = run_all_models_for_commits(owner, repo)
        print(df)

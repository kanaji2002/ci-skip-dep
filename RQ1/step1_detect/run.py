"""
Step 1: 未使用依存検知
PS8 CSV に記載の各リポジトリを clone し、5モデルで未使用依存を検知する。
出力: RQ1/output/step1_results.csv

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
import subprocess
import sys
import time
from typing import Any, Dict, List, Tuple

import pandas as pd
import requests
from dotenv import load_dotenv
from git import Repo

# .env 読み込み (GITHUB_TOKEN など)
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
load_dotenv(os.path.join(_ROOT, ".env"))

# ---------------------------------------------------------------------------
# パス設定
# ---------------------------------------------------------------------------
RQ1_DIR     = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUTPUT_DIR  = os.path.join(RQ1_DIR, "output")
CLONES_DIR  = os.path.join(OUTPUT_DIR, "clones")
RESULTS_CSV = os.path.join(OUTPUT_DIR, "step1_results.csv")

PS8_CSV = os.path.join(_ROOT, "PS", "js", "ps8",
                       "ps8_filtered.csv")

PROMPTS_BASE = os.path.join(_ROOT, "DC", "data-curation-all", "prompts")

# ---------------------------------------------------------------------------
# Ollama 設定
# ---------------------------------------------------------------------------
OLLAMA_URL            = "http://localhost:11434"
OLLAMA_MODEL_LLAMA    = "llama3.1:8b"
OLLAMA_MODEL_QWEN     = "qwen3.5:4b"
OLLAMA_MODEL_DEEPSEEK = "deepseek-coder:6.7b-instruct"

TOOL_TIMEOUT = 120   # depcheck / knip (秒)
LLM_TIMEOUT  = 300   # Ollama API (秒)

# ---------------------------------------------------------------------------
# package.json
# ---------------------------------------------------------------------------

def parse_package_json(repo_path: str) -> Dict:
    try:
        with open(os.path.join(repo_path, "package.json"), "r", encoding="utf-8") as f:
            content = f.read().replace("//", "")
        return json.loads(content)
    except Exception as e:
        print(f"  [warn] parse_package_json: {e}")
        return {}

# ---------------------------------------------------------------------------
# depcheck
# ---------------------------------------------------------------------------

def run_depcheck(repo_path: str) -> Tuple[List[str], List[str], bool]:
    try:
        result = subprocess.run(
            ["depcheck", ".", "--json"],
            cwd=repo_path, capture_output=True, text=True, timeout=TOOL_TIMEOUT,
        )
        raw = result.stdout.strip()
        if not raw:
            return [], [], False
        data = json.loads(raw)
        return data.get("dependencies", []), data.get("devDependencies", []), True
    except subprocess.TimeoutExpired:
        print("  [depcheck] timeout"); return [], [], False
    except json.JSONDecodeError as e:
        print(f"  [depcheck] JSON error: {e}"); return [], [], False
    except FileNotFoundError:
        print("  [depcheck] not found in PATH"); return [], [], False
    except Exception as e:
        print(f"  [depcheck] error: {e}"); return [], [], False

# ---------------------------------------------------------------------------
# knip
# ---------------------------------------------------------------------------


def run_knip(repo_path: str) -> Tuple[List[str], List[str], bool]:
    try:
        if not os.path.isdir(os.path.join(repo_path, "node_modules")):
            subprocess.run(
                ["npm", "install", "--ignore-scripts", "--prefer-offline"],
                cwd=repo_path, capture_output=True, timeout=TOOL_TIMEOUT,
            )
        result = subprocess.run(
            ["npx", "--yes", "knip", "--reporter", "json"],
            cwd=repo_path, capture_output=True, text=True, timeout=TOOL_TIMEOUT,
        )
        raw = result.stdout.strip()
        if not raw:
            return [], [], False
        data = json.loads(raw)

        unused_dep: List[str] = []
        unused_dev_dep: List[str] = []
        issues = data.get("issues", [])
        if isinstance(issues, list):
            for issue in issues:
                for e in issue.get("dependencies", []):
                    n = e.get("name") if isinstance(e, dict) else e
                    if n: unused_dep.append(n)
                for e in issue.get("devDependencies", []):
                    n = e.get("name") if isinstance(e, dict) else e
                    if n: unused_dev_dep.append(n)
        elif isinstance(issues, dict):
            unused_dep    = list(issues.get("dependencies", {}).keys())
            unused_dev_dep = list(issues.get("devDependencies", {}).keys())

        return list(dict.fromkeys(unused_dep)), list(dict.fromkeys(unused_dev_dep)), True
    except subprocess.TimeoutExpired:
        print("  [knip] timeout"); return [], [], False
    except json.JSONDecodeError as e:
        print(f"  [knip] JSON error: {e}"); return [], [], False
    except Exception as e:
        print(f"  [knip] error: {e}"); return [], [], False

# ---------------------------------------------------------------------------
# LLM (Ollama)
# ---------------------------------------------------------------------------

_prompt_cache: Dict = {}

def _load_prompt_template() -> Tuple[str, Dict]:
    if _prompt_cache:
        return _prompt_cache["t"], _prompt_cache["p"]
    with open(os.path.join(PROMPTS_BASE, "template.md"), encoding="utf-8") as f:
        t = f.read()
    with open(os.path.join(PROMPTS_BASE, "params", "javascript.json"), encoding="utf-8") as f:
        p = json.load(f)
    _prompt_cache["t"] = t
    _prompt_cache["p"] = p
    return t, p

def _build_prompt(template: str, variables: Dict) -> str:
    def rep(m):
        k = m.group(1)
        return str(variables[k]) if k in variables else m.group(0)
    return re.sub(r"\{(\w+)\}", rep, template)

def _project_tree(repo_path: str) -> str:
    EXCLUDE = {"node_modules", ".git", "dist", "build", ".next", "coverage", ".cache"}
    try:
        entries = []
        for e in sorted(os.scandir(repo_path), key=lambda e: (not e.is_dir(), e.name)):
            if e.name.startswith(".") or e.name in EXCLUDE:
                continue
            entries.append(e.name + ("/" if e.is_dir() else ""))
        return "\n".join(entries) or "(empty)"
    except Exception:
        return "(unavailable)"

_CFG_FILES = [
    ".babelrc", ".babelrc.js", "babel.config.js", "babel.config.ts", "babel.config.json",
    "webpack.config.js", "webpack.config.ts",
    "jest.config.js", "jest.config.ts", "jest.config.json",
    ".eslintrc", ".eslintrc.js", ".eslintrc.json", ".eslintrc.yml",
    "eslint.config.js", "eslint.config.ts", "eslint.config.mjs",
    "tsconfig.json", "vite.config.js", "vite.config.ts",
    "rollup.config.js", "rollup.config.ts",
    "prettier.config.js", ".prettierrc.js",
    "vitest.config.js", "vitest.config.ts",
]

def _config_refs(repo_path: str) -> str:
    parts = []
    for fn in _CFG_FILES:
        fp = os.path.join(repo_path, fn)
        if not os.path.exists(fp):
            continue
        try:
            with open(fp, encoding="utf-8", errors="ignore") as f:
                parts.append(f"### {fn}\n{f.read(3000).strip()}")
        except Exception:
            pass
    return "\n\n".join(parts) or "(no config files found)"

def _import_lines(repo_path: str) -> str:
    EXT = {".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs"}
    EXCL = {"node_modules", ".git", "dist", "build", ".next", "coverage"}
    pat = re.compile(
        r"^[ \t]*(?:import\s|export\s.*from\s"
        r"|(?:const|let|var)\s+.*=\s*require\s*\("
        r"|require\s*\(|.*=\s*await\s+import\s*\().*$",
        re.MULTILINE,
    )
    parts, total, fc = [], 0, 0
    for dp, dirs, files in os.walk(repo_path):
        if fc >= 200 or total >= 600: break
        dirs[:] = [d for d in dirs if d not in EXCL]
        for fn in files:
            if os.path.splitext(fn)[1] not in EXT: continue
            fp = os.path.join(dp, fn)
            try:
                with open(fp, encoding="utf-8", errors="ignore") as f:
                    src = f.read()
            except Exception:
                continue
            ms = pat.findall(src)
            if not ms: continue
            rem = 600 - total
            lines = [m.strip() for m in ms[:rem]]
            parts.append(f"### {os.path.relpath(fp, repo_path)}\n" + "\n".join(lines))
            total += len(lines); fc += 1
            if fc >= 200 or total >= 600: break
    return "\n\n".join(parts) or "(no import statements found)"

def run_llm(repo_path: str, model: str) -> Tuple[List[str], List[str], bool]:
    pkg = parse_package_json(repo_path)
    runtime_deps = pkg.get("dependencies", {})
    if not runtime_deps:
        return [], [], False

    template, params = _load_prompt_template()

    def fmt_rule(r):
        ls = r.splitlines()
        if not ls: return ""
        out = f"- {ls[0]}"
        for l in ls[1:]: out += f"\n  {l}"
        return out

    variables = {
        **params,
        "dependencies":       "\n".join(f"- {k}: {v}" for k, v in runtime_deps.items()),
        "dev_dependencies":   "(not provided — runtime dependencies only are evaluated in this analysis)",
        "extra_dependencies": "(not provided — runtime dependencies only are evaluated in this analysis)",
        "scripts":            "\n".join(f'  "{k}": "{v}"' for k, v in pkg.get("scripts", {}).items()) or "(none)",
        "project_tree":       _project_tree(repo_path),
        "source_code":        _import_lines(repo_path),
        "config_references":  _config_refs(repo_path),
        "language_rules":     "\n".join(fmt_rule(r) for r in params.get("language_rules", [])),
    }
    prompt = _build_prompt(template, variables)
    try:
        # /api/chat を使うことで Ollama が各モデルの instruction 形式を自動適用する
        resp = requests.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": [{"role": "user", "content": prompt}],
                "stream": False,
                "format": "json",
                **({"think": False} if model.startswith("qwen3") else {}),
                "options": {"temperature": 0, "num_predict": 8000, "num_ctx": 16384},
            },
            timeout=LLM_TIMEOUT,
        )
        resp.raise_for_status()
        raw_resp = resp.json()
        text = raw_resp.get("message", {}).get("content", "")
        print(f"  [llm:{model}] eval_count={raw_resp.get('eval_count')} prompt_eval_count={raw_resp.get('prompt_eval_count')}")
    except Exception as e:
        print(f"  [llm:{model}] API error: {e}"); return [], [], False

    text = re.sub(r"<think>.*?</think>", "", text, flags=re.DOTALL)
    s, e = text.find("{"), text.rfind("}")
    if s == -1 or e == -1 or e <= s:
        print(f"  [llm:{model}] no JSON found | full_response={repr(text[:500])}")
        return [], [], False
    try:
        r = json.loads(text[s:e+1])
    except json.JSONDecodeError as ex:
        print(f"  [llm:{model}] JSON parse error: {ex}"); return [], [], False

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
        "depcheck_unused_dep": [], "depcheck_unused_dev_dep": [], "depcheck_success": False,
        "knip_unused_dep":     [], "knip_unused_dev_dep":     [], "knip_success":     False,
        "llama_unused_dep":    [], "llama_missing_dep":    [], "llama_success":    False,
        "qwen_unused_dep":     [], "qwen_missing_dep":     [], "qwen_success":     False,
        "deepseek_unused_dep": [], "deepseek_missing_dep": [], "deepseek_success": False,
    }

def analyze_repo(owner: str, repo: str) -> Dict[str, Any]:
    repo_path = os.path.join(CLONES_DIR, f"{owner}-{repo}")
    print(f"\n{'='*60}")
    print(f"[{owner}/{repo}]")

    # clone
    if os.path.exists(repo_path):
        shutil.rmtree(repo_path, ignore_errors=True)
    try:
        print(f"  Cloning ...")
        Repo.clone_from(f"https://github.com/{owner}/{repo}", repo_path)
    except Exception as e:
        print(f"  [error] clone failed: {e}")
        return _empty(owner, repo, error=str(e))

    pkg = parse_package_json(repo_path)
    row: Dict[str, Any] = {
        "repo": f"{owner}/{repo}",
        "error": None,
        "all_dep":     list(pkg.get("dependencies", {}).keys()),
        "all_dev_dep": list(pkg.get("devDependencies", {}).keys()),
    }

    for label, fn in [
        ("depcheck", lambda: run_depcheck(repo_path)),
        ("knip",     lambda: run_knip(repo_path)),
        ("llama",    lambda: run_llm(repo_path, OLLAMA_MODEL_LLAMA)),
        ("qwen",     lambda: run_llm(repo_path, OLLAMA_MODEL_QWEN)),
        ("deepseek", lambda: run_llm(repo_path, OLLAMA_MODEL_DEEPSEEK)),
    ]:
        t0 = time.time()
        ret = fn()
        elapsed = time.time() - t0

        if label in ("depcheck", "knip"):
            dep, dev_dep, ok = ret
            print(f"  {label:<10}: {len(dep)} unused_dep, {len(dev_dep)} unused_dev_dep  ({elapsed:.1f}s) ok={ok}")
            row[f"{label}_unused_dep"]     = dep
            row[f"{label}_unused_dev_dep"] = dev_dep
            row[f"{label}_success"]        = ok
        else:
            dep, missing, ok = ret
            print(f"  {label:<10}: {len(dep)} unused_dep  ({elapsed:.1f}s) ok={ok}")
            row[f"{label}_unused_dep"]  = dep
            row[f"{label}_missing_dep"] = missing
            row[f"{label}_success"]     = ok

    # clone 削除
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
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--skip",  type=int, default=0)
    parser.add_argument("--output", default=RESULTS_CSV)
    args = parser.parse_args()

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
        pd.DataFrame(rows).to_csv(args.output, index=False)
        print(f"  Saved ({len(rows)} done)")

    print(f"\nFinished. Results: {args.output}")

if __name__ == "__main__":
    main()

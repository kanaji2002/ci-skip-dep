"""
deepseek-coder:6.7b-instruct の挙動調査スクリプト
Ollama が起動している状態で実行する。

実行方法:
    python3 debug_deepseek.py 2>&1 | tee debug_deepseek_output.txt
"""

import json
import requests
import time

OLLAMA_URL = "http://localhost:11434"
MODEL = "deepseek-coder:6.7b-instruct"

SEPARATOR = "=" * 60

def ask(label: str, prompt: str, use_format_json: bool = False, system: str = None):
    print(f"\n{SEPARATOR}")
    print(f"[Test: {label}]")
    print(f"  format=json: {use_format_json}")
    print(f"  prompt length: {len(prompt)} chars")
    print(f"  prompt preview:\n    {repr(prompt[:200])}")

    payload = {
        "model": MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0, "num_predict": 2000, "num_ctx": 4096},
    }
    if use_format_json:
        payload["format"] = "json"
    if system:
        payload["system"] = system

    t0 = time.time()
    try:
        resp = requests.post(f"{OLLAMA_URL}/api/generate", json=payload, timeout=120)
        resp.raise_for_status()
        data = resp.json()
    except Exception as e:
        print(f"  ERROR: {e}")
        return

    elapsed = time.time() - t0
    text = data.get("response", "")

    print(f"  elapsed: {elapsed:.1f}s")
    print(f"  eval_count (生成トークン数): {data.get('eval_count')}")
    print(f"  prompt_eval_count (入力トークン数): {data.get('prompt_eval_count')}")
    print(f"  done_reason: {data.get('done_reason')}")
    print(f"  response length: {len(text)} chars")
    print(f"  full response:\n{text}")

    # JSON 抽出を試みる
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e != -1 and e > s:
        try:
            parsed = json.loads(text[s:e+1])
            print(f"  >> JSON parsed OK: keys={list(parsed.keys())}")
        except json.JSONDecodeError as ex:
            print(f"  >> JSON parse FAILED: {ex}")
    else:
        print(f"  >> No JSON found in response")


# ---------------------------------------------------------------------------
# Test 1: 最小プロンプト (JSON なし、モデルの基本挙動確認)
# ---------------------------------------------------------------------------
ask(
    label="1. 最小プロンプト (挙動確認)",
    prompt="What is 1 + 1?",
)

# ---------------------------------------------------------------------------
# Test 2: JSON を返すよう指示（format=json なし）
# ---------------------------------------------------------------------------
ask(
    label="2. JSON指示 (format=json なし)",
    prompt='Return ONLY this JSON object with no explanation:\n{"result": "hello"}',
)

# ---------------------------------------------------------------------------
# Test 3: JSON を返すよう指示（format=json あり）
# ---------------------------------------------------------------------------
ask(
    label="3. JSON指示 (format=json あり)",
    prompt='Return ONLY this JSON object with no explanation:\n{"result": "hello"}',
    use_format_json=True,
)

# ---------------------------------------------------------------------------
# Test 4: 実際のタスク（短縮版）format=json なし
# ---------------------------------------------------------------------------
short_prompt = """You are analyzing a JavaScript project to find unused packages.

## Declared Dependencies
### dependencies
- express: ^4.18.0
- lodash: ^4.17.21

### dev_dependencies
- jest: ^29.0.0
- eslint: ^8.0.0

## Source Files
### index.js
const express = require('express')
const app = express()

## Output
Return ONLY valid JSON:
{
  "unused_dependencies": [],
  "unused_dev_dependencies": [],
  "missing_dependencies": []
}"""

ask(
    label="4. 実タスク短縮版 (format=json なし)",
    prompt=short_prompt,
)

# ---------------------------------------------------------------------------
# Test 5: 実際のタスク（短縮版）format=json あり
# ---------------------------------------------------------------------------
ask(
    label="5. 実タスク短縮版 (format=json あり)",
    prompt=short_prompt,
    use_format_json=True,
)

# ---------------------------------------------------------------------------
# Test 6: system prompt を使う
# ---------------------------------------------------------------------------
ask(
    label="6. system prompt 付き",
    prompt=short_prompt,
    use_format_json=True,
    system="You are a JSON API. Always respond with valid JSON only. Never include explanations.",
)

# ---------------------------------------------------------------------------
# Test 7: instruct 形式のプロンプト (### Instruction / ### Response)
# ---------------------------------------------------------------------------
ask(
    label="7. instruct 形式 (deepseek-coder用フォーマット)",
    prompt=(
        "### Instruction:\n"
        "List the unused npm packages from the following project.\n\n"
        "dependencies: express, lodash\n"
        "dev_dependencies: jest, eslint\n\n"
        "The source code only uses: express\n\n"
        "Return ONLY valid JSON like this:\n"
        '{"unused_dependencies": ["lodash"], "unused_dev_dependencies": ["jest", "eslint"], "missing_dependencies": []}\n\n'
        "### Response:\n"
    ),
    use_format_json=True,
)

print(f"\n{SEPARATOR}")
print("Debug complete.")

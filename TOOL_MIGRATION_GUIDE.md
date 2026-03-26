# ツール移行ガイド: depcheck → 別ツールへの切り替え手順

## 概要

`data-curation-depc`（depcheck版）をベースに、別の依存関係チェックツールを使う新しいディレクトリを作る際の手順と変更箇所をまとめる。

既存の事例:
- `data-curation-depc` → `data-curation-knip`（knip版・第1世代）
- `data-curation-depc` → `data-curation-knip2`（knip版・第2世代）

---

## 手順

### 1. コピー

```bash
cp -r data-curation-depc data-curation-<TOOL_NAME>
```

### 2. 変更が必要なファイル一覧

#### (A) `step2_data_curation/dc2_depcheck.py` → `dc2_<TOOL_NAME>.py`

- ファイルを削除 → 新ツール用のファイルを作成（または別バージョンからコピー）
- 変更ポイント:
  - ファイル名・モジュールコメントを更新
  - ツール実行コマンド（`npx_command`）を差し替え
  - 出力フォーマットのパースロジックを差し替え
  - 関数名: `run_depcheck(...)` → `run_<TOOL_NAME>(...)`, `run_depcheck_for_commits(...)` → `run_<TOOL_NAME>_for_commits(...)`

**depcheck の出力形式（テキスト）:**
```
Unused devDependencies
* パッケージ名

Unused dependencies
* パッケージ名

Missing dependencies
* パッケージ名
```

**knip の出力形式（JSON）:**
```json
{
  "issues": [
    {
      "file": "package.json",
      "dependencies": [{"name": "パッケージ名"}],
      "devDependencies": [{"name": "パッケージ名"}],
      "unlisted": [{"name": "パッケージ名"}]
    }
  ]
}
```
コマンド: `npx knip --reporter json --include dependencies,devDependencies,unlisted`

---

#### (B) `step2_data_curation/__init__.py`

```python
# 変更前
from .dc2_depcheck import run_depcheck_for_commits, run_depcheck

# 変更後（knip の例）
from .dc2_knip import run_knip_for_commits, run_knip
```

---

#### (C) `config.py`

| 変更箇所 | 変更前 | 変更後（knip の例） |
|:---|:---|:---|
| `ROOT_DIR` | `"data_dependency_waste_project_v2/"` | `"data_dependency_waste_project_knip2/"` |
| コメント | `# depcheck解析時に除外するパッケージ名` | `# knip解析時に除外するパッケージ名` |
| `DEPCHECK_SPECIALS` | ブロックごと存在 | **削除**（knipは`--specials`不要） |

---

#### (D) `pipeline_main.py`

| 変更箇所 | 変更前 | 変更後（knip の例） |
|:---|:---|:---|
| import | `from step2_data_curation.dc2_depcheck import run_depcheck_for_commits` | `from step2_data_curation.dc2_knip import run_knip_for_commits` |
| docstring | `3. depcheck →` | `3. Knip →` |
| Step 2 コメント | `# Step 2: depcheck実行` | `# Step 2: Knip実行` |
| Step 2 print | `"Running depcheck..."` | `"Running Knip..."` |
| 変数名 | `depcheck_df = run_depcheck_for_commits(...)` | `knip_df = run_knip_for_commits(...)` |
| 空チェック | `if depcheck_df is None...` | `if knip_df is None...` |
| エラーメッセージ | `"depcheck returned no results..."` | `"Knip returned no results..."` |

---

#### (E) `submit.sh`

| 変更箇所 | 変更前 | 変更後 |
|:---|:---|:---|
| `#SBATCH --output` パス | `.../data-curation-depc/...` | `.../data-curation-<TOOL_NAME>/...` |
| `#SBATCH --error` パス | `.../data-curation-depc/...` | `.../data-curation-<TOOL_NAME>/...` |
| `OUT_DIR` | `.../data-curation-depc/...` | `.../data-curation-<TOOL_NAME>/...` |
| `cd` | `cd .../data-curation-depc` | `cd .../data-curation-<TOOL_NAME>` |

---

#### (F) `CLAUDE.md`

- DC2 の説明を「DepCheck」→ 新ツール名に変更
- ディレクトリ構造の `dc2_depcheck.py` → `dc2_<TOOL_NAME>.py`
- 出力データディレクトリ名 `data_dependency_waste_project_v2/` → `data_dependency_waste_project_<TOOL_NAME>/`
- 実行環境の `depcheck 1.4.7` → 新ツール名
- 実行方法の `submit2.sh` → `submit.sh`（knip2の場合）

---

---

## depcheck vs knip vs llama の主な違い

| 項目 | depcheck | knip | llama (Ollama) |
|:---|:---|:---|:---|
| 出力形式 | テキスト（`* パッケージ名` のリスト） | JSON（`--reporter json`） | JSON（LLM が生成） |
| コマンド | `npx depcheck --specials=...` | `npx knip --reporter json ...` | `POST http://localhost:11434/api/generate` |
| タイムアウト | 120秒 | 180秒 | 300秒 |
| Node.js 必要 | Yes | Yes | **No** |
| GPU 必要 | No | No | **Yes**（推奨） |
| 精度 | 高（AST解析） | 高（AST解析） | 低〜中（import文の文字列照合） |
| 速度 | 速い | 普通 | 遅い |

---

## llama/Ollama 版の実装パターン (`data-curation-llama`)

### dc2_llama.py の構造

```
extract_imports(repo_path) → List[str]
  ↓ ソースファイルの import/require を正規表現で抽出
run_llama(repo_path) → (unused_dev_deps, unused_deps, missing_deps, success)
  ↓ package.json + import リストをプロンプトに埋め込み Ollama API に投げる
analyze_commit(repo_path, sha) → Dict
run_llama_for_commits(owner, repo) → pd.DataFrame
```

### Ollama API 呼び出し

```python
requests.post(
    "http://localhost:11434/api/generate",
    json={"model": "llama3.2", "prompt": prompt, "stream": False},
    timeout=300
)
```

### submit.sh の GPU 対応ポイント

```bash
#SBATCH --gres=gpu:1
#SBATCH --mem=32G

# Ollama 起動（バックグラウンド）
ollama serve &
OLLAMA_PID=$!
sleep 5
# 起動確認
curl -s http://localhost:11434/api/tags
# モデルプル
ollama pull llama3.2
# 終了時に停止
kill "${OLLAMA_PID}"
```

### config.py に追加する項目

```python
OLLAMA_MODEL = "llama3.2"
OLLAMA_URL = "http://localhost:11434"
```

---

## depcheck vs knip の主な違い

| 項目 | depcheck | knip |
|:---|:---|:---|
| 出力形式 | テキスト（`* パッケージ名` のリスト） | JSON（`--reporter json`） |
| コマンド | `npx depcheck --specials=...` | `npx knip --reporter json --include dependencies,devDependencies,unlisted` |
| タイムアウト | 120秒 | 180秒（少し遅い） |
| `--specials` オプション | 必要（`DEPCHECK_SPECIALS`） | 不要 |
| 未使用dep | `Unused dependencies` セクション | JSON の `issues[].dependencies` |
| 未使用devDep | `Unused devDependencies` セクション | JSON の `issues[].devDependencies` |
| 不足dep | `Missing dependencies` セクション | JSON の `issues[].unlisted` |
| フォールバック | なし | テキストパース関数 `parse_knip_text_output()` |

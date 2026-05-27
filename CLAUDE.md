# プロジェクト概要

JavaScript/npm エコシステムにおける「未使用依存関係の更新」に起因する CI の浪費を定量化する研究。
関連研究「Dependency-Induced Waste in Continuous Integration」(Dep-sCImitar) の手法を再現・拡張し、**Python・C#・Rust へも応用する**。

## 関連研究の実施条件 (再現対象)

### プロジェクト選定 (PS1-PS8)

Step1  Project Selection

- PS1 : Number of star
    - JavaScript:10 star 
    - Python:10 star
    - Rust:5 star
    - C#:5 star        
- PS2 : Over 10 commit (2024/3/1 - 2026/3/1). 
- PS3  : Having  `.github/workflows`                  
- PS4 : Over 10 CI data.                                        
- PS5 : Is there dependencies ( package managment system)
    - JS : Have a  `package.json`It’s mean it is  npm package. 
    - Python : .toml
    - Rust : Cargo.toml
    - C# : .csproj
- PS6 : Size < 10 MB
- PS7 : Using test tool
    - JS : Have a npm test (non-dummy)
    - Python  : Have a pytest (reference in pyproject.toml)
    - Rust :  Is there tests/ or test/ directory (checked via Git Trees API)
    - C# :  Refer to  xUnit in .csproj
- PS8 : Clone and execute test, then calculate coverage
    - line coverage ≧ 70%
    - JS : `npm install` → `npx nyc npm test` (nyc が package.json に存在する場合のみ対象)
    - Python  : `pytest --cov=. --cov-report=json` (pytest-cov) → coverage.json を解析
    - Rust :  `cargo tarpaulin --out Json` (Singularity: rust-tarpaulin.sif) → tarpaulin-report.json を解析
    - C# :  `dotnet restore` → `dotnet test --collect:"XPlat Code Coverage"` (Singularity: dotnet-sdk8.sif, coverlet) → coverage.cobertura.xml を解析 ※ net8.x のみ対象 (net9+ は除外)
    


















### データ精査 (DC1-DC3)

#### DC1: 依存バージョン更新コミットの抽出

依存管理ファイル**のみ**を修正し、依存バージョン更新だけを含むコミットを抽出。ソースコード・README 等の変更を含むコミットは除外。

| 言語 | 対象ファイル | 関連研究の件数 |
|:---|:---|---:|
| JavaScript | `package.json` (および `package-lock.json`) | 121,453 commits (1,854 proj) |
| Python | `pyproject.toml` | — (本研究独自) |
| C# | `.csproj` | — (本研究独自) |
| Rust | `Cargo.toml` | — (本研究独自) |

#### DC2: 未使用依存の検知

各コミット SHA にチェックアウトして未使用依存を検知する。

| 言語 | 使用ツール |
|:---|:---|
| JavaScript | depcheck + knip + LLM 3モデル (llama3.1:8b / qwen3.5:4b / deepseek-coder:6.7b-instruct) |
| Python | LLM 3モデルのみ (depcheck・knip は JS 専用) |
| C# | LLM 3モデルのみ |
| Rust | LLM 3モデルのみ |

関連研究 (JS) の DC2 件数: 49,731 commits

#### DC3: CI データの取得

GitHub API (Check Runs) でビルドステータス・実行時間を取得。**コミット本体と親コミットの両方**を対象。全言語で共通実装 (`RQ3/Code/common/`)。

| 言語 | 関連研究の件数 |
|---:|---:|
| JavaScript | 20,743 commits (1,487 proj) |
| Python / C# / Rust | — (本研究独自) |

> **本研究での実行順変更**: 関連研究は DC1→DC2→DC3 の順だが、本研究では **DC1→DC2(CI取得)→DC3(モデル実行)** の順で実行する。DC3 は DC2 で CI データが取得できたコミットのみを対象とする。

### スキップ判定 (ci-check)

```
is_skippable = (upgraded_dep ∈ unused_dep ∪ unused_dev_dep) AND (parent_build_status == 'success')
```

条件は「更新された依存が未使用」かつ「**親**コミットのCIが成功」の2つ。対象コミット自身のCIステータスはスキップ判定に含まれない。

### 関連研究の主要結果

- 未使用依存によるビルド: 全体の 50.19%
- 検出率 (Recall): 83%
- 適合率 (Precision): 94%
- 総ビルド浪費時間: 3,427 時間
- 削減可能時間: 2,342 時間 (68.34%)

## 本研究の設定 (関連研究との差異)

| 項目 | 関連研究 | 本研究 |
|:---|:---|:---|
| 取得期間 | 2020-01-01 ~ 2022-12-31 | **2024-03-01 ~ 2026-03-01** |
| PS4 | なし | GitHub Actions 実行履歴 10件以上 (追加) |
| PS5 の方法 | クローンして確認 | GitHub API で確認 (結果同等) |
| その他 | — | 同一 |

### 本研究の PS 結果件数 (JS, 2026-03-03 取得)

| ステップ | 条件 | 本研究の件数 |
|:---|:---|---:|
| PS1 | JavaScript プロジェクト抽出 (10 star 以上) | 294,477 |
| PS2 | 期間内に 10 コミット以上 | 46,249 |
| PS3 | `.github/workflows` の存在 | 22,716 |
| PS4 | GitHub Actions 実行履歴 10件以上 | 18,286 |
| PS5 | `package.json` の存在 (npm プロジェクト) | — |

## 本研究における DC3 の拡張 (5モデル並列検出)

関連研究は depcheck のみを使用していたが、本研究では以下の **5モデル**を各コミットに対して並列実行する。

| モデル | 手法 | 出力カラム |
|:---|:---|:---|
| **depcheck** | `depcheck . --json` (サブプロセス) | `depcheck_unused_dep`, `depcheck_unused_dev_dep` |
| **knip** | `npx --yes knip --reporter json` (サブプロセス) | `knip_unused_dep`, `knip_unused_dev_dep` |
| **llama** | Ollama HTTP API (`llama3.1:8b`) | `llama_unused_dep`, `llama_unused_dev_dep`, `llama_missing_dep` |
| **qwen** | Ollama HTTP API (`qwen3.5:4b`) | `qwen_unused_dep`, `qwen_unused_dev_dep`, `qwen_missing_dep` |
| **deepseek** | Ollama HTTP API (`deepseek-coder:6.7b-instruct`) | `deepseek_unused_dep`, `deepseek_unused_dev_dep`, `deepseek_missing_dep` |

各モデルに対して独立に `{model}_dep_status` と `{model}_is_skippable` が計算される。

## RQ1・RQ3.1 における実行依存・開発依存の扱い

### Step1 (検知フェーズ): 依存の分類定義

| | JS (RQ1) | Python (RQ3.1) | C# (RQ3.1) | Rust (RQ3.1) |
|:---|:---|:---|:---|:---|
| **実行依存** | `dependencies` | `[tool.poetry.dependencies]`<br>`[project.dependencies]` | `PackageReference`<br>(PrivateAssets なし) + `PackageVersion` | `[dependencies]` |
| **開発依存** | `devDependencies` | `[tool.poetry.dev-dependencies]`<br>`[tool.poetry.group.*]`<br>`[project.optional-dependencies]`<br>(dev/test/lint/docs グループ) | `PackageReference`<br>(PrivateAssets=all) | `[dev-dependencies]` |
| **extra** | `peerDependencies` | `[project.optional-dependencies]`<br>(非 dev グループ) | なし | `[build-dependencies]` |

Step1 では実行依存・開発依存の**両方**を検知対象とし、それぞれ `{model}_unused_dep` / `{model}_unused_dev_dep` に格納する。  
extra は LLM プロンプトに渡されるが `unused_extra_dependencies` として返却され、パース時に読み捨てられるため**スキップ判定・検証いずれにも使われない**。

検知ツール:

| | 実行依存 | 開発依存 |
|:---|:---|:---|
| **JS (RQ1)** | depcheck + knip + 3 LLM | depcheck + knip + 3 LLM |
| **Python / C# / Rust (RQ3.1)** | 3 LLM のみ | 3 LLM のみ |

### Step2 (精度検証フェーズ): 削除対象は実行依存のみ

**全言語共通**: `unused_dep` (実行依存) のみ削除・検証し、`unused_dev_dep` (開発依存) は検証対象から除外する。

```python
to_remove = list(dict.fromkeys(unused_dep))  # runtime deps のみ
# 開発依存は CLI ツール等が scripts 経由で使われている可能性があり誤検知判定が困難なため除外
```

### フェーズ間の整合性

| フェーズ | 実行依存 | 開発依存 |
|:---|:---|:---|
| **DC is_skippable** | 使われる | **使われる** (`unused_dep ∪ unused_dev_dep`) |
| **RQ step2 精度検証** | 削除・検証する | **しない** |

DC のスキップ判定では開発依存の未使用も `is_skippable=True` に寄与するが、step2 の Precision/Recall 計算では開発依存の検知精度は評価されない。

## ディレクトリ構造

```
research/
├── PS/                                  # プロジェクト選定 (PS1-PS8)
│   ├── js/                              # JavaScript
│   │   ├── ps2_ps4_filter.py            # PS2/PS3/PS4 フィルタ
│   │   ├── ps5_filter.py                # PS5: package.json 確認
│   │   ├── ps6_filter.py                # PS6: サイズ < 10MB
│   │   ├── ps7_filter.py                # PS7: npm test 存在確認
│   │   └── ps8_filter.py                # PS8: nyc カバレッジ ≥ 70%
│   ├── python/                          # Python (ps1〜ps8_filter.py)
│   ├── csharp/                          # C# (ps1〜ps8_filter.py)
│   └── rust/                            # Rust (ps1〜ps8_filter.py)
│
├── DC/
│   └── data-curation-all/               # JS 向け DC パイプライン
│       ├── config.py                    # 設定 (期間・トークン・パス)
│       ├── pipeline_main.py             # メインパイプライン
│       ├── submit.sh                    # Slurm ジョブスクリプト
│       ├── prompts/                     # LLM プロンプト (template.md + params/*.json)
│       ├── step1_project_selection/
│       │   ├── select_projects.py       # CSV からプロジェクト読み込み
│       │   └── filter_projects.py       # PS5 フィルタ (package.json 確認)
│       ├── step2_data_curation/
│       │   ├── dc1_extract_commits.py   # DC1: 依存更新コミット抽出
│       │   ├── dc2_ci_data.py           # DC2: GitHub Check Runs API で CI 取得
│       │   └── dc3_dependency_models.py # DC3: 5モデル並列実行
│       ├── step3_skip_analysis/
│       │   └── ci_check.py              # スキップ判定 + 最終データセット作成
│       ├── utils/
│       │   ├── git_utils.py             # clone / checkout / cleanup
│       │   └── github_api.py            # Check Runs API ラッパー
│       └── data_dependency_waste_project/  # 出力 (実行後に生成)
│           └── {csv_stem}/
│               ├── commits/             # git log ダンプ
│               ├── filtered/            # PS5 フィルタ済み CSV
│               ├── one_dependency_version_change_commits/  # DC1 結果
│               ├── ci_data/             # DC2 結果
│               ├── dependency_data/     # DC3 結果
│               └── datasets/
│                   └── final_dataset.csv
│
├── RQ1/                                 # RQ1: JS 未使用依存検知精度評価
│   ├── step1_detect/
│   │   └── run.py                       # 5モデル検知 (depcheck/knip/3LLM)
│   ├── step2_verify/
│   │   └── run.py                       # 削除検証 (npm uninstall + npm test)
│   ├── Analyze/                         # 集計・分析スクリプト
│   └── output/
│       ├── step1_results.csv
│       └── step2_results.csv
│
├── RQ2/                                 # RQ2: 分析
│   └── Analyze/
│       └── analyze_job_all.py
│
├── RQ3/
│   ├── RQ3-1/                           # RQ3-1: 多言語 未使用依存検知精度評価
│   │   └── Code/
│   │       ├── python/
│   │       │   ├── step1_detect/run.py  # LLM 3モデル検知
│   │       │   ├── step2_verify/run.py  # 削除検証 (pip uninstall + pytest)
│   │       │   └── output/step{1,2}_results.csv
│   │       ├── csharp/
│   │       │   ├── step1_detect/run.py  # LLM 3モデル検知
│   │       │   ├── step2_verify/run.py  # 削除検証 (dotnet remove + dotnet test)
│   │       │   └── output/step{1,2}_results.csv
│   │       └── rust/
│   │           ├── step1_detect/run.py  # LLM 3モデル検知
│   │           ├── step2_verify/run.py  # 削除検証 (cargo remove + cargo test via tarpaulin)
│   │           └── output/step{1,2}_results.csv
│   ├── RQ3-2/                           # RQ3-2: 多言語 DC パイプライン
│   │   ├── common/                      # 全言語共通実装
│   │   │   ├── dc2_ci_data.py           # CI データ取得
│   │   │   ├── dc3_dependency_models.py # LLM 3モデル検知
│   │   │   ├── ci_check.py              # スキップ判定
│   │   │   ├── git_utils.py
│   │   │   ├── github_api.py
│   │   │   └── prompts/                 # LLM プロンプト (template.md + params/*.json)
│   │   ├── python/
│   │   │   ├── config.py
│   │   │   ├── dc1_extract_commits.py   # Python 向け DC1
│   │   │   ├── pipeline_main.py
│   │   │   └── data_dependency_waste_project/
│   │   ├── csharp/
│   │   │   ├── config.py
│   │   │   ├── dc1_extract_commits.py   # C# 向け DC1
│   │   │   ├── pipeline_main.py
│   │   │   └── data_dependency_waste_project/
│   │   └── rust/
│   │       ├── config.py
│   │       ├── dc1_extract_commits.py   # Rust 向け DC1
│   │       ├── pipeline_main.py
│   │       └── data_dependency_waste_project/
│   └── Analyze/                         # RQ3-1/RQ3-2 集計スクリプト
│       ├── RQ3-1_analyze_job_{python,csharp,rust}.py
│       └── RQ3-2_analyze_job_{python,csharp,rust}.py
│
└── containers/                          # Singularity コンテナ
    ├── dotnet-sdk8.sif                  # C# (dotnet test)
    └── rust-tarpaulin.sif               # Rust (cargo tarpaulin)
```

DC パイプライン入力 CSV は `--repo-list` で指定。JS のベースディレクトリ:  
`/work/rintaro-k/research/PS/js/ps4_results_100row_each/`

## 実行環境

- Slurm クラスタ (`isgpu4h200_week` パーティション, GPU x2)
- Python 3.10 (pyenv, 仮想環境 `py3`)
- Node.js 20 (nvm) + depcheck 1.4.7 + npx knip (都度インストール)
- Ollama (Singularity コンテナ) — llama3.1:8b + qwen3.5:4b + deepseek-coder:6.7b-instruct

## 実行方法

```bash
# Slurm ジョブとして投入 (--repo-list 必須)
# 旧方式: ps4_results_N.csv を 1 ファイルずつ処理 (PS5 フィルタあり)
sbatch submit.sh --repo-list ps4_results_1.csv
sbatch submit.sh --repo-list ps4_results_1.csv --stage pipeline --limit 50
sbatch submit.sh --repo-list ps4_results_1.csv --stage filter   # PS5 フィルタのみ

# 新方式: PS5 済み CSV を --batch-index で 100 件ずつ処理 (PS5 フィルタをスキップ)
sbatch submit.sh --repo-list /work/rintaro-k/research/PS/js/ps5_filtered.csv --batch-index 0
sbatch submit.sh --repo-list /work/rintaro-k/research/PS/js/ps5_filtered.csv --batch-index 1

# ローカルテスト
export NVM_DIR="$HOME/.nvm" && source "$NVM_DIR/nvm.sh"
export REPO_LIST_PATH=/work/rintaro-k/research/PS/js/ps4_results_100row_each/ps4_results_1.csv
python3 pipeline_main.py --limit 5

# 最終データセットのみ再作成
python3 pipeline_main.py --repo-list /path/to/repos.csv --final-only
```

## ジョブ結果の解析プロトコル

- 出力フォーマットは `analyze_job.py` が固定しているため、追加の要約・整形・補足は不要
- ただし出力の後に「注意点」や「考察」がある場合は簡潔に追記してよい
- `--dataset-only` オプションでデータセット統計のみ表示可能

## 既知の課題

- CIデータの取得率が低い (parent_build_status=unknown が 85%)。GitHub API の仕様上、古いビルドデータが保持されない場合がある
- GitHub トークンが 1 つのみ (config.py)。大量処理時はレート制限に注意

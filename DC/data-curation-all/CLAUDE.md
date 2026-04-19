# プロジェクト概要

JavaScript/npm エコシステムにおける「未使用依存関係の更新」に起因する CI の浪費を定量化する研究。
関連研究「Dependency-Induced Waste in Continuous Integration」(Dep-sCImitar) の手法を再現・拡張する。

## 関連研究の実施条件 (再現対象)

### プロジェクト選定 (PS1-PS5)

| ステップ | 条件 | 関連研究の件数 |
|:---|:---|---:|
| PS1 | GitHub Search API で JavaScript プロジェクトを抽出 (`language:JavaScript stars:>=10`、created日付→stars範囲→pushed日付の3段階再帰分割) | 261,739 |
| PS2 | 期間内に 10 コミット以上の活動実績 | 100,811 |
| PS3 | GitHub Actions 採用 (`.github/workflows/*.yml` の存在) | 16,226 |
| PS4 | GitHub Actions 実行履歴が 10件以上 | (本研究追加) |
| PS5 | npm 採用 (`package.json` の存在) | 13,991 |

### データ精査 (DC1-DC3) ※関連研究の定義

| ステップ | 条件 | 関連研究の件数 |
|:---|:---|---:|
| DC1 | `package.json` (および `package-lock.json`) **のみ**を修正し、依存関係のバージョン更新だけを含むコミットを抽出。ソースコード・README 等の変更を含むコミットは除外 | 121,453 (1,854 proj) |
| DC2 | DepCheck で未使用依存を特定。各コミット SHA にチェックアウトして実行 | 49,731 |
| DC3 | GitHub API (Check Runs) でビルドステータス・実行時間を取得。コミット本体 + 親コミットの両方 | 20,743 (1,487 proj) |

> **本研究での実行順変更**: 関連研究は DC1→DC2→DC3 の順だが、本研究では **DC1→DC2(CI取得)→DC3(モデル実行)** の順で実行する。DC3はDC2でCIデータが取得できたコミットのみを対象とする。

### スキップ判定 (ci-check)

```
is_skippable = (upgraded_dep ∈ unused_dep ∪ unused_dev_dep) AND (parent_build_status == 'success')
```

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

## ディレクトリ構造

```
data-curation-all/
├── config.py                         # 設定 (期間・トークン・パス・モデル名)
├── pipeline_main.py                  # メインパイプライン
├── submit.sh                         # Slurm ジョブスクリプト (--repo-list 必須)
├── step1_project_selection/
│   ├── select_projects.py            # CSV からプロジェクト読み込み (filtered優先)
│   └── filter_projects.py            # PS5 フィルタリング (GitHub API で package.json 確認)
├── step2_data_curation/
│   ├── dc1_extract_commits.py        # 依存更新コミット抽出 (1依存・1挿入1削除のみ)
│   ├── dc2_ci_data.py                # GitHub Check Runs API でCI取得 (DC1出力を入力)
│   └── dc3_dependency_models.py      # 5モデル並列実行 (depcheck/knip/llama/qwen/deepseek、CI取得済みのみ)
├── step3_skip_analysis/
│   └── ci_check.py                   # 全モデルのスキップ判定 + 最終データセット作成
├── utils/
│   ├── git_utils.py                  # clone / checkout / cleanup
│   └── github_api.py                 # Check Runs API ラッパー
└── data_dependency_waste_project/     # 出力データ (実行後に生成)
    └── {csv_stem}/                   # CSV ファイル名ごとにサブディレクトリ
        ├── commits/                  # git log ダンプ
        ├── filtered/                 # PS5 フィルタ済み CSV
        ├── one_dependency_version_change_commits/  # DC1 結果 (JSON)
        ├── ci_data/                  # DC2 結果 (CI取得済みコミット JSON)
        ├── dependency_data/          # DC3 結果 (全5モデル入り JSON、CI取得できたコミットのみモデル結果あり)
        └── datasets/
            └── final_dataset.csv    # 最終データセット (全モデルのカラムを含む)
```

入力 CSV は実行時に `--repo-list` で指定する。ベースディレクトリ:
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

ユーザーがジョブID（数字）を提示して「解析して」「結果を見て」等と依頼した場合、**必ず以下のコマンドを実行**してその出力をそのまま提示する。

```bash
python3 /work/rintaro-k/research/analyze_job.py <job_id>
```

- 出力フォーマットは `analyze_job.py` が固定しているため、追加の要約・整形・補足は不要
- ただし出力の後に「注意点」や「考察」がある場合は簡潔に追記してよい
- `--dataset-only` オプションでデータセット統計のみ表示可能

## 既知の課題

- CIデータの取得率が低い (parent_build_status=unknown が 85%)。GitHub API の仕様上、古いビルドデータが保持されない場合がある
- GitHub トークンが 1 つのみ (config.py)。大量処理時はレート制限に注意

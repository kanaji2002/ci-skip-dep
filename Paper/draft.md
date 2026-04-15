# Dependency-Induced Waste in Continuous Integration: A Large-Scale Study of the JavaScript Ecosystem

著者: Rintaro Kato

---

## Abstract

（未記入）

---

## 1. Introduction

（未記入）

---

## 2. Related Work

（未記入）

---

## 3. 研究手法 (Methodology)

本研究のパイプラインは以下の3段階から構成される：
(1) プロジェクト選定 (Project Selection, PS)，
(2) データ精査 (Data Curation, DC)，
(3) スキップ分析 (Skip Analysis)．
各段階の概要を以下に述べる．

### 3.1 プロジェクト選定 (Project Selection)

JavaScriptエコシステムにおけるCIの浪費を定量化するために，
GitHubからJavaScriptプロジェクトを収集し，段階的なフィルタリングを適用した．
以下の表に各ステップと残存件数を示す．

| ステップ | 条件 | 件数 |
|:---:|:---|---:|
| PS1 | JavaScriptプロジェクト，10スター以上 (SEART) | 294,477 |
| PS2 | 対象期間内に10コミット以上 | 46,249 |
| PS3 | GitHub Actions採用 (`.github/workflows/*.yml` の存在) | 22,716 |
| PS4 | GitHub Actions実行履歴が10件以上 | 18,286 |
| PS5 | npm採用 (`package.json` の存在) | (集計中) |

**対象期間:** 2024年3月1日 〜 2026年3月1日

#### PS1 — 初期収集

SEART GitHub Search APIを用いて，10スター以上のJavaScriptリポジトリを取得した．
収集日は2026年3月3日であり，294,477件が得られた．

#### PS2 — 活動フィルタ

対象期間（2024-03-01〜2026-03-01）内に10コミット以上の活動実績があるプロジェクトのみを残した．
このフィルタにより，分析期間中に実際にメンテナンスされていたプロジェクトのみを対象とする．
結果として46,249件に絞り込まれた．

#### PS3 — GitHub Actions採用確認

各プロジェクトの `.github/workflows/` 配下にワークフローファイルが存在するかを確認し，
GitHub ActionsによるCIを採用していないプロジェクトを除外した．22,716件が残った．

#### PS4 — CI実行履歴の確認

GitHub ActionsがCI設定として存在するだけでなく，実際に実行されていることを確認するため，
ワークフロー実行履歴が10件以上あるプロジェクトのみを採用した．
このステップは先行研究 (Dep-sCImitar) にはなかった本研究独自の追加フィルタである．
結果として18,286件となった．

#### PS5 — npm採用確認

リポジトリルートに `package.json` が存在するかをGitHub APIで確認し，
npmパッケージマネージャを採用しているプロジェクトのみを対象とした．

---

### 3.2 データ精査 (Data Curation)

#### DC1 — 依存関係更新コミットの抽出

選定されたプロジェクトから，**純粋な依存関係更新コミット**を抽出した．
対象コミットの条件は以下のとおりである：

- 変更ファイルが `package.json` および/または `package-lock.json` のみ
- 変更された依存関係がちょうど1件（1挿入・1削除のみ）
- ソースファイル・テストファイル・ドキュメント等の変更を含まない

この厳格な条件により，各コミットを単一の依存関係バージョンアップに帰属させることができ，
スキップ判定の根拠を明確にできる．

先行研究 (Dep-sCImitar) では121,453コミット (1,854プロジェクト) が抽出されている．

**実装:** 各リポジトリを `git clone` してローカルで `git log` と `git diff` を実行し，
上記条件に合致するコミットのSHAおよびメタデータ（変更依存名・バージョン，タイムスタンプ等）をJSONで保存した．

#### DC2 — CIビルドデータの収集

DC1で抽出した各コミットに対して，GitHub Check Runs APIを用いてCIビルド情報を取得した．
取得項目は以下のとおりである：

- **コミット本体のビルドステータス** (`success` / `failure` / `unknown`)
- **親コミットのビルドステータス**（依存更新直前の状態）
- **ビルド実行時間**（秒単位）

GitHubのAPIは古いビルドデータを保持しない場合があり，
`parent_build_status = unknown` となるコミットが多く存在することが判明した（約85%）．
そのため，CIデータが取得できたコミットのみを以降の分析対象とした．

先行研究では20,743コミット (1,487プロジェクト) でCIデータが取得できている．

#### DC3 — 未使用依存関係の検出

DC2でCIデータを取得できた各コミットに対して，
依存関係が実際にプロジェクトで**使用されているか否か**を検出する．
本研究では，先行研究のdepcheck単一手法から拡張し，以下の**5つの検出モデル**を並列実行する．

| モデル | 手法 | 種別 |
|:---|:---|:---|
| **depcheck** | `depcheck . --json` (静的解析) | 静的解析 |
| **knip** | `npx --yes knip --reporter json` (静的解析) | 静的解析 |
| **llama** | Ollama HTTP API (`llama3.1:8b`) | LLM |
| **qwen** | Ollama HTTP API (`qwen3.5:4b`) | LLM |
| **deepseek** | Ollama HTTP API (`deepseek-coder:6.7b-instruct`) | LLM |

**静的解析ツール (depcheck, knip):**
各コミットのSHAにチェックアウトした後，ツールをサブプロセスとして実行し，
未使用の依存関係（`dependencies` および `devDependencies`）のリストをJSON形式で取得する．

- `depcheck`: ASTベースで全ソースファイルを解析し，importされていないパッケージを報告する
- `knip`: プロジェクト全体のクロスファイル解析を行い，未使用エクスポート・ファイル・依存を報告する

**LLMベース検出 (llama, qwen, deepseek):**
各コミット時点のソースファイルから `import` / `require` 文を正規表現で抽出し，
`package.json` に記載された依存関係と照合するようLLMに指示するプロンプトを構築して推論を実行する．
推論はHPCクラスタのGPU上でOllama経由のローカル実行を行い，外部APIへの依存を排除した．

各モデルの出力として，未使用依存リスト (`{model}_unused_dep`, `{model}_unused_dev_dep`) および
欠落依存リスト (`{model}_missing_dep`, LLMのみ) を記録する．

**実行順序の変更:**
先行研究はDC1→DC2(depcheck)→DC3(CI取得)の順で実行したが，
本研究ではDC1→DC2(CI取得)→DC3(モデル実行)の順に変更した．
これにより，CIデータが存在しないコミットへのモデル実行コストを削減している．

---

### 3.3 スキップ分析 (Skip Analysis)

各コミットに対して，以下の条件でスキップ可能性を判定する：

$$
\text{skippable} \Leftrightarrow \bigl(\text{dep} \in \text{unused} \cup \text{unused\_dev}\bigr) \wedge \bigl(\text{parent\_status} = \texttt{success}\bigr)
$$

ここで，$\text{dep}$ は更新された依存関係，$\text{unused}$ / $\text{unused\_dev}$ は
各検出モデルが報告した未使用のruntime依存・開発依存のセットである．
親コミットのビルドが成功している場合のみスキップ対象とするのは，
ビルドが既に失敗中の状態でスキップすることで障害を隠蔽しないためである．

スキップ可能と判定されたコミットのビルド実行時間を**浪費されたCIビルド時間**として集計する．
各検出モデルに対して独立にスキップ判定 (`{model}_is_skippable`) を算出し，
モデル間の比較を可能にする．

---

## 4. RQ1: （未記入）

（未記入）

---

## 5. RQ2: （未記入）

（未記入）

---

## 6. RQ3: （未記入）

（未記入）

---

## 7. Discussion

（未記入）

---

## 8. Threats to Validity

（未記入）

---

## 9. Conclusion

（未記入）

---

## References

（未記入）

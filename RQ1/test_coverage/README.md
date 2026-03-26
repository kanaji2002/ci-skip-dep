# check_coverage_badges.py

GitHubリポジトリのREADME.mdからカバレッジバッジを検出し、カバレッジ率を取得するスクリプト。

## 入出力

| 種別 | パス |
|------|------|
| 入力 | `original/results_1-50k_step3.csv` |
| 出力(全結果) | `picked-up/results_with_coverage_badge.csv` |
| 出力(バッジあり) | `picked-up/results_coverage_badge_true.csv` |
| 出力(75%以上) | `picked-up/results_coverage_75plus.csv` |

## 処理フロー

```
入力CSV (リポジトリ一覧)
  ↓
各リポジトリについて:
  1. GitHub API で README.md を取得 (fetch_readme)
  2. カバレッジバッジURLの有無を検出 (has_coverage_badge)
  3. バッジがあれば数値(%)を取得 (get_repo_coverage)
  4. 結果を3つのCSVに振り分けて出力
```

## バッジ検出の仕組み

検出対象は3種類のURLパターン：

| サービス | パターン |
|----------|----------|
| Codecov | `codecov.io` を含む |
| Coveralls | `coveralls.io` を含む |
| Shields.io | `img.shields.io` + `coverage` キーワード |

## カバレッジ数値取得の仕組み

1. **shields.io の場合**: `.svg` → `.json` に変換してJSONエンドポイントから `value` フィールドを取得
2. **フォールバック**: SVGを直接取得し `<text>` タグ内の `数字%` を正規表現で抽出

## 3つのCSVへの振り分け

各リポジトリを処理した後、以下の条件で振り分けます：

```
全リポジトリ
│
├── results_with_coverage_badge.csv   ← 全件（無条件に追加）
│
├── results_coverage_badge_true.csv   ← バッジあり (coverage_badge = true)
│
└── results_coverage_75plus.csv       ← バッジあり かつ カバレッジ >= 75%
```

上位集合 ⊃ 下位集合 の関係：

- `results_with_coverage_badge.csv` ⊇ `results_coverage_badge_true.csv` ⊇ `results_coverage_75plus.csv`

## 追加されるカラム

| カラム | 内容 |
|--------|------|
| `coverage_badge` | `true` / `false` |
| `readme_status` | `ok` / `no_readme` |
| `coverage_pct` | 数値(%) または空文字 |

## レート制限対応

| 状態 | スリープ |
|------|----------|
| トークンなし | 1.2秒/リクエスト（~50 req/min） |
| トークンあり | 0.05秒/リクエスト |

GitHub tokenは環境変数 `GITHUB_TOKEN` から取得。

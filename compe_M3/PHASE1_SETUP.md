# StoryWire M3 — Phase 1 セットアップ手順

## 作成ファイル一覧

```
storywire_m3/
├── config/
│   └── sources.yaml          # 情報源定義（10サイト）
├── crawler/
│   ├── __init__.py
│   └── playwright_crawler.py # Playwright巡回・キャプチャ・HTML保存
├── store/
│   ├── __init__.py
│   └── article_store.py      # SQLiteストア（格納・検索・分析結果書き戻し）
├── requirements_m3.txt       # 追加依存パッケージ
└── test_phase1.py            # Phase 1動作確認スクリプト
```

---

## セットアップ

### 1. パッケージインストール

既存のM1プロジェクトディレクトリに追加する：

```bash
# M1プロジェクトルートで実行
pip install playwright beautifulsoup4 lxml "APScheduler>=3.10,<4.0" pillow

# または requirements_m3.txt で一括インストール
pip install -r requirements_m3.txt
```

### 2. Playwright Chromiumブラウザのインストール

```bash
playwright install chromium
```

---

## Phase 1 動作確認

### ファイルの配置

M1プロジェクトのルートに以下をコピー（またはシンボリックリンク）：
- `config/sources.yaml`
- `crawler/` ディレクトリ
- `store/` ディレクトリ
- `test_phase1.py`

### テスト実行

```bash
# 登録済みソース一覧を確認
python test_phase1.py --list-sources

# UN Newsで動作確認（デフォルト・軽量）
python test_phase1.py

# CENTCOMで試す
python test_phase1.py --source centcom

# Reutersで試す
python test_phase1.py --source reuters_mideast
```

### 正常時の出力例

```
2026-03-07 12:00:00 [INFO] test_phase1: === Phase 1 Test: UN News ===
2026-03-07 12:00:00 [INFO] test_phase1:   URL        : https://news.un.org/en/
...
2026-03-07 12:00:10 [INFO] test_phase1: [RESULT] 4 articles crawled
  [0] UN News - Top Page
       URL       : https://news.un.org/en/
       Screenshot: data/crawl/un_news/20260307_120000_top.png
       Text len  : 3842
...
[DB STATS]
  Total articles : 4
  New saves      : 4
✅ Phase 1 test complete!
```

---

## 設計上のポイント

### ArticleStore（SQLite）
- `url_hash + crawled_at` のUNIQUE制約で同一URL・同一時刻の重複を防止
- `url_hash` のみでの重複排除はしていない（同じURLでも時間が変われば再格納する。ニュースサイトは同URLで内容が更新されることがあるため）
- `analyzed_at IS NULL` で未分析記事を効率的にフィルタリング
- `get_unanalyzed()` は Tier昇順（高品質ソース優先）→ 新しい順でソート

### PlaywrightCrawler
- `wait_until="networkidle"` がタイムアウトした場合は `domcontentloaded` にフォールバック
- 関連記事抽出はソース固有セレクタを優先、不足時はヒューリスティックで補完
- 各記事ページ間に1秒のwaitを挟んでサーバー負荷を軽減
- X(Twitter)はnitter.netを試み、失敗時x.comに直接アクセス

---

## 次のステップ（Phase 2）

Phase 1の動作確認後、Phase 2で以下を追加：

1. `analyst/gemini_analyst.py` — Gemini分析パイプライン（要約・重要度スコア・補足検索・AI画像生成）
2. `ArticleStore.update_analysis()` の呼び出し統合テスト

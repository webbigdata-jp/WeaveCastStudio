# M3: ファクトチェッカー / クローラー

**ニュース情報パイプライン — 収集・分析・動画生成**

M3 は政府機関・UN・通信社・シンクタンク・OSINT ダッシュボードなど信頼性の高いニュースソースを設定したスケジュールで巡回し、記事を SQLite に保存、Gemini で報道価値を採点し、ブリーフィング動画またはショートクリップを生成して M4 ライブ配信モジュールに提供します。

YouTuber・独立系ジャーナリスト・放送チームなど、あらゆるジャンルのニュースコンテンツクリエイターを想定した汎用設計です。デフォルトのプロンプトは意図的に広く設定されています。専門分野向けにカスタマイズする方法は [プロンプトのカスタマイズ](#プロンプトのカスタマイズ) を参照してください。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│  config/sources.yaml                                            │
│  (11 sources: gov, military, UN, wire services, think tanks)    │
└────────────┬────────────────────────────────────────────────────┘
             │
             ▼
┌────────────────────────┐     ┌─────────────────────────────────┐
│  crawler/              │     │  store/                          │
│  drission_crawler.py   │────▶│  article_store.py  (SQLite)     │
│                        │     │  data/articles.db                │
│  • ヘッドレス Chromium  │     │                                  │
│  • スクリーンショット   │     │  テーブル:                        │
│  • HTML + テキスト抽出  │     │    articles (収集 + 分析)        │
└────────────────────────┘     └──────────┬──────────────────────┘
                                          │
                                          ▼
                               ┌─────────────────────────┐
                               │  analyst/                │
                               │  gemini_analyst.py       │
                               │                          │
                               │  • 要約生成              │
                               │  • 重要度スコアリング    │
                               │  • トピック分類          │
                               │  • エンティティ抽出      │
                               │  • BREAKING 検知         │
                               └──────────┬──────────────┘
                                          │
                        ┌─────────────────┼─────────────────┐
                        ▼                 ▼                  ▼
              ┌──────────────┐  ┌──────────────────┐  ┌───────────┐
              │ composer/    │  │ scheduler/        │  │ M4        │
              │ briefing_    │  │ crawl_            │  │ (消費側)  │
              │ composer.py  │  │ scheduler.py      │  │           │
              │              │  │                   │  │ • search()│
              │ • ブリーフィン│  │ • APScheduler     │  │ • get_    │
              │   グ動画     │  │ • ソース別ジョブ   │  │   breaking│
              │ • ショート   │  │ • BREAKING フラグ │  │ • get_    │
              │   クリップ   │  └───────────────────┘  │   today   │
              └──────┬───────┘                         └───────────┘
                     │
                     ▼
              ┌──────────────┐
              │ shared/      │  (プロジェクトルート)
              │              │
              │ • image_gen  │
              │ • narrator   │
              │ • video_comp │
              └──────────────┘
```

## データフロー

```
1. CRAWL     sources.yaml → DrissionCrawler → ArticleStore（生記事）
2. ANALYZE   ArticleStore（未分析）→ GeminiAnalyst → ArticleStore（スコア付き）
3. COMPOSE   ArticleStore（上位記事）→ BriefingComposer → 動画 / クリップ
4. REGISTER  BriefingComposer → ContentIndex (content_index.json) → M4
```

各ステップは CLI から独立して実行できます。`pipeline` コマンドで連続実行も可能です。

## CLI リファレンス

すべてのコマンドは `compe_M3/` ディレクトリで実行します。

```bash
uv run main.py [--debug] <command> [options]
```

`--debug` フラグを付けると詳細出力（DB 統計・記事詳細・スクリプトプレビュー）が有効になります。

### crawl — ソース巡回

```bash
uv run main.py crawl                        # un_news を巡回（デフォルト）
uv run main.py crawl --source centcom        # 特定ソースを指定して巡回
uv run main.py crawl --all                   # 全ソースを一括巡回
uv run main.py crawl --list-sources          # 登録済みソース一覧を表示
```

各ソースについて、クローラーはトップページにアクセスしてスクリーンショットを撮影し、CSS セレクタで記事リンクを抽出したうえで、リンク先の記事ページ（ソースあたり最大 5 件）へアクセスしてスクリーンショットとテキストを取得します。結果は `data/articles.db` に保存されます。

### analyze — Gemini 分析

```bash
uv run main.py analyze                       # 未分析の記事をすべて分析
uv run main.py analyze --limit 5             # 最大 5 件に制限
```

各記事を Gemini 2.5 Flash に送信して構造化分析を実行します。モデルは summary・importance_score（0〜10）・topics・key_entities・sentiment・actionable intel フラグを含む JSON を返し、結果は同じ SQLite の行に書き戻されます。

### compose — 動画生成

```bash
uv run main.py compose --dry-run             # スクリプトのみ生成（動画スキップ）
uv run main.py compose                       # フルブリーフィング動画を生成
uv run main.py compose --short-clips         # 記事ごとに 30 秒クリップを生成
uv run main.py compose --short-clips --limit 1
uv run main.py compose --hours 6             # 直近 6 時間の記事を対象
```

2 つのモードがあります。

**ブリーフィングモード**（デフォルト）: 重要度上位の記事を選択し、タイトルカード・インフォグラフィック・TTS 音声を含む 5〜8 分のナレーション付きブリーフィング動画を `shared/` パイプライン経由で生成します。

**ショートクリップモード**（`--short-clips`）: 記事 1 件につき 30 秒のクリップ（4 文・約 75 語）を 1 本生成します。記事のスクリーンショットが存在する場合はそれを動画の背景に使用し、存在しない場合はタイトルカードを生成します。

出力ディレクトリ: `data/output/briefing_<timestamp>/` または `data/output/clips_<timestamp>/`

### schedule — バックグラウンドスケジューラー

```bash
uv run main.py schedule                      # デーモンモード（Ctrl+C で停止）
uv run main.py schedule --duration 30        # 30 秒後に自動停止（動作確認用）
```

APScheduler（BackgroundScheduler）を使用して、`sources.yaml` に定義された間隔でソースごとの巡回ジョブを実行します。スレッドベースのため Windows・Linux 両方で動作します。各ジョブは巡回と記事保存を行います。`importance_score >= 9.0` の記事は BREAKING フラグが立てられ、ContentIndex に即時登録されます。

### pipeline — フルパイプライン

```bash
uv run main.py pipeline                      # crawl_all → analyze → compose
uv run main.py pipeline --dry-run            # 動画生成をスキップ
uv run main.py pipeline --hours 48           # 直近 48 時間の記事から compose
```

`crawl --all` → `analyze` → `compose` を順番に実行します。GCE 上の cron ジョブでの運用を想定しています。

## sources.yaml スキーマ

`config/sources.yaml` の各エントリが巡回対象を定義します。

```yaml
sources:
  - id: centcom                    # 一意の識別子（DB・ファイルパス・CLI で使用）
    name: "U.S. Central Command"   # 表示用の名称
    url: "https://www.centcom.mil/MEDIA/NEWS-ARTICLES/"  # 巡回開始 URL
    tier: 1                        # 信頼階層（1=最高、3=最低）
    credibility: 5                 # 信頼性スコア（1〜5、Gemini に渡される）
    type: military                 # ソース種別（下記参照）
    country: US                    # 国コードまたは INTL
    crawl_interval_min: 10         # スケジューラーの巡回間隔（分）
    selectors:                     # リンク抽出用 CSS セレクタ
      news_links: "a[href*='/MEDIA/NEWS-ARTICLES/Article/']"
    notes: "任意のメモ"             # 自由記述のメモ（コードでは未使用）
```

### ソース種別

| 種別 | 説明 | 例 |
|------|------|----|
| `government` | 政府機関の公式部門 | U.S. DoD |
| `military` | 軍の司令部ページ | CENTCOM |
| `international_org` | 国連および関連機関 | UN News、UNHCR |
| `wire_service` | 通信社 / ニュースエージェンシー | Reuters、AP |
| `news_liveblog` | リアルタイム更新のライブブログ | Al Jazeera Live Blog |
| `think_tank` | 政策研究機関 | ISW、Critical Threats |
| `research` | 学術・紛争研究機関 | Understanding War |
| `osint_dashboard` | 地図ベースの OSINT ツール | LiveUAMap |
| `social_official` | 公式ソーシャルメディアアカウント | （X/Twitter タイムライン） |

### ティアシステム

| ティア | 信頼性 | 説明 | 巡回間隔 |
|--------|--------|------|----------|
| 1 | 5/5 | 公式政府機関・国際機関 | 10〜60 分 |
| 2 | 4/5 | 主要通信社・高信頼メディア | 5 分 |
| 3 | 3〜4/5 | シンクタンク・OSINT ダッシュボード・研究機関 | 10〜60 分 |

ティアと信頼性スコアは分析時に Gemini に渡され、重要度スコアリングのソース信頼性評価に利用されます。

### セレクタ

`selectors.news_links` フィールドには、BeautifulSoup がトップページ HTML から記事リンクを抽出するために使う CSS セレクタ文字列を指定します。セレクタで 3 件未満しか取得できない場合、クローラーはヒューリスティックなリンク抽出（URL パスキーワード `/news/`・`/article/`・`/story/` など）にフォールバックします。

地図ダッシュボードのようにトップページのスクリーンショットのみが意味を持つソースには `news_links: null` を設定してください。

## データベーススキーマ

すべてのデータは `data/articles.db`（SQLite）に格納されます。

```sql
CREATE TABLE articles (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id           TEXT NOT NULL,       -- sources.yaml の id に対応
    source_name         TEXT NOT NULL,
    url                 TEXT NOT NULL,
    url_hash            TEXT NOT NULL,       -- URL の MD5（重複排除キー）
    title               TEXT,
    text_content        TEXT,                -- 抽出した記事テキスト
    screenshot_path     TEXT,                -- PNG スクリーンショットのパス
    html_path           TEXT,                -- 保存 HTML のパス
    credibility         INTEGER,             -- sources.yaml から取得
    tier                INTEGER,             -- sources.yaml から取得
    is_top_page         BOOLEAN DEFAULT FALSE,

    -- Gemini 分析結果（analyze ステップ後に更新）
    summary             TEXT,
    importance_score    REAL,                -- 0.0〜10.0
    importance_reason   TEXT,
    topics              TEXT,                -- JSON 配列 ["politics", "economy", "environment"]
    key_entities        TEXT,                -- JSON 配列 ["Person Name", "Organisation", "Location"]
    sentiment           TEXT,                -- positive|negative|neutral|alarming
    has_actionable_intel BOOLEAN,
    ai_image_path       TEXT,

    crawled_at          TEXT NOT NULL,        -- ISO 8601 UTC
    analyzed_at         TEXT,                 -- Gemini 分析後に設定
    used_in_briefing    BOOLEAN DEFAULT FALSE,
    is_breaking         BOOLEAN DEFAULT FALSE,

    UNIQUE(url_hash, crawled_at)
);
```

### 重要度スコアリングガイド

Gemini に渡されるスコアリング基準：

| スコア | レベル | 基準 |
|--------|--------|------|
| 9〜10 | Breaking | 重大な確認済みイベント、重要な政策変更、大規模事件 |
| 7〜8 | High | 注目すべき進展、新たな公式声明、トレンドニュース |
| 5〜6 | Medium | 継続中の状況更新、背景情報、専門家見解 |
| 3〜4 | Low | ソフトニュース、社説、新事実のない分析 |
| 0〜2 | Minimal | 周辺的・時代遅れ・宣伝的・重複するコンテンツ |

スコアが 9.0 以上の記事はスケジューラーによって自動的に BREAKING フラグが立てられ、M4 のティッカーオーバーレイ用に ContentIndex に即時登録されます。

## 出力ディレクトリ構造

### ブリーフィング動画

```
data/output/briefing_20260315_120000/
├── briefing_plan.json        # 記事選択と中間構造
├── script.txt                # 生成されたナレーションスクリプト
├── images/                   # タイトルカード + インフォグラフィックスライド
├── audio/                    # TTS 音声セグメント
└── video/                    # 最終 MP4
```

### ショートクリップ

```
data/output/clips_20260315_120000/
├── clips_manifest.json       # 全クリップのサマリ
├── clip_001/
│   ├── script.txt            # 75 語のナレーション
│   ├── images/               # タイトルカードまたはスクリーンショット
│   ├── audio/                # TTS セグメント
│   └── clip_001.mp4
├── clip_002/
│   └── ...
```

## 他モジュールとの連携

### M4（ライブ配信）

M4 は M3 のデータを 2 つの方法で利用します。

**ContentIndex**（プロジェクトルートの `content_index.json`）: M3 はブリーフィング動画・ショートクリップ・BREAKING スクリーンショットをここに登録します。M4 の Gemini Live クライアントは、ジャーナリストが「最新の気候変動ニュースを見せて」と話しかけた際に ContentIndex を検索して関連メディアを探します。

**ArticleStore への直接アクセス**: M4 は `search(query)`・`get_breaking()`・`get_today_titles()` などのメソッドを使って SQLite データベースをリアルタイムに照会できます。

### M1（データ収集）

M3 は動画生成（画像生成・TTS ナレーション・ffmpeg 合成）のために M1 と `shared/` パイプラインモジュールを共有しています。M3 の `BriefingComposer` は、これらの共有関数を呼び出す前に記事データを M1 互換の構造に変換します。

### GCS 同期

GCE 上では、M3 の出力を cron 経由で Google Cloud Storage に同期します。

```bash
gcloud storage rsync data/output/ gs://<bucket>/m3/output/ --recursive
gcloud storage cp data/articles.db gs://<bucket>/m3/articles.db
```

Windows の放送局側では `pull_from_gcs.ps1 -m3only` で取得します。

## 設定

### 環境変数

M3 はプロジェクトルートの `.env` ファイルに `GOOGLE_API_KEY` と `LANGUAGE` の両方が必要です。

```
GOOGLE_API_KEY=your_gemini_api_key
LANGUAGE=ja        # BCP-47 コード: ja, en, ko, zh, fr, de, es, ...
```

`GeminiClient` はインスタンス化時に `LANGUAGE` を解決し、`self.language`（`bcp47_code` と `prompt_lang` を持つ `LanguageConfig`）として保持します。ユーザー向けテキストを生成するすべてのコンポーネントがクライアントから自動的に言語設定を読み込むため、コンポーネントごとの追加設定は不要です。

#### 言語出力の動作

| フィールド | 言語 |
|------------|------|
| `summary` | 出力言語（`LANGUAGE` で設定） |
| `importance_reason` | 出力言語（`LANGUAGE` で設定） |
| ブリーフィングスクリプト | 出力言語（`LANGUAGE` で設定） |
| ショートクリップスクリプト | 出力言語（`LANGUAGE` で設定） |
| `topics` | **常に英語**（下流処理でのフィルタリングを一貫させるため） |
| `key_entities` | **常に英語**（下流処理でのフィルタリングを一貫させるため） |
| ログメッセージ / 内部 ID | **常に英語** |

`LANGUAGE` が未設定の場合、M3 は `en`（英語）にデフォルトします。未対応の BCP-47 コードも英語にフォールバックし、警告ログが出力されます。

`GeminiClient` は以下の順序で `.env` を探索します。
1. `WeaveCastStudio/.env`（プロジェクトルート — 推奨）
2. カレントディレクトリの `.env`
3. `config/.env`（後方互換）
4. `../config/.env`（後方互換）

### 使用する Gemini モデル

| コンポーネント | モデル | 用途 |
|---------------|--------|------|
| GeminiAnalyst | `gemini-2.5-flash` | 記事分析（要約・スコアリング） |
| BriefingComposer | `gemini-2.5-flash-lite` | スクリプト生成（ブリーフィング・クリップ） |
| GeminiClient デフォルト | `gemini-2.5-flash-lite` | 汎用テキスト / JSON 生成 |

## プロンプトのカスタマイズ

デフォルトのプロンプトは一般的なニュース視聴者向けに設計されています。各プロンプトファイルには `NOTE FOR MAINTAINERS` コメントブロックがあり、専門分野向けに変更すべき箇所が具体的に説明されています。

| ファイル | カスタマイズ内容 |
|----------|-----------------|
| `analyst/gemini_analyst.py` — `_ANALYSIS_PROMPT_TEMPLATE` | スコアリング基準、`topics` の例示値、システム役割の説明 |
| `composer/briefing_composer.py` — `generate_m3_script` | スクリプト構成、語数、トーン、チャンネルブランディング |
| `composer/briefing_composer.py` — `_generate_short_clip_script` | 文数、語数、トーン |

**専門分野への適用例:**

- **金融 / マーケット**: 決算・金利決定・規制ニュースを重視するようスコアリング基準を変更。`topics` の例として `"earnings"`・`"markets"`・`"regulation"` を追加。
- **スポーツ**: 試合結果と移籍情報を優先。マッチレポート風のトーンに調整。
- **地域ニュース**: 「Breaking」の閾値を下げる（市議会の決定・地域の事件など）。`sources.yaml` を地域メディアのみに絞る。

## Cron 設定（GCE）

本番環境向けの推奨 crontab：

```cron
# 2 時間ごとにフルパイプラインを実行
0 */2 * * * cd /home/user/WeaveCastStudio/compe_M3 && uv run main.py pipeline >> /var/log/m3_pipeline.log 2>&1

# パイプライン後に GCS へ同期
10 */2 * * * gcloud storage rsync /home/user/WeaveCastStudio/compe_M3/data/output/ gs://BUCKET/m3/output/ --recursive
15 */2 * * * gcloud storage cp /home/user/WeaveCastStudio/compe_M3/data/articles.db gs://BUCKET/m3/articles.db
```

BREAKING ニュースをほぼリアルタイムで検知するには、スケジューラーデーモンを使用してください。

```bash
# screen / tmux セッションまたは systemd サービスで起動
cd compe_M3 && uv run main.py schedule
```

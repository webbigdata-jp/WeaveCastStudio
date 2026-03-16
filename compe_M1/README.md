# WeaveCastStudio M1

各国政府公式見解の収集・要約・画像生成・音読・動画化・ContentIndex登録を行うパイプライン。  
全トピックを一括処理し、**全体ブリーフィング動画**と**トピック別ショートクリップ**を生成する。

---

## システムフロー

```
┌──────────────────────────────────────────────────────────────────────────┐
│                        M1 パイプライン概要                                │
│                                                                          │
│  [STEP 1] トピック定義 (config/topics.yaml)                              │
│      │    search_countries + topics[] を読み込み                          │
│      ▼                                                                   │
│  [STEP 2] 各国政府公式見解の収集                                          │
│      │  ├─ Gemini 2.5 Flash + Google Search Tool → URL・要約テキスト取得  │
│      │  ├─ DrissionPage (Chrome) → URLをブラウザで開いてページ本文取得     │
│      │  ├─ スクリーンショット保存 → output/.../data/screenshots/          │
│      │  └─ Gemini 2.5 Flash → ページ本文から要点を再抽出                  │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 3] 構造化要約の生成 (Gemini 2.5 Flash — JSON出力)                 │
│      │  └─ 各国見解を統一JSONスキーマに変換 + URL一覧を保持               │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 4] ニュースブリーフィング原稿生成 (Gemini 2.5 Flash)               │
│      │  ├─ 全体ブリーフィング原稿 (script.txt)                           │
│      │  └─ トピック別クリップ原稿 (clip_scripts.json)                    │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 5] 画像生成 (Gemini Image Generation)                             │
│      │  ├─ タイトルスライド                                               │
│      │  ├─ ニュース一覧画像（本日のトピック一覧）                         │
│      │  ├─ コンテンツスライド（トピックごと）                             │
│      │  └─ クリップ用画像（トピックごと）                                 │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 6] ナレーション音声生成 (Gemini TTS)                              │
│      │  ├─ 全体ブリーフィング音声                                         │
│      │  ├─ クリップ音声（トピックごと）                                   │
│      │  └─ 失敗時は指数バックオフでリトライ（最大4回）                    │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 7] 動画合成 (ffmpeg)                                              │
│      │  ├─ 全体ブリーフィング動画 (1920x1080 / H.264 / AAC)              │
│      │  └─ トピック別ショートクリップ動画                                 │
│      │                                                                   │
│      ▼                                                                   │
│  [STEP 8] ContentIndex登録                                               │
│           ├─ 全体ブリーフィング動画を登録                                 │
│           ├─ クリップ動画を個別登録                                       │
│           ├─ ニュース一覧画像を登録                                       │
│           └─ manifest.json を更新                                        │
└──────────────────────────────────────────────────────────────────────────┘
```

### データフロー

```
config/topics.yaml
    │
    ▼
[Phase 1] shared/source_collector.py ──→ output/.../data/raw_statements.json
              │                                {topic: {country: {text, urls}}}
              │                          output/.../data/screenshots/*.png
              ▼
          shared/summarizer.py ────────→ output/.../data/briefing_data.json
                                               {briefing_sections[], analysis{}}
    │
    ▼
[Phase 2] shared/script_writer.py ─────→ output/.../data/script.txt        (全体原稿)
              │                          output/.../data/clip_scripts.json  (クリップ原稿)
              ▼
          shared/image_generator.py ───→ output/.../images/slide_title.png
                                         output/.../images/news_lineup.png
                                         output/.../images/slide_*.png
                                         output/.../clips/clip_*/image.png
                                         output/.../data/image_manifest.json
    │
    ▼
[Phase 3] shared/narrator.py ──────────→ output/.../audio/segment_*.wav    (全体音声)
                                         output/.../clips/clip_*/          (クリップ音声)
                                         output/.../data/audio_manifest.json
    │
    ▼
[Phase 4] shared/video_composer.py ────→ output/.../briefing.mp4           (全体動画)
                                         output/.../video/clips/clip_*.mp4 (クリップ動画)
                                         output/.../manifest.json
    │
    ▼
[Phase 5] content_index.py ────────────→ content_index.json (プロジェクトルート)
              │                          └─ M4 がこのファイルを参照して再生
              ▼
          (オプション) YouTube アップロード
```

> **出力ディレクトリ**: Phase 実行ごとにタイムスタンプ付きディレクトリ  
> `output/briefing_YYYYMMDD_HHMMSS/` が作成される。  
> `--output-dir` で既存ディレクトリを再利用可能。

---

## セットアップ

### 1. 依存パッケージのインストール

プロジェクトルートで `uv sync` を実行する。M1 固有の追加インストールは不要。

```bash
cd WeaveCastStudio
uv sync
```

### 2. Chrome / Chromiumのインストール（DrissionPageに必要）

```bash
# Ubuntu/Debian (GCE)
sudo apt install chromium-browser

# Windows
# Chrome または Edge がインストールされていれば動作する
```

### 3. ffmpegのインストール

```bash
# Ubuntu/Debian (GCE)
sudo apt install ffmpeg

# Windows
# https://www.gyan.dev/ffmpeg/builds/ からダウンロードしてPATHに追加
```

### 4. 環境変数の設定

プロジェクトルートの `.env` に `GOOGLE_API_KEY` を設定する（全モジュール共通）。

```bash
cat > .env << 'EOF'
GOOGLE_API_KEY=your_api_key_here
EOF
```

### 5. YouTube OAuth2の設定（YouTubeアップロードを使う場合のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) で **YouTube Data API v3** を有効化
2. 「認証情報」→「OAuth 2.0 クライアント ID」→「**デスクトップ アプリ**」で作成
3. ダウンロードしたJSONを `compe_M1/config/youtube_client_secrets.json` として保存
4. 初回実行時にブラウザが開いて認証フロー → `config/youtube_token.json` が自動生成
5. 以降はトークンの自動リフレッシュで再認証不要

---

## 使い方

### 段階的に実行する（推奨・デバッグしやすい）

```bash
cd compe_M1

# Phase 1: 情報収集 + 構造化要約
uv run main.py --phase 1

# Phase 2: 原稿生成 + 画像生成
uv run main.py --phase 2

# Phase 3: TTS 音声生成
uv run main.py --phase 3

# Phase 4: 動画合成
uv run main.py --phase 4

# Phase 5: ContentIndex 登録
uv run main.py --phase 5
```

### 全フェーズをまとめて実行

```bash
uv run main.py                 # Phase 1〜5 すべて実行
uv run main.py --skip-upload   # Phase 1〜5（YouTubeアップロードなし）
```

### トピックを切り替える

`config/topics.yaml` を編集してトピックを定義する。  
デフォルトでは全トピックを一括処理する。特定トピックのみ処理する場合は `--topic-index` を指定する。

```bash
uv run main.py --phase 1 --topic-index 0   # 最初のトピックのみ
uv run main.py --phase 1 --topic-index 1   # 2番目のトピックのみ
```

### 既存の出力ディレクトリを再利用する

Phase 1 の出力を使って Phase 2 以降だけやり直す場合に便利。

```bash
uv run main.py --phase 2 --output-dir output/briefing_20260310_172523
```

---

## topics.yaml の書き方

`config/topics.yaml` でトピックを定義する。サンプル構成:

```yaml
# search_countries: 検索対象の国（ヒント。ここにない国も検索に含まれる場合がある）
search_countries:
  - "United States"
  - "Iran"
  - "Israel"
  - "Russia"
  - "China"

topics:
  - title: "トピック日本語名"
    title_en: "Topic English Name"
    query_keywords:
      - "search keyword 1"
      - "search keyword 2"
    importance_score: 8.5       # 0.0〜10.0（ContentIndex での優先度に影響）
    tags: ["tag1", "tag2"]      # M4 での検索・フィルタ用
```

`topics` リストの各エントリが1つのニューストピックに対応する。  
全トピックに対して全体ブリーフィング動画 1 本 + トピック別クリップ動画が生成される。

---

## 使用モデル・ツール

| STEP | ツール／モデル | 用途 |
|------|--------------|------|
| 2 | Gemini 2.5 Flash + Google Search Tool | 各国見解・URL収集 |
| 2 | **DrissionPage** (Chrome自動化) | URLをブラウザで開きページ本文・スクリーンショット取得 |
| 2 | Gemini 2.5 Flash | ページ本文から要点を再抽出 |
| 3 | Gemini 2.5 Flash | 構造化JSON要約 |
| 4 | Gemini 2.5 Flash | ナレーション原稿生成（全体 + クリップ） |
| 5 | Gemini Image Generation | タイトル・一覧・コンテンツ・クリップ画像生成 |
| 6 | Gemini TTS | 音声合成（全体 + クリップ） |
| 7 | ffmpeg | 画像+音声→MP4動画合成 |
| 8 | content_index.py | M4向けコンテンツ登録 |

---

## エラーハンドリング

| STEP | 失敗シナリオ | 対処 |
|------|------------|------|
| STEP 2 (Gemini Search) | 検索結果なし | その国をスキップ |
| STEP 2 (DrissionPage) | ページ取得失敗 | Gemini検索結果のみで続行 |
| STEP 3 | JSON parse失敗 | 最大3回リトライ → フォールバック構造で続行 |
| STEP 5 | 画像生成失敗 | 単色プレースホルダー画像で代替 |
| STEP 6 | TTS失敗 | 指数バックオフで最大4回リトライ（5s→10s→20s→40s） |
| STEP 6 | 全リトライ失敗 | 3秒無音WAVをプレースホルダーとして挿入（動画合成を継続） |
| STEP 7 | ffmpeg失敗 | エラーログ出力・手動確認 |

---

## ディレクトリ構成

```
WeaveCastStudio/
├── .env                           # GOOGLE_API_KEY（全モジュール共通）
├── content_index.py               # M1/M3 → M4 共有コンテンツ登録
├── content_index.json             # ↑が管理するインデックスファイル（自動生成）
├── shared/                        # M1/M3 共通パイプラインモジュール
│   ├── source_collector.py        #   STEP 2: 情報収集 (Gemini + DrissionPage)
│   ├── summarizer.py              #   STEP 3: 構造化JSON要約
│   ├── script_writer.py           #   STEP 4: ナレーション原稿生成
│   ├── image_generator.py         #   STEP 5: 画像生成
│   ├── narrator.py                #   STEP 6: TTS音声合成（リトライ付き）
│   └── video_composer.py          #   STEP 7: ffmpeg動画合成
│
├── compe_M1/                      # ← このモジュール
│   ├── main.py                    # パイプライン実行スクリプト
│   ├── README.md                  # このファイル
│   ├── config/
│   │   ├── topics.yaml            # トピック定義（ユーザーが適宜編集）
│   │   ├── youtube_client_secrets.json  # YouTube OAuth2（要手動配置）
│   │   └── youtube_token.json     # OAuth2トークン（初回認証後に自動生成）
│   ├── uploader/
│   │   └── youtube_uploader.py    # YouTube アップロード
│   └── output/
│       └── briefing_YYYYMMDD_HHMMSS/   # Phase実行ごとに生成
│           ├── data/
│           │   ├── raw_statements.json  # Phase 1 出力
│           │   ├── briefing_data.json   # Phase 1 出力
│           │   ├── script.txt           # Phase 2 出力（全体原稿）
│           │   ├── clip_scripts.json    # Phase 2 出力（クリップ原稿）
│           │   ├── image_manifest.json  # Phase 2 出力
│           │   ├── audio_manifest.json  # Phase 3 出力
│           │   └── screenshots/         # Phase 1 スクリーンショット
│           ├── images/                  # Phase 2 生成画像
│           ├── audio/                   # Phase 3 生成音声
│           ├── video/
│           │   └── clips/               # Phase 4 クリップ動画
│           ├── clips/
│           │   └── clip_001/            # クリップ別作業ディレクトリ
│           │       ├── image.png
│           │       ├── script.txt
│           │       └── *.wav
│           ├── briefing.mp4             # Phase 4 全体動画
│           └── manifest.json            # 全Phase の成果物パス一覧
```

---

## Windows上での実行（M4ライブ中の利用）

M1 は GCE 上での定期実行を主な想定としているが、Windows 上でも動作する。  
M4 でライブ配信中に M1 を並行実行すれば、最新のブリーフィングやクリップを  
ContentIndex 経由で M4 に差し込むことができる。

### 動作要件

- **Chrome / Edge** がインストールされていること（DrissionPage が使用）
- **ffmpeg** にPATHが通っていること
- OBS + M4 + M1 の同時起動になるため、**メモリ 16GB 以上を推奨**  
  （特に Phase 1 でブラウザ自動化が走る間は CPU・メモリ負荷が高くなる）

### ContentIndex の同時アクセスについて

`content_index.py` はスレッドセーフ（`threading.Lock`）かつアトミックリネームで  
書き込みを行うため、M4 が読み取り中に M1 が書き込んでもファイルが壊れることはない。

ただし、M1 と M4 が**同時に書き込む**（M1 が `add_entry`、M4 が `mark_used` を  
ほぼ同時に呼ぶ）場合、プロセス間ロックがないため片方の変更が失われる可能性がある。  
実運用ではこのタイミングが重なる頻度は極めて低いが、留意すること。

### 運用パターン例

- **GCE で Phase 1 まで実行** → GCS 経由でデータを pull → **Windows で Phase 2〜5**  
  （Phase 1 のブラウザ自動化だけ GCE に任せ、Windows の負荷を軽減する）
- **Windows で全 Phase 実行**  
  （GCS 同期なしで完結。ただしリソース消費に注意）

---

## コスト見積もり（1ブリーフィングあたり）

| STEP | コスト |
|------|--------|
| STEP 2 Gemini Search × 10カ国 | ~$0.001 |
| STEP 3 構造化要約 | ~$0.001 |
| STEP 4 原稿生成 | ~$0.001 |
| STEP 5 画像生成 × 5〜8枚 | ~$0.20〜$0.31 |
| STEP 6 TTS × 15パラグラフ前後 | 無料枠内の見込み |
| STEP 7 ffmpeg | 無料（ローカル処理） |
| STEP 8 ContentIndex登録 | 無料（ローカル処理） |
| **合計** | **~$0.21〜$0.32** |

1日5本生成しても **~$1.05〜$1.60 / 日**

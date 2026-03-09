# StoryWire M1

各国政府公式見解の収集・要約・画像生成・音読・動画化・YouTubeアップロードを行うパイプライン。

---

## システムフロー

```
┌─────────────────────────────────────────────────────────────────────────┐
│                        M1 パイプライン概要                               │
│                                                                         │
│  [STEP 1] トピック定義 (config/topics.yaml)                             │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 2] 各国政府公式見解の収集                                         │
│      │  ├─ Gemini 2.5 Flash + Google Search Tool → URL・要約テキスト取得 │
│      │  ├─ nodriver (Chrome) → URLをブラウザで開いてページ本文取得        │
│      │  ├─ スクリーンショット保存 → output/screenshots/                  │
│      │  └─ Gemini 2.5 Flash → ページ本文から要点を再抽出                 │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 3] 構造化要約の生成 (Gemini 2.5 Flash — JSON出力)                │
│      │  └─ 各国見解を統一JSONスキーマに変換 + URL一覧を保持              │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 4] ニュースブリーフィング原稿生成 (Gemini 2.5 Flash)              │
│      │  └─ [IMAGE: ...] マーカー付きのナレーション原稿を生成             │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 5] 画像生成 (gemini-3.1-flash-image-preview)                     │
│      │  ├─ タイトルスライド生成                                          │
│      │  └─ [IMAGE: ...] マーカーごとにインフォグラフィック画像を生成     │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 6] ナレーション音声生成 (gemini-2.5-flash-preview-tts)           │
│      │  ├─ 原稿をパラグラフ分割してTTS合成                               │
│      │  └─ 失敗時は指数バックオフでリトライ（最大4回）                   │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 7] 動画合成 (ffmpeg: 画像スライドショー + 音声 → MP4)            │
│      │  └─ 1920x1080 / H.264 / AAC / faststart                         │
│      │                                                                  │
│      ▼                                                                  │
│  [STEP 8] YouTubeアップロード (YouTube Data API v3)                     │
│           ├─ containsSyntheticMedia: true（AI生成コンテンツフラグ）      │
│           ├─ 説明文にサマリ・各国スタンス一覧を自動生成                  │
│           └─ 説明文に引用元URL一覧を自動付与                            │
└─────────────────────────────────────────────────────────────────────────┘
```

### データフロー

```
topics.yaml
    │
    ▼
[Phase 1] source_collector.py ──→ output/data/raw_statements.json
              │                         {country: {text, urls, screenshot_paths}}
              │                   output/screenshots/*.png
              ▼
          summarizer.py ────────→ output/data/briefing_data.json
                                        {briefing_sections[], analysis{summary}}
    │
    ▼
[Phase 2] script_writer.py ─────→ output/data/script.txt
              │                         [IMAGE: ...] マーカー付き原稿
              ▼
          image_generator.py ───→ output/images/slide_title.png
                                  output/images/slide_000.png ...
                                  output/data/image_manifest.json
    │
    ▼
[Phase 3] narrator.py ──────────→ output/audio/segment_000.wav ...
                                  output/data/audio_manifest.json
    │
    ▼
[Phase 4] video_composer.py ────→ output/audio/full_narration.wav
                                  output/video/briefing.mp4
    │
    ▼
[Phase 5] youtube_uploader.py ──→ YouTube動画URL
```

---

## セットアップ

### 1. 依存パッケージのインストール

```bash
pip install -r requirements.txt
```

### 2. Chromeのインストール（nodriverに必要）

```bash
# Ubuntu/Debian
sudo apt install chromium-browser

# インストール確認
chromium-browser --version
```

### 3. ffmpegのインストール

```bash
sudo apt install ffmpeg
```

### 4. 環境変数の設定

```bash
cp config/.env.example config/.env
# config/.env を編集して GOOGLE_API_KEY を設定
```

### 5. YouTube OAuth2の設定（Phase 5を使う場合のみ）

1. [Google Cloud Console](https://console.cloud.google.com/) で **YouTube Data API v3** を有効化
2. 「認証情報」→「OAuth 2.0 クライアント ID」→「**デスクトップ アプリ**」で作成
3. ダウンロードしたJSONを `config/youtube_client_secrets.json` として保存
4. 初回実行時にブラウザが開いて認証フロー → `config/youtube_token.json` が自動生成
5. 以降はトークンの自動リフレッシュで再認証不要

---

## 使い方

### 段階的に実行する（推奨・デバッグしやすい）

```bash
# Phase 1: 情報収集 + 構造化要約
#   → output/data/raw_statements.json
#   → output/data/briefing_data.json
#   → output/screenshots/*.png
python main.py --phase 1

# Phase 2: 原稿生成 + 画像生成
#   → output/data/script.txt
#   → output/images/*.png
python main.py --phase 2

# Phase 3: TTS 音声生成
#   → output/audio/segment_*.wav
python main.py --phase 3

# Phase 4: 動画合成
#   → output/video/briefing.mp4
python main.py --phase 4

# Phase 5: YouTube アップロード（要 OAuth2 設定）
python main.py --phase 5
```

### 全フェーズをまとめて実行

```bash
python main.py --skip-upload   # Phase 1〜4のみ（YouTube アップロードなし）
python main.py                 # Phase 1〜5 すべて実行
```

### トピックを切り替える

```bash
python main.py --phase 1 --topic-index 0   # Iran Conflict 2026（デフォルト）
python main.py --phase 1 --topic-index 1   # Ukraine Peace Negotiations
python main.py --phase 1 --topic-index 2   # Taiwan Strait Tensions
python main.py --phase 1 --topic-index 3   # Global AI Regulation Treaty
python main.py --phase 1 --topic-index 4   # Climate Emission Targets
```

---

## 使用モデル・ツール

| STEP | ツール／モデル | 用途 |
|------|--------------|------|
| 2 | Gemini 2.5 Flash + Google Search Tool | 各国見解・URL収集 |
| 2 | **nodriver** (Chrome自動化) | URLをブラウザで開きページ本文・スクリーンショット取得 |
| 2 | Gemini 2.5 Flash | ページ本文から要点を再抽出 |
| 3 | Gemini 2.5 Flash | 構造化JSON要約 |
| 4 | Gemini 2.5 Flash | [IMAGE:]マーカー付きナレーション原稿生成 |
| 5 | gemini-3.1-flash-image-preview | インフォグラフィック画像生成 |
| 6 | gemini-2.5-flash-preview-tts | TTS音声合成 |
| 7 | ffmpeg | 画像+音声→MP4動画合成 |
| 8 | YouTube Data API v3 | 動画アップロード |

> **モデル名注意**: 仕様書の `gemini-2.5-flash-image` は存在しません。
> 2026-03時点の正式モデル名は `gemini-3.1-flash-image-preview` です。

---

## YouTube動画の説明文構成

アップロード時に自動生成される説明文の構成：

```
📋 SUMMARY
（analysis.summaryの内容）

✅ Points of Consensus:
  • （consensus_points）

⚡ Points of Divergence:
  • （divergence_points）

🌍 COUNTRY POSITIONS
🟦 United States: （position）
🟥 Iran: （position）
⬜ China: （position）
...

🔗 SOURCES
  • United States: https://state.gov/...
  • Japan: https://mofa.go.jp/...
  ...

Countries covered: ...

⚠️ AI生成コンテンツ警告
#StoryWire #OSINT #CrisisIntelligence
```

スタンス凡例: 🟦 supportive / 🟥 opposed / ⬜ neutral / 🟨 cautious

---

## エラーハンドリング

| STEP | 失敗シナリオ | 対処 |
|------|------------|------|
| STEP 2 (Gemini Search) | 検索結果なし | その国をスキップ |
| STEP 2 (nodriver) | ページ取得失敗 | Gemini検索結果のみで続行 |
| STEP 3 | JSON parse失敗 | 最大3回リトライ → フォールバック構造で続行 |
| STEP 5 | 画像生成失敗 | 単色プレースホルダー画像で代替 |
| STEP 6 | TTS失敗 | **指数バックオフで最大4回リトライ**（5s→10s→20s→40s） |
| STEP 6 | 全リトライ失敗 | 3秒無音WAVをプレースホルダーとして挿入（動画合成を継続） |
| STEP 7 | ffmpeg失敗 | エラーログ出力・手動確認 |
| STEP 8 | YouTube API失敗 | エラーログ出力・手動アップロードを案内 |

---

## ディレクトリ構成

```
storywire/
├── main.py                         # パイプライン実行スクリプト
├── requirements.txt
├── storywire.log                   # 実行ログ（自動生成）
├── config/
│   ├── topics.yaml                 # トピック定義（5サンプル収録）
│   ├── .env                        # GOOGLE_API_KEY 等
│   ├── .env.example                # テンプレート
│   ├── youtube_client_secrets.json # YouTube OAuth2（要手動配置）
│   └── youtube_token.json          # OAuth2トークン（初回認証後に自動生成）
├── agents/
│   ├── source_collector.py         # STEP 2: 情報収集 (Gemini + nodriver)
│   ├── summarizer.py               # STEP 3: 構造化JSON要約
│   ├── script_writer.py            # STEP 4: ナレーション原稿生成
│   ├── image_generator.py          # STEP 5: 画像生成
│   ├── narrator.py                 # STEP 6: TTS音声合成（リトライ付き）
│   └── video_composer.py           # STEP 7: ffmpeg動画合成
├── uploader/
│   └── youtube_uploader.py         # STEP 8: YouTubeアップロード
└── output/
    ├── data/                       # JSON中間ファイル
    │   ├── raw_statements.json     # STEP 2出力
    │   ├── briefing_data.json      # STEP 3出力
    │   ├── script.txt              # STEP 4出力
    │   ├── image_manifest.json     # STEP 5出力
    │   └── audio_manifest.json     # STEP 6出力
    ├── screenshots/                # nodriver スクリーンショット（STEP 2）
    ├── images/                     # 生成画像（STEP 5）
    ├── audio/                      # 生成音声（STEP 6）
    └── video/                      # 完成動画（STEP 7）
        └── briefing.mp4
```

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
| STEP 8 YouTube API | 無料（クォータ内） |
| **合計** | **~$0.21〜$0.32** |

1日5本生成しても **~$1.05〜$1.60 / 日**

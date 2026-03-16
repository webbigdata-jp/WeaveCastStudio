# WeaveCastStudio M4: Live Broadcast Client

**Gemini Live API を使ったリアルタイム放送支援クライアント**

ジャーナリストが OBS でライブ配信中に音声で指示を出すと、Gemini AI が動画再生・静止画表示・記事検索などを自動で実行します。画面下部には Breaking News ティッカーが常時表示され、速報が入ると自動でハイライトされます。

## アーキテクチャ

```
┌─────────────────────────────────────────────────────────────────┐
│  Windows PC（放送局）                                            │
│                                                                  │
│  ┌──────────────────────┐    ┌───────────────────────────────┐  │
│  │  gemini_live_client   │    │  OBS Studio                   │  │
│  │  ┌────────────────┐   │    │  ┌─────────────────────────┐  │  │
│  │  │ PTT マイク入力  │   │    │  │  ウィンドウキャプチャ    │  │  │
│  │  │ (F9 押下中)     │   │    │  │  ← MediaWindow          │  │  │
│  │  └───────┬────────┘   │    │  ├─────────────────────────┤  │  │
│  │          ▼            │    │  │  ブラウザソース          │  │  │
│  │  ┌────────────────┐   │    │  │  ← ticker.html          │  │  │
│  │  │ Gemini Live API │   │    │  │    (localhost:8765)      │  │  │
│  │  │  音声 + FC      │   │    │  └─────────────────────────┘  │  │
│  │  └───────┬────────┘   │    └───────────────────────────────┘  │
│  │          ▼            │                                       │
│  │  ┌────────────────┐   │    ┌───────────────────────────────┐  │
│  │  │ ToolExecutor    │   │    │  breaking_news_server         │  │
│  │  │ - play_video    │───┼───▶│  :8765/overlay → ticker.html  │  │
│  │  │ - show_image    │   │    │  :8765/events  → SSE          │  │
│  │  │ - search_articles│  │    │  :8765/status  → JSON          │  │
│  │  └───────┬────────┘   │    └──────────┬────────────────────┘  │
│  │          ▼            │               │                       │
│  │  ┌────────────────┐   │    ┌──────────▼────────────────────┐  │
│  │  │ MediaWindow     │   │    │  ArticleStore (SQLite)        │  │
│  │  │ tkinter + VLC   │   │    │  ← compe_M3/data/articles.db  │  │
│  │  └────────────────┘   │    └───────────────────────────────┘  │
│  └──────────────────────┘                                        │
└─────────────────────────────────────────────────────────────────┘
```

### データの流れ

1. **M1/M3（GCE）** が動画クリップ・記事データベースを生成し、GCS にアップロード
2. **Windows PC** が `pull_from_gcs.ps1` で GCS からデータを取得
3. **gemini_live_client.py** が起動時に ContentIndex と ArticleStore を読み込み
4. ジャーナリストが **F9（PTT）** で音声指示 → Gemini が Function Calling で応答
5. **MediaWindow** が動画/静止画を表示、**ティッカーサーバ** が速報を配信

## ファイル構成

```
compe_M4/
├── gemini_live_client.py      # メインエントリポイント
├── media_window.py            # tkinter + VLC メディアウィンドウ
├── breaking_news_server.py    # HTTP + SSE ティッカーサーバ
├── media_assets.json          # 静止画アセット定義
├── demo_setup.py              # デモ撮影用データ準備スクリプト
├── overlay/
│   └── ticker.html            # OBS ブラウザソース（ティッカー表示）
├── assets/                    # 静止画ファイル格納ディレクトリ
│   ├── iranstrikemap.png
│   ├── trump_truth_kharg.png
│   ├── kharg_island_map.png
│   └── ...
├── OBS_SETUP.md               # OBS 設定手順
└── DEMO_SCRIPT.md             # デモ撮影台本
```

## 前提条件

### ソフトウェア

- **Python 3.11+**（[uv](https://docs.astral.sh/uv/) で管理）
- **OBS Studio**（ライブ配信用）
- **VLC**（動画再生エンジン。python-vlc が利用）

### データ

- **M1/M3 の出力データ** が `pull_from_gcs.ps1` で取得済みであること
  - `compe_M3/data/articles.db` — 記事データベース（ArticleStore）
  - ContentIndex に登録された動画クリップ
- **Gemini API キー** がプロジェクトルートの `.env` に設定済みであること

### ハードウェア

- **マイク** — PTT 音声入力用
- **スピーカー/ヘッドフォン** — Gemini の音声応答出力用

## セットアップ

### 1. 依存パッケージのインストール

プロジェクトルートで実行:

```bash
uv sync
```

主要な依存パッケージ: `google-genai`, `pyaudio`, `keyboard`, `python-vlc`, `Pillow`, `python-dotenv`, `aiohttp`

### 2. 環境変数の設定

プロジェクトルート（`WeaveCastStudio/`）に `.env` ファイルを作成:

```bash
GOOGLE_API_KEY=your_api_key_here
```

### 3. データの取得

GCS から M1/M3 の出力データを取得:

```powershell
# 全モジュールのデータを取得
.\pull_from_gcs.ps1

# M3 のみ
.\pull_from_gcs.ps1 -m3only
```

### 4. 静止画アセットの準備

`media_assets.json` に定義された静止画アセットは、初回起動時に `source_url` から自動ダウンロードされます。`source_url` が空の場合は、手動で `assets/` ディレクトリに配置してください。

### 5. OBS の設定

[OBS_SETUP.md](OBS_SETUP.md) を参照してください。

## 起動方法

```bash
cd compe_M4
python gemini_live_client.py
```

起動すると以下が自動的に行われます:

1. 静止画アセットのダウンロード確認
2. ArticleStore から本日の記事タイトルをロード
3. ContentIndex から再生可能なコンテンツ一覧をロード
4. MediaWindow（tkinter）を別スレッドで起動
5. Breaking News ティッカーサーバを `http://localhost:8765` で起動
6. Gemini Live API に接続し、PTT 待機状態に入る

起動後、ターミナルに再生可能なコンテンツ一覧と静止画アセット一覧が表示されます。

## キーボードショートカット

| キー | 動作 |
|------|------|
| **F9** | **プッシュトゥトーク**（押している間だけマイク音声を Gemini に送信） |
| F5 | 再生 / 一時停止トグル |
| F6 | 停止（メディアウィンドウを画面外に移動） |
| F7 | メディアウィンドウを画面外に移動 |
| F8 | メディアウィンドウを画面内に復元 |

## Gemini が使えるツール（Function Calling）

ジャーナリストの音声指示に応じて、Gemini は以下のツールを自動で呼び出します。

| ツール名 | 説明 | パラメータ |
|----------|------|-----------|
| `play_video` | ContentIndex の動画を再生 | `content_id` |
| `stop_video` | 再生を停止しウィンドウを画面外に移動 | — |
| `pause_video` | 一時停止 | — |
| `resume_video` | 再生再開 | — |
| `show_image` | 静止画を表示 | `image_id` または `file_path` |
| `minimize_window` | ウィンドウを画面外に移動 | — |
| `restore_window` | ウィンドウを画面内に復元 | — |
| `search_articles` | ArticleStore をキーワード検索 | `query`, `limit` |
| `list_videos` | 再生可能なコンテンツ一覧を返す | — |

### コンテンツ選択の仕組み

起動時に ContentIndex の全コンテンツと ArticleStore の本日の記事タイトル（最大30件）を Gemini の system instruction に渡します。ジャーナリストが「〇〇についての動画を」と指示すると、Gemini がタイトル・トピックタグ・重要度スコアから最適な `content_id` を自律的に選択して `play_video` を呼び出します。

## コンポーネント詳細

### gemini_live_client.py

Gemini Live API との音声セッションを管理するメインクライアントです。

**主な機能:**

- **PTT（プッシュトゥトーク）**: F9 を押している間だけマイク音声を 16kHz PCM で Gemini に送信。離すと `audio_stream_end` を送信して発話終了を通知。
- **音声応答再生**: Gemini からの 24kHz PCM 音声をスピーカーに出力。
- **Function Calling**: Gemini のツール呼び出しを `ToolExecutor` 経由で実行し、結果を返送。
- **セッション自動再接続**: 接続切断時に Session Resumption ハンドルを使って会話コンテキストを維持したまま再接続（最大5回、指数バックオフ）。
- **GoAway ハンドリング**: サーバーからの切断予告を受信し、自動再接続。
- **Transcription 表示**: 入力音声（ジャーナリスト）と出力音声（Gemini）の文字起こしをターミナルに表示。

**使用モデル**: `gemini-2.5-flash-native-audio-preview-12-2025`

### media_window.py

tkinter ウィンドウに VLC 動画と Pillow 静止画を表示するメディアプレイヤーです。

**主な機能:**

- **動画再生**: VLC を tkinter Frame に埋め込み、1920×1080 で表示。再生中の動画切り替え（`swap_video`）にも対応。
- **静止画表示**: Pillow で画像を読み込み、アスペクト比を維持しつつウィンドウサイズにフィットさせて Canvas に描画。
- **スレッド分離**: tkinter メインループは別スレッドで実行し、asyncio のイベントループと共存。
- **OBS 対応**: 最小化ではなく画面外移動（`-1920, 0`）で非表示にすることで、OBS のウィンドウキャプチャが常にウィンドウを認識できる状態を維持。

**ウィンドウタイトル**: `WeaveCast Media`（OBS でキャプチャ対象を識別する際に使用）

### breaking_news_server.py

aiohttp ベースの HTTP + SSE サーバで、OBS のブラウザソースにニュースティッカーを配信します。

**エンドポイント:**

| パス | メソッド | 説明 |
|------|---------|------|
| `/overlay` | GET | ティッカー表示用 HTML（`ticker.html`）を返す |
| `/events` | GET | SSE ストリーム（ティッカー更新・速報検知イベントを配信） |
| `/breaking` | POST | 手動速報差し込み（JSON: `{"headline": "...", "source": "..."}`) |
| `/status` | GET | 現在のティッカー状態を JSON で返す（デバッグ用） |

**動作:**

- ArticleStore を 30秒間隔でポーリングし、`importance_score >= 3.0` の記事をティッカーに表示
- `is_breaking: true` の記事を検知すると、SSE で `breaking` イベントを配信
- SSE クライアントには 30秒間隔で heartbeat を送信して接続を維持

### ticker.html

OBS ブラウザソースとして表示されるティッカー UI です。

**表示要素:**

- **ティッカーバー**（画面下部 72px）: ニュースヘッドラインが左方向にスクロール（120px/秒）
- **速報アイテム**: 赤背景 + 黄色文字 + `BREAKING` タグで通常ニュースに混在表示
- **速報バナー**（画面上部）: 新しい速報を初回検知した際に 8秒間表示
- **速報フラッシュ**: 初回速報時に画面全体に軽い赤フラッシュ演出

SSE で `ticker` / `breaking` イベントを受信すると、ヘッドラインを再構築してスクロールを再開します。接続が切断された場合は 3秒後に自動再接続します。

### media_assets.json

静止画アセットの定義ファイルです。`ImageAssetManager` がこのファイルを読み込み、アセットの管理とダウンロードを行います。

**スキーマ:**

```json
{
  "image_assets": [
    {
      "id": "asset_id",
      "title": "表示名",
      "description": "説明文",
      "local_path": "assets/filename.png",
      "source_url": "https://...",
      "topic_tags": ["tag1", "tag2"]
    }
  ]
}
```

| フィールド | 必須 | 説明 |
|-----------|------|------|
| `id` | ○ | アセットの一意識別子。Gemini の `show_image` ツールで `image_id` として指定する |
| `title` | ○ | 表示名。Gemini の system instruction に渡される |
| `description` | — | 説明文 |
| `local_path` | ○ | `compe_M4/` からの相対パス |
| `source_url` | — | 初回起動時に自動ダウンロードする URL（空の場合は手動配置） |
| `topic_tags` | — | トピックタグ。Gemini がアセット選択時に参照する |

### demo_setup.py

デモ撮影用のデータ準備スクリプトです。詳細は [DEMO_SCRIPT.md](DEMO_SCRIPT.md) を参照してください。

**コマンド:**

```bash
# デモ用の通常ニュースを DB に投入
python demo_setup.py --seed-db

# ティッカーサーバ起動 + 30秒後に速報自動差し込み
python demo_setup.py --run --breaking-delay 30

# フルセット（DB 投入 + サーバ起動 + 速報タイマー）
python demo_setup.py --seed-db --run --breaking-delay 30

# 別ターミナルから速報を即時差し込み
python demo_setup.py --trigger-breaking

# 速報を解除してデモ初期状態に戻す
python demo_setup.py --clear-breaking
```

## 設定値一覧

### gemini_live_client.py

| 定数 | デフォルト値 | 説明 |
|------|-------------|------|
| `MODEL` | `gemini-2.5-flash-native-audio-preview-12-2025` | 使用する Gemini モデル |
| `SEND_RATE` | `16000` | マイク入力サンプリングレート（Hz） |
| `RECEIVE_RATE` | `24000` | Gemini 出力サンプリングレート（Hz） |
| `CHUNK_SIZE` | `1024` | 音声チャンクサイズ |
| `PTT_KEY` | `f9` | PTT キー |

### breaking_news_server.py

| 定数 | デフォルト値 | 説明 |
|------|-------------|------|
| `DEFAULT_PORT` | `8765` | HTTP サーバポート |
| `POLL_INTERVAL_SEC` | `30` | ArticleStore ポーリング間隔（秒） |
| `TICKER_HEADLINE_MAX` | `20` | ティッカーに表示する最大記事数 |
| `MIN_IMPORTANCE` | `3.0` | ティッカーに載せる最低 importance_score |

### media_window.py

| 定数 | デフォルト値 | 説明 |
|------|-------------|------|
| `WINDOW_WIDTH` | `1920` | ウィンドウ幅（px） |
| `WINDOW_HEIGHT` | `1080` | ウィンドウ高さ（px） |
| `WINDOW_TITLE` | `WeaveCast Media` | ウィンドウタイトル |

### ticker.html

| 定数 | デフォルト値 | 説明 |
|------|-------------|------|
| `SCROLL_SPEED` | `120` | スクロール速度（px/秒） |
| `BREAKING_BANNER_DURATION` | `8000` | 速報バナー表示時間（ms） |
| `RECONNECT_DELAY` | `3000` | SSE 再接続遅延（ms） |

## トラブルシューティング

### Gemini に接続できない

- `.env` に `GOOGLE_API_KEY` が正しく設定されているか確認してください
- ネットワーク接続を確認してください
- ターミナルに表示されるエラーメッセージを確認してください。セッション再接続は最大5回まで自動で行われます

### マイクが認識されない

- PyAudio がデフォルトの入力デバイスを認識できるか確認してください
- Windows の「サウンド設定」でマイクが有効になっているか確認してください
- 他のアプリケーションがマイクを占有していないか確認してください

### 動画が再生されない

- VLC がインストールされているか確認してください（python-vlc は VLC 本体が必要です）
- ContentIndex に動画が登録されているか確認してください（起動時にターミナルに一覧が表示されます）
- `pull_from_gcs.ps1` でデータが正しく取得されているか確認してください

### ティッカーが表示されない

- ブラウザで `http://localhost:8765/overlay` にアクセスして表示を確認してください
- `http://localhost:8765/status` で JSON が返るか確認してください
- OBS のブラウザソース設定は [OBS_SETUP.md](OBS_SETUP.md) を参照してください

### keyboard モジュールのパーミッションエラー（Linux の場合）

`keyboard` ライブラリは Linux では root 権限が必要です。`sudo` で実行するか、`udev` ルールを設定してください。Windows では管理者権限は不要です。

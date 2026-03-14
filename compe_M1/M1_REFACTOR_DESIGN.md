# M1 リファクタリング設計書
## 複数トピック対応 + ショートクリップ生成 + ニュース一覧画像

---

## 1. 現状の構造

### topics.yaml（現在）
```yaml
topics:
  - title: "ホルムズ海峡封鎖"
    query_keywords: ["Strait of Hormuz blockade 2026"]
    countries_of_interest: ["United States", "Iran"]
  - title: "イラン新最高指導者"
    ...
```

### 現在の処理フロー
```
main.py --topic-index 0  （1トピックずつ実行）
  ↓
Phase 1: source_collector → 1トピック × N国の情報収集 → raw_statements.json
         summarizer → briefing_data.json
Phase 2: script_writer → 1本の原稿 → script.txt
         image_generator → タイトル画像 + コンテンツ画像
Phase 3: narrator → TTS音声セグメント
Phase 4: video_composer → 1本のブリーフィング動画 briefing.mp4
Phase 5: YouTube upload + content_index 登録
```

### 問題点
- 1実行 = 1トピックしか処理できない
- countries_of_interest は国ごとに個別検索するが、グラウンディング検索は
  トピック+国でまとめて情報が返るので国指定の意味が薄い
- ショートクリップ（トピック別の個別動画）が作れない
- M4が「ニュース1を流して」と指示するためのクリップ単位が存在しない

---

## 2. 新しい構造

### topics.yaml（新）
```yaml
# 全トピック共通設定
search_countries:
  - "United States"
  - "Iran"
  - "Israel"
  - "Russia"
  - "China"

topics:
  - title: "ホルムズ海峡封鎖"
    title_en: "Strait of Hormuz blockade"
    query_keywords:
      - "Strait of Hormuz blockade 2026"
      - "Iran Hormuz closure shipping"
    importance_score: 9.0
    tags: ["hormuz", "iran", "shipping", "military"]

  - title: "イラン新最高指導者"
    title_en: "New Iranian Supreme Leader"
    query_keywords:
      - "New Iranian Supreme Leader 2026"
      - "Mojtaba Khamenei supreme leader"
    importance_score: 8.5
    tags: ["iran", "leadership", "politics"]

  - title: "イラン女子校への空爆"
    title_en: "Airstrikes on girls schools in Iran"
    query_keywords:
      - "Airstrikes on girls schools in Iran 2026"
    importance_score: 9.5
    tags: ["iran", "airstrikes", "civilian", "humanitarian"]
```

**変更点:**
- `countries_of_interest` → 廃止。代わりにルートレベルに `search_countries`（OR検索条件）
- `title_en` 追加（グラウンディング検索用の英語タイトル）
- `importance_score` をトピックレベルに追加
- `tags` をトピックレベルに追加（content_index登録用）

### 新しい処理フロー
```
main.py  （全トピックを一括処理）
  ↓
Phase 1: source_collector
           ├─ トピック1のグラウンディング検索（search_countriesをOR条件で使用）
           ├─ トピック2のグラウンディング検索
           ├─ トピック3のグラウンディング検索
           └─ バックアップOSINT 3サイト取得
         summarizer → 全トピック統合のbriefing_data.json

Phase 2: script_writer → 全体ブリーフィング原稿 + トピック別原稿
         image_generator
           ├─ 「本日のニュース一覧」画像
           ├─ タイトルスライド
           └─ コンテンツ画像（トピック別）

Phase 3: narrator
           ├─ 全体ブリーフィング用TTS
           └─ トピック別TTS

Phase 4: video_composer
           ├─ 全体ブリーフィング動画 briefing.mp4
           ├─ clip_001_hormuz.mp4  （トピック1）
           ├─ clip_002_leader.mp4  （トピック2）
           └─ clip_003_school.mp4  （トピック3）

Phase 5: content_index登録（全体 + 各クリップ + ニュース一覧画像）
```

---

## 3. ファイル別の変更内容

### 3.1 topics.yaml
- `countries_of_interest` → ルートの `search_countries` に移動
- 各トピックに `title_en`, `importance_score`, `tags` を追加

### 3.2 main.py — 変更: 中
- `load_topic(index)` → `load_all_topics()` に変更（全トピック一括ロード）
- `--topic-index` オプションは後方互換で残す（指定時はそのトピックのみ）
- Phase 1: `collect_government_statements()` をトピックごとにループ呼び出し
  結果を `{"topic_title": raw_statements}` の形で統合保存
- Phase 2: 全体原稿 + トピック別原稿の両方を生成
- Phase 4: 全体動画 + トピック別クリップの両方を生成
- Phase 5: content_indexに全体 + 各クリップを登録
- OutputDirs に `clips/` ディレクトリを追加

### 3.3 source_collector.py — 変更: 中
- `collect_government_statements()` のシグネチャ変更:
  - `topic: dict` → 1トピックを受け取る形は維持
  - `countries_of_interest` の代わりに `search_countries` を使用
  - プロンプトを変更: 国名をOR条件として検索クエリに含める
    ```
    "Search for ... regarding {topic_title_en}.
     Include perspectives from: {', '.join(search_countries)} (if available)."
    ```
  - 国ごとのループ廃止 → 1トピック1回のグラウンディング検索に変更
- main.py側で `for topic in topics: collect_government_statements(client, topic)` のループ

### 3.4 summarizer.py — 変更: 小
- `generate_structured_summary()` は基本的にそのまま使える
- raw_statementsの構造が変わる（キーが国名→トピック名になる可能性）ため、
  `_normalize_raw_statements()` の調整が必要
- briefing_sections の出力が「国×立場」→「トピック×国の反応」に変わる

### 3.5 script_writer.py — 変更: 中
- 新関数 `generate_clip_scripts()` を追加:
  briefing_dataのトピックごとにショートクリップ用の短い原稿（30-60秒）を生成
- 既存の `generate_briefing_script()` は全体ブリーフィング用にそのまま維持
- 返り値を拡張:
  ```python
  {
      "full_script": str,           # 全体ブリーフィング原稿
      "clip_scripts": [             # トピック別ショートクリップ原稿
          {"topic_title": str, "script": str, "image_marker": str},
          ...
      ]
  }
  ```

### 3.6 image_generator.py — 変更: 中
- 新関数 `generate_news_lineup_image()` を追加:
  全トピックのタイトル一覧を1枚の画像にまとめる
  （M4で「本日のニュース一覧」として表示）
  ```
  ┌──────────────────────────────┐
  │    本日のニュース一覧        │
  │    2026年3月15日             │
  │                              │
  │  1. ホルムズ海峡封鎖        │
  │  2. イラン新最高指導者      │
  │  3. イラン女子校への空爆    │
  │                              │
  │              STORYWIRE        │
  └──────────────────────────────┘
  ```
- Gemini画像生成で日本語テキスト入り画像を生成（既に対応済み）
- 各クリップ用の個別画像はコンテンツ画像をそのまま流用可能

### 3.7 narrator.py — 変更: 小
- 変更なし。`generate_narration()` はテキストを受け取ってWAVを返すだけなので、
  全体原稿/クリップ原稿のどちらでも使える
- main.py側でクリップごとに呼び出す

### 3.8 video_composer.py — 変更: 小
- 変更なし。`compose_video()` は画像+音声→MP4を作るだけなので、
  全体/クリップのどちらでも使える
- main.py側でクリップごとに呼び出す

### 3.9 content_index.py — 変更: なし
- `make_entry()` + `ContentIndexManager.add_entry()` をそのまま使用
- 登録エントリ:
  ```
  id="m1_{timestamp}"           type="video"   → 全体ブリーフィング
  id="m1_{timestamp}_clip_001"  type="video"   → クリップ1
  id="m1_{timestamp}_clip_002"  type="video"   → クリップ2
  id="m1_{timestamp}_lineup"    type="image"   → ニュース一覧画像
  ```

---

## 4. 出力ディレクトリ構成

```
output/briefing_20260315_120000/
├── manifest.json              # 全成果物のパス・メタデータ
├── data/
│   ├── raw_statements.json    # 全トピック分の生データ
│   ├── briefing_data.json     # 構造化要約（全トピック統合）
│   ├── script.txt             # 全体ブリーフィング原稿
│   ├── clip_scripts.json      # トピック別原稿一覧
│   ├── image_manifest.json
│   └── audio_manifest.json
├── images/
│   ├── slide_title.png        # タイトルスライド
│   ├── news_lineup.png        # ★新規: 本日のニュース一覧画像
│   ├── slide_000.png          # コンテンツ画像（全体用）
│   ├── slide_001.png
│   └── ...
├── audio/
│   ├── segment_000.wav        # 全体ナレーション
│   ├── segment_001.wav
│   ├── clip_001_000.wav       # ★新規: クリップ1のナレーション
│   ├── clip_002_000.wav
│   └── full_narration.wav
├── video/
│   ├── briefing.mp4           # 全体ブリーフィング動画
│   └── clips/                 # ★新規
│       ├── clip_001_hormuz.mp4
│       ├── clip_002_leader.mp4
│       └── clip_003_school.mp4
└── clips/                     # ★新規: クリップ別のアセットまとめ
    ├── clip_001/
    │   ├── script.txt
    │   ├── image.png
    │   └── audio.wav
    └── ...
```

---

## 5. M4側への影響

### content_index.json に登録されるエントリ例
```json
[
  {
    "id": "m1_20260315_120000",
    "module": "M1",
    "type": "video",
    "title": "国際情勢ブリーフィング 2026-03-15",
    "topic_tags": ["hormuz", "iran", "leadership", "airstrikes"],
    "importance_score": 9.5,
    "duration_seconds": 180
  },
  {
    "id": "m1_20260315_120000_clip_001",
    "module": "M1",
    "type": "video",
    "title": "ホルムズ海峡封鎖",
    "topic_tags": ["hormuz", "iran", "shipping", "military"],
    "importance_score": 9.0,
    "duration_seconds": 45
  },
  {
    "id": "m1_20260315_120000_clip_002",
    "module": "M1",
    "type": "video",
    "title": "イラン新最高指導者",
    "topic_tags": ["iran", "leadership", "politics"],
    "importance_score": 8.5,
    "duration_seconds": 40
  },
  {
    "id": "m1_20260315_120000_lineup",
    "module": "M1",
    "type": "image",
    "title": "本日のニュース一覧 2026-03-15",
    "topic_tags": ["lineup", "index"],
    "importance_score": 10.0
  }
]
```

### M4（gemini_live_client.py）での使い方
- system_instructionの再生可能コンテンツ一覧に上記が表示される
- ジャーナリスト:「本日のニュース一覧を出して」→ Geminiが `show_image(content_id="m1_..._lineup")` を呼ぶ
- ジャーナリスト:「ホルムズ海峡のニュースを流して」→ Geminiが `play_video(content_id="m1_..._clip_001")` を呼ぶ
- ジャーナリスト:「全体ブリーフィングを流して」→ `play_video(content_id="m1_...")` を呼ぶ
- **M4側のコード変更は不要**（content_indexに正しく登録されていれば自動で認識）

---

## 6. source_collector のプロンプト変更案

### 現在（国ごとにループ）
```
"Search for ... regarding {country}'s stance on: {topic_title}."
× countries_of_interest の数だけ繰り返し
```

### 新方式（トピックごとに1回）
```
"Today's date is {date}.
Search for the latest developments and official government statements
regarding: {topic_title_en}.

Include perspectives from any of these countries if available:
{', '.join(search_countries)}.

PRIORITIZE news from the last 24 hours.
Return multiple statements if available, ordered by date (most recent first).

For each statement:
1. DATE: ...
2. COUNTRY: ...
3. SPEAKER_OR_SOURCE: ...
4. KEY_POINTS:
   - ...
"
```

**メリット:**
- API呼び出し回数が「トピック数 × 国数」→「トピック数」に削減
- グラウンディング検索は自然と複数国の情報を返すので、
  1回の検索で十分な情報が得られる（テスト結果で確認済み）

---

## 7. 実装優先度

| 優先度 | ファイル | 変更内容 | 工数目安 |
|--------|----------|----------|----------|
| 1 | topics.yaml | 新構造に変更 | 小 |
| 2 | source_collector.py | 国ループ廃止→トピック1回検索 | 中 |
| 3 | main.py | 全トピック一括処理+クリップ生成ループ | 中〜大 |
| 4 | script_writer.py | クリップ用原稿生成関数追加 | 中 |
| 5 | image_generator.py | ニュース一覧画像生成関数追加 | 小 |
| 6 | summarizer.py | raw_statements構造の調整 | 小 |
| 7 | narrator.py | 変更なし | — |
| 8 | video_composer.py | 変更なし | — |
| 9 | content_index.py | 変更なし | — |
| — | gemini_live_client.py | 変更なし | — |
| — | breaking_news_server.py | 変更なし | — |

---

## 8. 後方互換性

- `--topic-index N` を指定した場合は従来通り1トピックのみ処理（後方互換）
- `--topic-index` 未指定で全トピック一括処理（新動作）
- raw_statements.json の構造変更あり（キーが国名→トピック名）
  → 既存のPhase 2以降を再実行する場合は Phase 1 からやり直す必要あり
- content_index.json のエントリ形式は変更なし（既存のM3/M4エントリと共存可能）

# Content Index — Developer Reference

> **File:** `WeaveCastStudio/content_index.py`  
> **Data store:** `WeaveCastStudio/content_index.json`  
> **Audience:** Developers working on M1, M3 (producers) and M4 (consumer)

---

## Overview

`content_index.py` is the shared registry that connects the backend pipeline modules (M1, M3) with the live broadcast client (M4).

- **M1** registers briefing videos and individual topic clips after video composition (Phase 4/5).
- **M3** registers screenshots and short-clip videos produced by the fact-checker / crawler pipeline.
- **M4** queries the registry at broadcast time to find, play, and display content in response to voice commands.

All state is persisted in a single JSON file (`content_index.json`) located at the project root. Writes are thread-safe via `threading.Lock`. The file is written atomically (temp file + rename) to avoid corruption.

---

## Public API

### `make_entry(...)` → `dict`

Factory function that builds a well-formed entry dict. Always use this function rather than constructing dicts by hand.

```python
from content_index import ContentIndexManager, make_entry

entry = make_entry(
    id="m1_20260315_073422",
    module="M1",
    content_type="video",
    title="Iran Conflict: Official Government Positions",
    topic_tags=["iran", "military", "hormuz"],
    description="Briefing covering the Strait of Hormuz blockade and Iranian leadership.",
    importance_score=9.5,
    is_breaking=False,
    video_path="compe_M1/output/briefing_20260315_073422/video/briefing.mp4",
    manifest_path="compe_M1/output/briefing_20260315_073422/manifest.json",
)
```

**Key behaviours:**
- `video_path`, `screenshot_path`, and `manifest_path` are stored as **both relative and absolute** paths. Relative paths are computed from the directory containing `content_index.json`.
- If `duration_seconds` is omitted and `video_path` is provided, duration is auto-detected via `ffprobe`. Set `duration_seconds` explicitly if `ffprobe` is unavailable.
- `created_at` defaults to the current UTC time in ISO 8601 format.
- `index_path` can be passed to override the default `content_index.json` location (useful for testing).

---

### `ContentIndexManager`

The main interface for reading and writing the registry.

```python
mgr = ContentIndexManager()                        # default path
mgr = ContentIndexManager("/custom/path/index.json")  # custom path
```

#### Write methods

| Method | Description |
|--------|-------------|
| `add_entry(entry)` | Add or overwrite an entry by `id`. |
| `remove_entry(entry_id)` | Remove an entry by `id`. Returns `True` if found. |
| `set_breaking(entry_id, is_breaking)` | Flip the `is_breaking` flag. |
| `mark_used(entry_id)` | Set `used_in_broadcast=True` after M4 plays the content. |

#### Read methods

| Method | Description |
|--------|-------------|
| `get_all(sort_by_importance)` | All entries. Breaking items sort first, then by `importance_score` desc. |
| `get_by_module(module)` | Filter by `"M1"` or `"M3"`. |
| `get_by_tags(tags, match_any)` | Tag-based search (case-insensitive). `match_any=True` = OR logic. |
| `get_by_type(content_type)` | Filter by `"video"`, `"screenshot"`, or `"image"`. |
| `get_breaking()` | All entries where `is_breaking=True`. |
| `get_today()` | Entries created today (UTC). Used by M4 on startup. |
| `get_stats()` | Summary counts for debugging / monitoring. |

---

## Entry Schema

Every entry in `content_index.json` follows this structure:

```jsonc
{
  // ── Identity ──────────────────────────────────────────────────────
  "id": "m1_briefing_20260315_073422",   // Unique string ID
  "module": "M1",                         // "M1" | "M3"
  "type": "video",                        // "video" | "screenshot" | "image"

  // ── Discovery ─────────────────────────────────────────────────────
  "title": "国際情勢ブリーフィング 2026-03-15",
  "description": "...",                   // Optional. Passed to Gemini for context-aware search.
  "topic_tags": ["iran", "hormuz", "military"],  // English tags always

  // ── Source provenance (M3 only) ───────────────────────────────────
  "source_id": null,                      // sources.yaml id, e.g. "centcom"
  "source_name": null,                    // Display name of the crawled source

  // ── Importance ────────────────────────────────────────────────────
  "importance_score": 9.5,               // 0.0–10.0, scored by Gemini
  "is_breaking": false,                  // True = M4 may interrupt current playback

  // ── File paths ────────────────────────────────────────────────────
  "video_path": "compe_M1/output/.../video/briefing.mp4",      // Relative path
  "video_path_abs": "C:/Users/.../video/briefing.mp4",         // Absolute path
  "duration_seconds": 463.025,

  "screenshot_path": null,               // Relative path (M3 screenshots / M1 images)
  "screenshot_path_abs": null,

  "manifest_path": "compe_M1/output/.../manifest.json",
  "manifest_path_abs": "C:/Users/.../manifest.json",

  // ── Lifecycle ─────────────────────────────────────────────────────
  "created_at": "2026-03-15T07:48:05.385142+00:00",  // UTC ISO 8601
  "used_in_broadcast": false             // Set to true by M4 after playback
}
```

### Field reference

| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `id` | string | ✅ | Unique identifier. Convention: `m1_<timestamp>` or `m3_<timestamp>`. |
| `module` | string | ✅ | `"M1"` or `"M3"`. |
| `type` | string | ✅ | `"video"`, `"screenshot"`, or `"image"`. |
| `title` | string | ✅ | Human-readable title. Language follows the `LANGUAGE` env var. |
| `description` | string\|null | — | Free-text summary. Passed to Gemini in M4 for semantic matching. |
| `topic_tags` | string[] | ✅ | **Always English.** Used for tag-based search in `get_by_tags()`. |
| `source_id` | string\|null | — | M3 only. Key from `sources.yaml`. |
| `source_name` | string\|null | — | M3 only. Display name of the crawled source. |
| `importance_score` | float\|null | — | 0.0–10.0. Higher scores surface first in `get_all()`. |
| `is_breaking` | bool | ✅ | Breaking-news flag. M4 polls `get_breaking()` to trigger urgent interrupts. |
| `video_path` | string\|null | — | Path relative to `content_index.json` directory. |
| `video_path_abs` | string\|null | — | Absolute path. M4 uses this for direct file access. |
| `duration_seconds` | float\|null | — | Video length in seconds. Auto-detected via `ffprobe` if omitted. |
| `screenshot_path` | string\|null | — | Relative path to screenshot or image file. |
| `screenshot_path_abs` | string\|null | — | Absolute path to screenshot or image file. |
| `manifest_path` | string\|null | — | Relative path to `manifest.json` / `briefing_plan.json`. |
| `manifest_path_abs` | string\|null | — | Absolute path to manifest. |
| `created_at` | string | ✅ | UTC ISO 8601 timestamp. |
| `used_in_broadcast` | bool | ✅ | Set to `true` by M4 via `mark_used()` after playback. |

---

## ID Naming Conventions

| Module | Pattern | Example |
|--------|---------|---------|
| M1 full briefing | `m1_briefing_<YYYYMMDD_HHMMSS>` | `m1_briefing_20260315_073422` |
| M1 individual clip | `m1_briefing_<YYYYMMDD_HHMMSS>_clip_<NNN>` | `m1_briefing_20260315_073422_clip_001` |
| M1 lineup image | `m1_briefing_<YYYYMMDD_HHMMSS>_lineup` | `m1_briefing_20260315_073422_lineup` |
| M3 article clip | `m3_<YYYYMMDD_HHMMSS>` | `m3_20260315_094500` |

IDs must be unique across all modules. The timestamp component should be the moment the content was produced, not the moment the entry was registered.

---

## Topic Tags

`topic_tags` must always be in **English**, regardless of the `LANGUAGE` environment variable. This ensures consistent cross-module filtering.

```python
# ✅ Correct
topic_tags=["iran", "airstrikes", "civilian", "humanitarian"]

# ❌ Wrong — Japanese tags break get_by_tags() cross-module searches
topic_tags=["イラン", "空爆", "民間人"]
```

M4's Gemini function-call handler normalises search keywords to English before calling `get_by_tags()`.

---

## Module Integration Guide

### M1 — Registering content after video composition

Call `add_entry` at the end of Phase 4 (video composition) or Phase 5 (upload). Register one entry per output file: the full briefing, each topic clip, and the lineup image.

```python
from content_index import ContentIndexManager, make_entry
from pathlib import Path

INDEX_PATH = Path(__file__).parents[1] / "content_index.json"

mgr = ContentIndexManager(INDEX_PATH)

# Full briefing video
mgr.add_entry(make_entry(
    id=f"m1_briefing_{timestamp}",
    module="M1",
    content_type="video",
    title=f"国際情勢ブリーフィング {date_str}",
    topic_tags=all_tags,
    importance_score=9.5,
    video_path=output_dir / "video" / "briefing.mp4",
    manifest_path=output_dir / "manifest.json",
    index_path=INDEX_PATH,
))

# Per-topic clips
for i, clip in enumerate(clips, start=1):
    mgr.add_entry(make_entry(
        id=f"m1_briefing_{timestamp}_clip_{i:03d}",
        module="M1",
        content_type="video",
        title=clip.title,
        topic_tags=clip.tags + ["clip"],
        importance_score=clip.score,
        video_path=clip.path,
        manifest_path=output_dir / "manifest.json",
        index_path=INDEX_PATH,
    ))

# Lineup image
mgr.add_entry(make_entry(
    id=f"m1_briefing_{timestamp}_lineup",
    module="M1",
    content_type="image",
    title=f"本日のニュース一覧 {date_str}",
    topic_tags=all_tags + ["lineup", "index"],
    importance_score=10.0,
    screenshot_path=output_dir / "images" / "news_lineup.png",
    manifest_path=output_dir / "manifest.json",
    index_path=INDEX_PATH,
))
```

---

### M3 — Registering content from the crawler

Register entries after `briefing_composer.py` produces output. Set `source_id` and `source_name` from the crawled source definition, and pass `is_breaking=True` when the crawler flags an urgent article.

```python
mgr.add_entry(make_entry(
    id=f"m3_{timestamp}",
    module="M3",
    content_type="video",          # or "screenshot" / "image"
    title=article.title,
    topic_tags=article.topics,     # English — from GeminiAnalyst output
    description=article.summary,
    source_id=source.id,           # e.g. "centcom"
    source_name=source.display_name,
    importance_score=article.importance_score,
    is_breaking=article.is_breaking,
    video_path=composed_video_path,
    screenshot_path=screenshot_path,
    index_path=INDEX_PATH,
))
```

To promote an existing entry to breaking status without re-registering:

```python
mgr.set_breaking("m3_20260315_094500", is_breaking=True)
```

---

### M4 — Searching and playing content

M4 queries the registry in response to Gemini function calls and on startup.

```python
mgr = ContentIndexManager(INDEX_PATH)

# Startup: summarise today's content
today = mgr.get_today()

# Voice command: "Show the Iran clip"
results = mgr.get_by_tags(["iran"], match_any=True)
best = results[0] if results else None
if best:
    play_video(best["video_path_abs"])
    mgr.mark_used(best["id"])

# Breaking news interrupt
breaking = mgr.get_breaking()
if breaking:
    interrupt_with(breaking[0])
```

`get_all()` returns entries with `is_breaking=True` first, then sorted by `importance_score` descending — use this order when presenting options to Gemini.

---

## `content_index.json` Top-Level Structure

```jsonc
{
  "last_updated": "2026-03-15T07:48:05.567313+00:00",  // UTC ISO 8601; updated on every write
  "entries": [ /* array of entry objects */ ]
}
```

The file is managed exclusively through `ContentIndexManager`. Do not edit it by hand during a running broadcast.

---

## Notes for Maintainers

- **Path portability:** `video_path` (relative) is safe to use after GCS sync. `video_path_abs` reflects the machine that produced the content — on Windows broadcast stations, M4 should prefer the absolute path only when the relative path cannot be resolved.
- **ffprobe dependency:** `_probe_duration()` silently returns `None` if `ffprobe` is not on `PATH`. Always verify `duration_seconds` is populated when the entry reaches M4.
- **Thread safety:** `ContentIndexManager` is safe to use from multiple threads within one process. Concurrent writes from separate processes (e.g. M1 and M3 running simultaneously on GCE) rely on OS-level atomic rename; this is safe on Linux but should be avoided on network filesystems.
- **GCS sync:** After GCE pushes `content_index.json` to GCS, the Windows station replaces its local copy entirely. M4 should reload the index after each `pull_from_gcs.ps1` run.

---
---

# コンテンツインデックス — 開発者リファレンス

> **ファイル:** `WeaveCastStudio/content_index.py`  
> **データストア:** `WeaveCastStudio/content_index.json`  
> **対象読者:** M1・M3（プロデューサー）および M4（コンシューマー）の実装担当者

---

## 概要

`content_index.py` は、バックエンドパイプラインモジュール（M1・M3）とライブ放送クライアント（M4）をつなぐ共有レジストリです。

- **M1** は映像合成（Phase 4/5）完了後、ブリーフィング動画とトピック別クリップを登録します。
- **M3** は ファクトチェッカー／クローラーパイプラインで生成したスクリーンショットや短尺クリップを登録します。
- **M4** は放送中にこのレジストリを検索し、音声コマンドに応じてコンテンツを再生・表示します。

すべての状態はプロジェクトルートの `content_index.json` に永続化されます。書き込みは `threading.Lock` でスレッドセーフです。ファイルはアトミックに書き込まれます（テンポラリファイル経由のリネーム）。

---

## 公開 API

### `make_entry(...)` → `dict`

エントリ dict を構築するファクトリ関数です。dict を手で作らず、必ずこの関数を使ってください。

```python
from content_index import ContentIndexManager, make_entry

entry = make_entry(
    id="m1_20260315_073422",
    module="M1",
    content_type="video",
    title="Iran Conflict: Official Government Positions",
    topic_tags=["iran", "military", "hormuz"],
    description="ホルムズ海峡封鎖とイラン指導部に関するブリーフィング。",
    importance_score=9.5,
    is_breaking=False,
    video_path="compe_M1/output/briefing_20260315_073422/video/briefing.mp4",
    manifest_path="compe_M1/output/briefing_20260315_073422/manifest.json",
)
```

**主な挙動:**
- `video_path`・`screenshot_path`・`manifest_path` は**相対パスと絶対パスの両方**で保存されます。相対パスは `content_index.json` が置かれているディレクトリを起点に計算されます。
- `duration_seconds` を省略し `video_path` を指定した場合、`ffprobe` で自動取得されます。`ffprobe` が利用できない環境では明示的に指定してください。
- `created_at` を省略すると現在の UTC 時刻が ISO 8601 形式で設定されます。
- `index_path` を渡すことで `content_index.json` の場所を上書きできます（テスト時などに便利です）。

---

### `ContentIndexManager`

レジストリの読み書き・検索を行うメインインターフェースです。

```python
mgr = ContentIndexManager()                           # デフォルトパス
mgr = ContentIndexManager("/custom/path/index.json")  # カスタムパス
```

#### 書き込みメソッド

| メソッド | 説明 |
|--------|-------------|
| `add_entry(entry)` | `id` が一致する既存エントリを上書きして追加します。 |
| `remove_entry(entry_id)` | `id` でエントリを削除します。見つかった場合 `True` を返します。 |
| `set_breaking(entry_id, is_breaking)` | `is_breaking` フラグを更新します。 |
| `mark_used(entry_id)` | M4 が再生後に `used_in_broadcast=True` をセットします。 |

#### 読み込みメソッド

| メソッド | 説明 |
|--------|-------------|
| `get_all(sort_by_importance)` | 全エントリを返します。速報が先頭、次に `importance_score` の降順。 |
| `get_by_module(module)` | `"M1"` または `"M3"` でフィルタリングします。 |
| `get_by_tags(tags, match_any)` | タグ検索（大文字小文字無視）。`match_any=True` で OR 検索。 |
| `get_by_type(content_type)` | `"video"`・`"screenshot"`・`"image"` でフィルタリングします。 |
| `get_breaking()` | `is_breaking=True` の全エントリを返します。 |
| `get_today()` | 本日（UTC）作成されたエントリを返します。M4 起動時に使用されます。 |
| `get_stats()` | デバッグ・監視用のサマリーカウントを返します。 |

---

## エントリスキーマ

`content_index.json` の各エントリは以下の構造に従います。

```jsonc
{
  // ── 識別情報 ───────────────────────────────────────────────────────
  "id": "m1_briefing_20260315_073422",   // 一意な文字列 ID
  "module": "M1",                         // "M1" | "M3"
  "type": "video",                        // "video" | "screenshot" | "image"

  // ── 検索用情報 ────────────────────────────────────────────────────
  "title": "国際情勢ブリーフィング 2026-03-15",
  "description": "...",                   // 任意。M4 のセマンティック検索で Gemini に渡される。
  "topic_tags": ["iran", "hormuz", "military"],  // 常に英語

  // ── ソース情報（M3 のみ） ─────────────────────────────────────────
  "source_id": null,                      // sources.yaml のキー（例: "centcom"）
  "source_name": null,                    // クロール対象ソースの表示名

  // ── 重要度 ────────────────────────────────────────────────────────
  "importance_score": 9.5,               // 0.0〜10.0、Gemini がスコアリング
  "is_breaking": false,                  // true = M4 が現在の再生を中断して割り込み表示

  // ── ファイルパス ──────────────────────────────────────────────────
  "video_path": "compe_M1/output/.../video/briefing.mp4",      // 相対パス
  "video_path_abs": "C:/Users/.../video/briefing.mp4",         // 絶対パス
  "duration_seconds": 463.025,

  "screenshot_path": null,               // スクリーンショット／画像の相対パス
  "screenshot_path_abs": null,

  "manifest_path": "compe_M1/output/.../manifest.json",
  "manifest_path_abs": "C:/Users/.../manifest.json",

  // ── ライフサイクル ────────────────────────────────────────────────
  "created_at": "2026-03-15T07:48:05.385142+00:00",  // UTC ISO 8601
  "used_in_broadcast": false             // M4 が再生後に mark_used() で true にセット
}
```

### フィールドリファレンス

| フィールド | 型 | 必須 | 説明 |
|-------|------|----------|-------------|
| `id` | string | ✅ | 一意な識別子。命名規則は下記参照。 |
| `module` | string | ✅ | `"M1"` または `"M3"`。 |
| `type` | string | ✅ | `"video"`・`"screenshot"`・`"image"` のいずれか。 |
| `title` | string | ✅ | 人間が読めるタイトル。`LANGUAGE` 環境変数に従った言語で設定。 |
| `description` | string\|null | — | 自由記述のサマリー。M4 がセマンティックマッチングのために Gemini に渡す。 |
| `topic_tags` | string[] | ✅ | **常に英語。** `get_by_tags()` のタグ検索に使用。 |
| `source_id` | string\|null | — | M3 専用。`sources.yaml` のキー。 |
| `source_name` | string\|null | — | M3 専用。クロール対象ソースの表示名。 |
| `importance_score` | float\|null | — | 0.0〜10.0。`get_all()` で高スコア順に並ぶ。 |
| `is_breaking` | bool | ✅ | 速報フラグ。M4 は `get_breaking()` をポーリングして割り込み再生をトリガーする。 |
| `video_path` | string\|null | — | `content_index.json` ディレクトリからの相対パス。 |
| `video_path_abs` | string\|null | — | 絶対パス。M4 がファイル直接アクセスに使用。 |
| `duration_seconds` | float\|null | — | 動画の長さ（秒）。省略時は `ffprobe` で自動取得。 |
| `screenshot_path` | string\|null | — | スクリーンショット／画像の相対パス。 |
| `screenshot_path_abs` | string\|null | — | スクリーンショット／画像の絶対パス。 |
| `manifest_path` | string\|null | — | `manifest.json` / `briefing_plan.json` の相対パス。 |
| `manifest_path_abs` | string\|null | — | manifest の絶対パス。 |
| `created_at` | string | ✅ | UTC ISO 8601 タイムスタンプ。 |
| `used_in_broadcast` | bool | ✅ | 再生後に M4 が `mark_used()` で `true` にセットする。 |

---

## ID 命名規則

| モジュール | パターン | 例 |
|--------|---------|---------|
| M1 フルブリーフィング | `m1_briefing_<YYYYMMDD_HHMMSS>` | `m1_briefing_20260315_073422` |
| M1 個別クリップ | `m1_briefing_<YYYYMMDD_HHMMSS>_clip_<NNN>` | `m1_briefing_20260315_073422_clip_001` |
| M1 ラインアップ画像 | `m1_briefing_<YYYYMMDD_HHMMSS>_lineup` | `m1_briefing_20260315_073422_lineup` |
| M3 記事クリップ | `m3_<YYYYMMDD_HHMMSS>` | `m3_20260315_094500` |

ID はすべてのモジュール間で一意でなければなりません。タイムスタンプ部分はエントリを登録した時刻ではなく、コンテンツが生成された時刻を使用してください。

---

## トピックタグ

`topic_tags` は `LANGUAGE` 環境変数の設定に関わらず**常に英語**で記述してください。これによりモジュール間のフィルタリングが一貫して機能します。

```python
# ✅ 正しい
topic_tags=["iran", "airstrikes", "civilian", "humanitarian"]

# ❌ 誤り — 日本語タグはモジュール横断の get_by_tags() 検索を壊す
topic_tags=["イラン", "空爆", "民間人"]
```

M4 の Gemini ファンクションコールハンドラーは、`get_by_tags()` を呼ぶ前に検索キーワードを英語に正規化します。

---

## モジュール別インテグレーションガイド

### M1 — 映像合成後のコンテンツ登録

Phase 4（映像合成）または Phase 5（アップロード）の末尾で `add_entry` を呼び出します。出力ファイルごとに 1 エントリを登録します（フルブリーフィング・各トピッククリップ・ラインアップ画像）。

```python
from content_index import ContentIndexManager, make_entry
from pathlib import Path

INDEX_PATH = Path(__file__).parents[1] / "content_index.json"

mgr = ContentIndexManager(INDEX_PATH)

# フルブリーフィング動画
mgr.add_entry(make_entry(
    id=f"m1_briefing_{timestamp}",
    module="M1",
    content_type="video",
    title=f"国際情勢ブリーフィング {date_str}",
    topic_tags=all_tags,
    importance_score=9.5,
    video_path=output_dir / "video" / "briefing.mp4",
    manifest_path=output_dir / "manifest.json",
    index_path=INDEX_PATH,
))

# トピック別クリップ
for i, clip in enumerate(clips, start=1):
    mgr.add_entry(make_entry(
        id=f"m1_briefing_{timestamp}_clip_{i:03d}",
        module="M1",
        content_type="video",
        title=clip.title,
        topic_tags=clip.tags + ["clip"],
        importance_score=clip.score,
        video_path=clip.path,
        manifest_path=output_dir / "manifest.json",
        index_path=INDEX_PATH,
    ))

# ラインアップ画像
mgr.add_entry(make_entry(
    id=f"m1_briefing_{timestamp}_lineup",
    module="M1",
    content_type="image",
    title=f"本日のニュース一覧 {date_str}",
    topic_tags=all_tags + ["lineup", "index"],
    importance_score=10.0,
    screenshot_path=output_dir / "images" / "news_lineup.png",
    manifest_path=output_dir / "manifest.json",
    index_path=INDEX_PATH,
))
```

---

### M3 — クローラーからのコンテンツ登録

`briefing_composer.py` が出力を生成した後にエントリを登録します。クロール対象ソース定義から `source_id` と `source_name` を設定し、クローラーが緊急記事を検出した場合は `is_breaking=True` を渡します。

```python
mgr.add_entry(make_entry(
    id=f"m3_{timestamp}",
    module="M3",
    content_type="video",          # または "screenshot" / "image"
    title=article.title,
    topic_tags=article.topics,     # 英語 — GeminiAnalyst の出力から取得
    description=article.summary,
    source_id=source.id,           # 例: "centcom"
    source_name=source.display_name,
    importance_score=article.importance_score,
    is_breaking=article.is_breaking,
    video_path=composed_video_path,
    screenshot_path=screenshot_path,
    index_path=INDEX_PATH,
))
```

再登録せずに既存エントリを速報扱いにする場合:

```python
mgr.set_breaking("m3_20260315_094500", is_breaking=True)
```

---

### M4 — コンテンツの検索と再生

M4 は Gemini ファンクションコールへの応答時および起動時にレジストリを参照します。

```python
mgr = ContentIndexManager(INDEX_PATH)

# 起動時: 本日のコンテンツを集計
today = mgr.get_today()

# 音声コマンド: 「イランのクリップを流して」
results = mgr.get_by_tags(["iran"], match_any=True)
best = results[0] if results else None
if best:
    play_video(best["video_path_abs"])
    mgr.mark_used(best["id"])

# 速報割り込み
breaking = mgr.get_breaking()
if breaking:
    interrupt_with(breaking[0])
```

`get_all()` は `is_breaking=True` のエントリを先頭に返し、次に `importance_score` の降順で並べます。Gemini に候補を提示する際はこの順序をそのまま使用してください。

---

## `content_index.json` トップレベル構造

```jsonc
{
  "last_updated": "2026-03-15T07:48:05.567313+00:00",  // 書き込みのたびに更新される UTC ISO 8601
  "entries": [ /* エントリオブジェクトの配列 */ ]
}
```

このファイルは `ContentIndexManager` 経由でのみ操作してください。放送中の手動編集は避けてください。

---

## メンテナー向けメモ

- **パスの移植性:** `video_path`（相対パス）は GCS 同期後も安全に使用できます。`video_path_abs` はコンテンツを生成したマシンのパスを反映しています。Windows 放送局の M4 では、相対パスで解決できない場合のみ絶対パスを使用してください。
- **ffprobe 依存:** `ffprobe` が `PATH` に存在しない場合、`_probe_duration()` はサイレントに `None` を返します。エントリが M4 に到達したとき `duration_seconds` が確実に設定されているか確認してください。
- **スレッドセーフ:** `ContentIndexManager` は同一プロセス内の複数スレッドから安全に使用できます。別プロセスからの同時書き込み（例: GCE 上で M1 と M3 が同時実行される場合）は OS レベルのアトミックリネームに依存します。Linux では安全ですが、ネットワークファイルシステム上での使用は避けてください。
- **GCS 同期:** GCE が `content_index.json` を GCS にプッシュした後、Windows 局はローカルコピーを丸ごと置き換えます。M4 は `pull_from_gcs.ps1` 実行後にインデックスを再読み込みしてください。

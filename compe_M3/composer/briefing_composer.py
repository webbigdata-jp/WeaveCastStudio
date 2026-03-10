"""
composer/briefing_composer.py

M3 Phase 3: ブリーフィング動画生成オーケストレーター

処理フロー:
  1. ArticleStore から分析済み記事を取得
  2. Gemini で OSINT ブリーフィング原稿を生成
  3. shared/ (M1 agents) を使って画像生成 → TTS → 動画合成
  4. data/output/briefing_<timestamp>/ に成果物を保存

M1 との差分:
  - 入力: ArticleStore の分析済み記事群（各国政府見解JSONではなくOSINT記事）
  - 出力パス: data/output/briefing_<timestamp>/
  - script_writer は M3 専用プロンプトで再実装（generate_briefing_script は使わない）
  - image_generator / narrator / video_composer は shared/ 経由で M1 をそのまま再利用
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent))  # GeminiLiveAgent/
from content_index import ContentIndexManager, make_entry

from google import genai

from analyst.gemini_client import GeminiClient
from store.article_store import ArticleStore

# shared/ は compe_M1/agents/ へのシンボリックリンク
# sys.path への追加は呼び出し元（main / test）で行う
from shared.image_generator import generate_title_slide, generate_content_images
from shared.narrator import generate_narration
from shared.video_composer import compose_video

logger = logging.getLogger(__name__)

# ブリーフィングに使用する記事の上限
_MAX_ARTICLES = 12

# 原稿生成モデル
_SCRIPT_MODEL = "gemini-2.5-flash-lite"

# 出力ルートディレクトリ
_OUTPUT_ROOT = Path("data/output")


def _make_output_dir() -> Path:
    """実行単位の出力ディレクトリを作成して返す"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _OUTPUT_ROOT / f"briefing_{ts}"
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "video").mkdir(parents=True, exist_ok=True)
    return out


def _build_m3_briefing_data(articles: list[dict]) -> dict:
    """
    ArticleStore の分析済み記事群を M3 ブリーフィング用の中間構造に変換する。

    M1 の briefing_data（各国政府見解）とは異なり、
    M3 では OSINT 記事を importance_score 降順で並べたセクション構造にする。
    """
    sections = []
    for a in articles:
        sections.append({
            "title": a.get("title", ""),
            "source_name": a.get("source_name", ""),
            "source_url": a.get("url", ""),
            "credibility": a.get("credibility", 0),
            "tier": a.get("tier", 0),
            "importance_score": a.get("importance_score") or 0.0,
            "summary": a.get("summary", ""),
            "topics": _parse_json_field(a.get("topics"), []),
            "key_entities": _parse_json_field(a.get("key_entities"), []),
            "sentiment": a.get("sentiment", "neutral"),
            "has_actionable_intel": bool(a.get("has_actionable_intel")),
        })

    # importance_score 降順でソート
    sections.sort(key=lambda x: x["importance_score"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(sections),
        "sections": sections,
    }


def _parse_json_field(value, default):
    """SQLite から取得した JSON 文字列フィールドをパースする"""
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, ValueError):
            pass
    return default


def generate_m3_script(
    gemini_client: GeminiClient,
    briefing_data: dict,
    focus_topics: list[str] | None = None,
    user_instruction: str | None = None,
) -> str:
    """
    M3 OSINT ブリーフィング用の原稿を生成する。
    M1 の generate_briefing_script とは別実装（OSINT向けプロンプト）。

    Args:
        gemini_client: GeminiClient インスタンス
        briefing_data: _build_m3_briefing_data() の出力
        focus_topics: 注目するトピック（例: ["military", "humanitarian"]）
        user_instruction: 追加指示（将来の M4 連携用）
    Returns:
        [IMAGE: ...] マーカー付きの原稿テキスト
    """
    focus_str = (
        f"\nFocus especially on these topics: {', '.join(focus_topics)}"
        if focus_topics else ""
    )
    instruction_str = (
        f"\nAdditional instruction: {user_instruction}"
        if user_instruction else ""
    )

    prompt = f"""You are a professional OSINT intelligence briefing anchor for StoryWire.
Write a 5-8 minute news briefing script based on the following intelligence reports.

INTELLIGENCE REPORTS (sorted by importance):
{json.dumps(briefing_data["sections"], indent=2, ensure_ascii=False)}
{focus_str}
{instruction_str}

SCRIPT REQUIREMENTS:
- Open with a 2-sentence situation overview summarizing the top developments
- Cover the top 5-7 stories, ordered by importance_score
- For each story: mention the source name and credibility tier, give 2-3 sentences of analysis
- Flag any items with has_actionable_intel=true with "ACTIONABLE INTELLIGENCE:" prefix
- Group stories by topics where natural (military, humanitarian, diplomatic)
- Close with a 2-sentence outlook
- Total word count: 700-1000 words
- Professional, measured BBC World Service tone
- Write in English only

IMAGE MARKERS:
Insert [IMAGE: description] markers between paragraphs (NOT mid-sentence) at these points:
- After opening overview: [IMAGE: OSINT Intelligence Briefing title card with StoryWire branding]
- After every 2nd story: [IMAGE: infographic or visualization relevant to the story just covered]
- Before closing: [IMAGE: Summary dashboard showing top stories with importance scores]

Output the script text only. No preamble, no markdown formatting."""

    logger.info("[Composer] Generating M3 briefing script...")
    script = gemini_client.generate_text(
        prompt=prompt,
        model=_SCRIPT_MODEL,
        temperature=0.3,
        max_output_tokens=8192,
    )
    logger.info(f"[Composer] Script generated: {len(script.split())} words")
    return script


def _generate_short_clip_script(
    gemini_client: GeminiClient,
    article: dict,
) -> str:
    """
    記事1件から30秒ショートクリップ用の原稿を生成する。

    構成: headline 1文 + detail 2文 + closing 1文（計75語固定）
    画像マーカーは挿入しない（短尺は1枚固定のため不要）。

    Args:
        gemini_client: GeminiClient インスタンス
        article: ArticleStore から取得した分析済み記事 dict
    Returns:
        75語程度の原稿テキスト（画像マーカーなし）
    """
    prompt = f"""You are a professional OSINT news anchor for StoryWire.
Write a 30-second news clip script (exactly 4 sentences, ~75 words) for the following article.

ARTICLE:
Title: {article.get("title", "")}
Source: {article.get("source_name", "")} (Credibility: {article.get("credibility", "?")}/5)
Summary: {article.get("summary", "")}
Topics: {", ".join(_parse_json_field(article.get("topics"), []))}
Key entities: {", ".join(_parse_json_field(article.get("key_entities"), []))}
Importance score: {article.get("importance_score", 5.0):.1f}/10
Actionable intel: {article.get("has_actionable_intel", False)}

SCRIPT STRUCTURE (strictly 4 sentences):
1. HEADLINE sentence — state the core development in one punchy sentence
2. DETAIL sentence 1 — provide the key context or background
3. DETAIL sentence 2 — add the most important implication or next development
4. CLOSING sentence — brief forward-looking statement or significance

REQUIREMENTS:
- Total word count: 70-80 words (targeting 75)
- Professional BBC World Service tone
- English only
- No image markers, no markdown, no preamble
- Output the 4-sentence script only"""

    logger.info(f"[ShortClip] Generating script for: {article.get('title', '')[:60]}")
    script = gemini_client.generate_text(
        prompt=prompt,
        model=_SCRIPT_MODEL,
        temperature=0.3,
        max_output_tokens=256,
    )
    word_count = len(script.split())
    logger.info(f"[ShortClip] Script generated: {word_count} words")
    return script


    """
    M3 ブリーフィング動画生成のオーケストレーター。

    Usage:
        composer = BriefingComposer()
        result = composer.compose(hours=6)
        print(result["video_path"])
    """

    def __init__(self, env_path: str | None = None):
        self._gemini = GeminiClient(env_path=env_path)
        self._store = ArticleStore()
        # M1 agents は genai.Client を直接受け取るため raw client も保持
        self._raw_client: genai.Client = self._gemini._client

    def compose(
        self,
        hours: int = 6,
        focus_topics: list[str] | None = None,
        user_instruction: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        ブリーフィング動画を生成して成果物パスを返す。

        Args:
            hours: 直近何時間の記事を対象にするか
            focus_topics: 注目トピック（例: ["military", "humanitarian"]）
            user_instruction: 追加指示（M4 連携用）
            dry_run: True の場合、原稿生成まで行い動画生成をスキップする
        Returns:
            dict:
                {
                  "output_dir": str,
                  "script_path": str,
                  "briefing_plan_path": str,
                  "video_path": str | None,   # dry_run=True の場合は None
                  "article_count": int,
                }
        """
        out_dir = _make_output_dir()
        logger.info(f"[Composer] Output dir: {out_dir}")

        # ── STEP 1: 分析済み記事を取得 ──
        articles = self._store.get_top_articles(
            limit=_MAX_ARTICLES, hours=hours
        )
        if not articles:
            raise RuntimeError(
                f"直近 {hours} 時間に分析済み記事がありません。"
                "test_phase1.py → test_phase2.py を先に実行してください。"
            )
        logger.info(f"[Composer] {len(articles)} articles loaded (top by importance)")

        # ── STEP 2: M3用中間構造に変換 ──
        briefing_data = _build_m3_briefing_data(articles)
        briefing_plan_path = out_dir / "briefing_plan.json"
        briefing_plan_path.write_text(
            json.dumps(briefing_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"[Composer] Briefing plan saved: {briefing_plan_path}")

        # ── STEP 3: 原稿生成 ──
        script = generate_m3_script(
            self._gemini,
            briefing_data,
            focus_topics=focus_topics,
            user_instruction=user_instruction,
        )
        script_path = out_dir / "script.txt"
        script_path.write_text(script, encoding="utf-8")
        logger.info(f"[Composer] Script saved: {script_path}")

        # M1 の generate_title_slide が必要とする topic 形式
        topic = {
            "title": "OSINT Intelligence Briefing",
            "timestamp": briefing_data["generated_at"],
        }

        result = {
            "output_dir": str(out_dir),
            "script_path": str(script_path),
            "briefing_plan_path": str(briefing_plan_path),
            "video_path": None,
            "article_count": len(articles),
        }

        if dry_run:
            logger.info("[Composer] dry_run=True: skipping image/audio/video generation")
            return result

        # ── STEP 4: 画像生成（M1 shared 再利用）──
        logger.info("[Composer] Generating images...")
        title_slide = generate_title_slide(
            self._raw_client, topic, out_dir / "images"
        )
        content_slides = generate_content_images(
            self._raw_client, script, out_dir / "images"
        )
        logger.info(
            f"[Composer] Images: 1 title + {len(content_slides)} content slides"
        )

        # ── STEP 5: TTS 音声生成（M1 shared 再利用）──
        logger.info("[Composer] Generating narration (TTS)...")
        audio_segments = generate_narration(
            self._raw_client, script, out_dir / "audio"
        )
        logger.info(f"[Composer] Audio: {len(audio_segments)} segments")

        # ── STEP 6: 動画合成（M1 shared 再利用）──
        logger.info("[Composer] Composing video...")
        video_path = compose_video(
            title_slide=title_slide,
            content_slides=content_slides,
            audio_segments=audio_segments,
            output_dir=out_dir,
        )
        logger.info(f"[Composer] Video: {video_path}")

        # 使用した記事を briefing 使用済みにマーク
        used_ids = [a["id"] for a in articles if a.get("id")]
        self._store.mark_used_in_briefing(used_ids)

        result["video_path"] = str(video_path)

        # ── ContentIndex に登録（動画生成時）──
        if result["video_path"]:
            mgr = ContentIndexManager()
            # ブリーフィング全体動画
            all_tags = []
            for a in articles:
                all_tags.extend(_parse_json_field(a.get("topics"), []))
            unique_tags = list(dict.fromkeys(all_tags))  # 順序保持重複除去

            entry = make_entry(
                id=f"m3_briefing_{out_dir.name}",
                module="M3",
                content_type="video",
                title=f"OSINT Intelligence Briefing - {datetime.now(timezone.utc).date()}",
                topic_tags=unique_tags or ["osint", "briefing"],
                importance_score=max(
                    (a.get("importance_score") or 0.0) for a in articles
                ),
                video_path=result["video_path"],
                manifest_path=result["briefing_plan_path"],
            )
            mgr.add_entry(entry)
            logger.info(f"[Composer] ContentIndex registered: {entry['id']}")

        # ── スクリーンショットも個別登録 ──
        mgr = ContentIndexManager()
        for article in articles:
            shot = article.get("screenshot_path")
            if not shot or not Path(shot).exists():
                continue
            tags = _parse_json_field(article.get("topics"), [])
            entry = make_entry(
                id=f"m3_{article['source_id']}_{article['id']}",
                module="M3",
                content_type="screenshot",
                title=article.get("title", ""),
                topic_tags=tags,
                source_id=article.get("source_id"),
                source_name=article.get("source_name"),
                importance_score=article.get("importance_score"),
                screenshot_path=shot,
            )
            mgr.add_entry(entry)


        return result

    # ──────────────────────────────────────────
    # 短尺クリップ生成（記事1件 → 30秒動画1本）
    # ──────────────────────────────────────────

    def compose_short_clips(
        self,
        hours: int = 6,
        article_ids: list[int] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        分析済み記事1件につき30秒のショートクリップを生成する。

        M4から「イランの動画を出して」のように記事単位で検索・再生できるよう、
        各クリップをContentIndexに個別登録する。

        出力ディレクトリ: data/output/clips_<timestamp>/
          clip_001/
            script.txt
            audio/
            images/
            clip_001.mp4
          clip_002/
            ...
          clips_manifest.json   ← 全クリップのサマリ

        Args:
            hours: 直近何時間の記事を対象にするか（article_ids指定時は無視）
            article_ids: 特定記事IDを指定する場合（None=直近hours時間の全記事）
            dry_run: True の場合、原稿生成まで行い動画生成をスキップする
        Returns:
            dict:
                {
                  "output_dir": str,
                  "clips_manifest_path": str,
                  "clips": [
                    {
                      "article_id": int,
                      "title": str,
                      "script_path": str,
                      "video_path": str | None,
                      "content_index_id": str | None,
                    }, ...
                  ],
                  "total": int,
                  "succeeded": int,
                }
        """
        # ── 出力ディレクトリ ──
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_root = _OUTPUT_ROOT / f"clips_{ts}"
        out_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"[ShortClip] Output dir: {out_root}")

        # ── 記事取得 ──
        if article_ids:
            articles = [
                a for a in self._store.get_top_articles(limit=100, hours=hours * 10)
                if a.get("id") in article_ids
            ]
        else:
            articles = self._store.get_top_articles(limit=_MAX_ARTICLES, hours=hours)

        if not articles:
            raise RuntimeError(
                f"直近 {hours} 時間に分析済み記事がありません。"
                "test_phase1.py → test_phase2.py を先に実行してください。"
            )
        logger.info(f"[ShortClip] {len(articles)} articles to process")

        clips_result: list[dict] = []
        succeeded = 0

        for idx, article in enumerate(articles, start=1):
            clip_name = f"clip_{idx:03d}"
            clip_dir = out_root / clip_name
            (clip_dir / "audio").mkdir(parents=True, exist_ok=True)
            (clip_dir / "images").mkdir(parents=True, exist_ok=True)

            logger.info(
                f"[ShortClip] [{idx}/{len(articles)}] "
                f"{article.get('title', '')[:60]}"
            )

            clip_entry: dict = {
                "article_id": article.get("id"),
                "title": article.get("title", ""),
                "script_path": None,
                "video_path": None,
                "content_index_id": None,
            }

            try:
                # ── 原稿生成（75語 / 30秒） ──
                script = _generate_short_clip_script(self._gemini, article)
                script_path = clip_dir / "script.txt"
                script_path.write_text(script, encoding="utf-8")
                clip_entry["script_path"] = str(script_path)

                if dry_run:
                    clips_result.append(clip_entry)
                    continue

                # ── 画像: スクリーンショットがあればそれを使用、なければタイトルカード生成 ──
                screenshot = article.get("screenshot_path")
                if screenshot and Path(screenshot).exists():
                    image_path = Path(screenshot)
                    logger.info(f"[ShortClip] Using existing screenshot: {image_path.name}")
                else:
                    topic_for_slide = {
                        "title": article.get("title", "OSINT Update"),
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    }
                    image_path = generate_title_slide(
                        self._raw_client, topic_for_slide, clip_dir / "images"
                    )
                    logger.info(f"[ShortClip] Title card generated: {image_path.name}")

                # ── TTS ──
                audio_segments = generate_narration(
                    self._raw_client, script, clip_dir / "audio"
                )

                # ── 動画合成（画像1枚 + 音声） ──
                video_path = compose_video(
                    title_slide=image_path,
                    content_slides=[],          # 短尺は1枚のみ
                    audio_segments=audio_segments,
                    output_dir=clip_dir,
                )
                logger.info(f"[ShortClip] Video: {video_path}")
                clip_entry["video_path"] = str(video_path)

                # ── ContentIndex 登録 ──
                tags = _parse_json_field(article.get("topics"), [])
                entry = make_entry(
                    id=f"m3_clip_{article['id']}_{ts}",
                    module="M3",
                    content_type="short_clip",
                    title=article.get("title", ""),
                    topic_tags=tags or ["osint"],
                    importance_score=article.get("importance_score"),
                    video_path=str(video_path),
                    manifest_path=str(clip_dir / "script.txt"),
                )
                mgr = ContentIndexManager()
                mgr.add_entry(entry)
                clip_entry["content_index_id"] = entry["id"]
                logger.info(f"[ShortClip] ContentIndex registered: {entry['id']}")

                succeeded += 1

            except Exception as e:
                logger.error(
                    f"[ShortClip] Failed for article id={article.get('id')}: {e}",
                    exc_info=True,
                )

            clips_result.append(clip_entry)

        # ── clips_manifest.json ──
        clips_manifest = {
            "module": "M3",
            "type": "short_clips",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "output_dir": str(out_root),
            "total": len(articles),
            "succeeded": succeeded,
            "dry_run": dry_run,
            "clips": clips_result,
        }
        manifest_path = out_root / "clips_manifest.json"
        manifest_path.write_text(
            json.dumps(clips_manifest, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(
            f"[ShortClip] Done: {succeeded}/{len(articles)} clips generated. "
            f"Manifest: {manifest_path}"
        )

        return {
            "output_dir": str(out_root),
            "clips_manifest_path": str(manifest_path),
            "clips": clips_result,
            "total": len(articles),
            "succeeded": succeeded,
        }



"""
composer/briefing_composer.py

M3 Phase 3: Briefing video generation orchestrator.

Processing flow:
  1. Fetch analyzed articles from ArticleStore
  2. Generate a news briefing script with Gemini
  3. Use shared/ (project root) for image generation → TTS → video composition
  4. Save all artifacts to data/output/briefing_<timestamp>/

Differences from M1:
  - Input:  analyzed articles from ArticleStore (not government-statement JSON)
  - Output: data/output/briefing_<timestamp>/
  - Script generation uses an M3-specific prompt (not generate_briefing_script)
  - image_generator / narrator / video_composer are shared with M1 via shared/

Language behaviour:
  The briefing script and short-clip scripts are generated in the language
  configured via LANGUAGE in .env.  All log messages and internal identifiers
  remain in English.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

# Add project root to sys.path (for shared/ and content_index imports)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from analyst.gemini_client import GeminiClient
from google import genai
from store.article_store import ArticleStore

from content_index import ContentIndexManager, make_entry

# shared/ lives directly under the project root — common modules shared with M1
from shared.image_generator import generate_content_images, generate_title_slide
from shared.narrator import generate_narration
from shared.video_composer import compose_video

logger = logging.getLogger(__name__)

# Maximum number of articles used in one briefing
_MAX_ARTICLES = 12

# Model for script generation
_SCRIPT_MODEL = "gemini-2.5-flash-lite"

# Root output directory
_OUTPUT_ROOT = Path("data/output")


def _make_output_dir() -> Path:
    """Create and return a timestamped output directory for this run"""
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    out = _OUTPUT_ROOT / f"briefing_{ts}"
    (out / "images").mkdir(parents=True, exist_ok=True)
    (out / "audio").mkdir(parents=True, exist_ok=True)
    (out / "video").mkdir(parents=True, exist_ok=True)
    return out


def _build_m3_briefing_data(articles: list[dict]) -> dict:
    """
    Convert analyzed ArticleStore articles into the M3 briefing intermediate structure.

    Unlike M1's briefing_data (government statements), M3 arranges articles
    as sections sorted by importance_score descending.
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

    # Sort by importance_score descending
    sections.sort(key=lambda x: x["importance_score"], reverse=True)

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "article_count": len(sections),
        "sections": sections,
    }


def _parse_json_field(value, default):
    """Parse a JSON string field retrieved from SQLite"""
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
    Generate a M3 news briefing script.
    Separate implementation from M1's generate_briefing_script
    (general-purpose news prompt rather than government-statement prompt).

    The script is written in the language configured via LANGUAGE in .env.

    Args:
        gemini_client: GeminiClient instance
        briefing_data: output of _build_m3_briefing_data()
        focus_topics: topics to emphasise (e.g. ["economy", "environment"])
        user_instruction: additional free-text instruction (reserved for M4 integration)
    Returns:
        Script text with [IMAGE: ...] markers between sections
    """
    output_lang = gemini_client.language.prompt_lang

    focus_str = (
        f"\nFocus especially on these topics: {', '.join(focus_topics)}"
        if focus_topics else ""
    )
    instruction_str = (
        f"\nAdditional instruction: {user_instruction}"
        if user_instruction else ""
    )

    # -------------------------------------------------------------------
    # NOTE FOR MAINTAINERS:
    # This prompt targets a general YouTube / independent news audience.
    # Adjust tone, structure, and word-count targets to suit your channel.
    # The {output_lang} placeholder is filled at runtime from .env LANGUAGE.
    # -------------------------------------------------------------------
    prompt = f"""You are a professional news presenter writing a script for a YouTube news briefing.
Write a 5-8 minute video script based on the following news articles.
The ENTIRE script — every word the presenter speaks — MUST be written in {output_lang}.

NEWS ARTICLES (sorted by importance score, highest first):
{json.dumps(briefing_data["sections"], indent=2, ensure_ascii=False)}
{focus_str}
{instruction_str}

SCRIPT STRUCTURE:
1. Opening (2 sentences): greet the audience and summarise today's top stories
2. Main stories (top 5-7 by importance_score):
   - State the headline clearly
   - Give 2-3 sentences of context and significance
   - Mention the source name so viewers know where the information comes from
   - If has_actionable_intel is true, flag it as a developing story worth following
3. Group thematically where it flows naturally
4. Closing (2 sentences): brief outlook or call-to-action (like/subscribe prompt is fine)

TONE & STYLE:
- Accessible and engaging — suitable for a general online audience
- Balanced and factual; do not editorialize beyond what the sources support
- Total word count: 700-1000 words
- Write ENTIRELY in {output_lang}; do NOT mix languages

IMAGE MARKERS:
Insert [IMAGE: description] between paragraphs (never mid-sentence) at these points:
- After the opening: [IMAGE: News briefing title card with today's date]
- After every 2nd story: [IMAGE: relevant graphic or infographic for the story just covered]
- Before the closing: [IMAGE: summary card listing today's top headlines]

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
    Generate a 30-second short clip script from a single article.

    Structure: 1 headline + 2 detail sentences + 1 closing (≈75 words total).
    No image markers — short clips use a single fixed image.

    The script is written in the language configured via LANGUAGE in .env.

    Args:
        gemini_client: GeminiClient instance
        article: analyzed article dict from ArticleStore
    Returns:
        4-sentence script (~75 words), no image markers
    """
    output_lang = gemini_client.language.prompt_lang

    # -------------------------------------------------------------------
    # NOTE FOR MAINTAINERS:
    # Adjust word count, sentence count, or tone to match your channel format.
    # {output_lang} is resolved at runtime from .env LANGUAGE.
    # -------------------------------------------------------------------
    prompt = f"""You are a professional news presenter writing a short video script for YouTube.
Write a 30-second news clip script (exactly 4 sentences, approximately 75 words) for the article below.
The ENTIRE script MUST be written in {output_lang}.

ARTICLE:
Title: {article.get("title", "")}
Source: {article.get("source_name", "")} (Credibility: {article.get("credibility", "?")}/5)
Summary: {article.get("summary", "")}
Topics: {", ".join(_parse_json_field(article.get("topics"), []))}
Key entities: {", ".join(_parse_json_field(article.get("key_entities"), []))}
Importance score: {article.get("importance_score", 5.0):.1f}/10

SCRIPT STRUCTURE (strictly 4 sentences, all in {output_lang}):
1. HEADLINE — state the core development in one clear, punchy sentence
2. CONTEXT — provide the key background or cause
3. SIGNIFICANCE — explain the most important implication or next development
4. CLOSING — a brief forward-looking statement or viewer call-to-action

REQUIREMENTS:
- Total word count: 70-80 words (target 75)
- Friendly and accessible tone suitable for a general online audience
- Write ENTIRELY in {output_lang}; do NOT mix languages
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

class BriefingComposer:
    """
    Orchestrates M3 briefing video generation.

    Scripts are generated in the language set by LANGUAGE in .env.

    Usage:
        composer = BriefingComposer()
        result = composer.compose(hours=6)
        print(result["video_path"])
    """

    def __init__(self, env_path: str | None = None):
        self._gemini = GeminiClient(env_path=env_path)
        self._store = ArticleStore()
        # M1 shared agents expect a raw genai.Client, so keep a reference
        self._raw_client: genai.Client = self._gemini._client

    def compose(
        self,
        hours: int = 6,
        focus_topics: list[str] | None = None,
        user_instruction: str | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Generate a briefing video and return artifact paths.

        Args:
            hours: look-back window for articles (hours)
            focus_topics: topics to emphasise (e.g. ["economy", "environment"])
            user_instruction: additional instruction (reserved for M4 integration)
            dry_run: if True, generate script only — skip image/audio/video steps
        Returns:
            dict:
                {
                  "output_dir": str,
                  "script_path": str,
                  "briefing_plan_path": str,
                  "video_path": str | None,   # None when dry_run=True
                  "article_count": int,
                }
        """
        out_dir = _make_output_dir()
        logger.info(f"[Composer] Output dir: {out_dir}")

        # ── STEP 1: Fetch analyzed articles ──
        articles = self._store.get_top_articles(
            limit=_MAX_ARTICLES, hours=hours
        )
        if not articles:
            raise RuntimeError(
                f"No analyzed articles found in the last {hours} hours. "
                "Please run: uv run main.py crawl && uv run main.py analyze"
            )
        logger.info(f"[Composer] {len(articles)} articles loaded (top by importance)")

        # ── STEP 2: Convert to M3 intermediate structure ──
        briefing_data = _build_m3_briefing_data(articles)
        briefing_plan_path = out_dir / "briefing_plan.json"
        briefing_plan_path.write_text(
            json.dumps(briefing_data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info(f"[Composer] Briefing plan saved: {briefing_plan_path}")

        # ── STEP 3: Generate script ──
        script = generate_m3_script(
            self._gemini,
            briefing_data,
            focus_topics=focus_topics,
            user_instruction=user_instruction,
        )
        script_path = out_dir / "script.txt"
        script_path.write_text(script, encoding="utf-8")
        logger.info(f"[Composer] Script saved: {script_path}")

        # topic dict format expected by M1's generate_title_slide
        topic = {
            "title": "News Briefing",
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

        # ── STEP 4: Image generation (reusing M1 shared) ──
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

        # ── STEP 5: TTS narration (reusing M1 shared) ──
        logger.info("[Composer] Generating narration (TTS)...")
        audio_segments = generate_narration(
            self._raw_client, script, out_dir / "audio"
        )
        logger.info(f"[Composer] Audio: {len(audio_segments)} segments")

        # ── STEP 6: Video composition (reusing M1 shared) ──
        logger.info("[Composer] Composing video...")
        video_path = compose_video(
            title_slide=title_slide,
            content_slides=content_slides,
            audio_segments=audio_segments,
            output_dir=out_dir,
        )
        logger.info(f"[Composer] Video: {video_path}")

        # Mark used articles as consumed by this briefing
        used_ids = [a["id"] for a in articles if a.get("id")]
        self._store.mark_used_in_briefing(used_ids)

        # ── Verify the video file was actually created ──
        if not Path(video_path).exists():
            logger.error(f"[Composer] Video file not found after compose_video(): {video_path}")
            raise RuntimeError(f"Video file was not generated: {video_path}")

        result["video_path"] = str(video_path)

        # ── Register in ContentIndex (video file confirmed to exist) ──
        if result["video_path"]:
            mgr = ContentIndexManager()
            # Full briefing video — collect all topic tags
            all_tags = []
            for a in articles:
                all_tags.extend(_parse_json_field(a.get("topics"), []))
            unique_tags = list(dict.fromkeys(all_tags))  # deduplicate preserving order

            entry = make_entry(
                id=f"m3_briefing_{out_dir.name}",
                module="M3",
                content_type="video",
                title=f"News Briefing - {datetime.now(timezone.utc).date()}",
                topic_tags=unique_tags or ["news", "briefing"],
                importance_score=max(
                    (a.get("importance_score") or 0.0) for a in articles
                ),
                video_path=result["video_path"],
                manifest_path=result["briefing_plan_path"],
            )
            mgr.add_entry(entry)
            logger.info(f"[Composer] ContentIndex registered: {entry['id']}")

        # ── Register individual screenshots in ContentIndex as well ──
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
    # Short clip generation (1 article → 1 × 30-second video)
    # ──────────────────────────────────────────

    def compose_short_clips(
        self,
        hours: int = 6,
        article_ids: list[int] | None = None,
        dry_run: bool = False,
    ) -> dict:
        """
        Generate one 30-second short clip per analyzed article.

        Each clip is registered individually in ContentIndex so M4 can search
        and play them by topic (e.g. "show me a clip about climate").

        Output layout: data/output/clips_<timestamp>/
          clip_001/
            script.txt
            audio/
            images/
            clip_001.mp4
          clip_002/
            ...
          clips_manifest.json   ← summary of all clips

        Scripts are written in the language configured via LANGUAGE in .env.

        Args:
            hours: look-back window in hours (ignored when article_ids is set)
            article_ids: restrict to specific article IDs (None = all within hours)
            dry_run: if True, generate scripts only — skip video generation
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
        # ── Output directory ──
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        out_root = _OUTPUT_ROOT / f"clips_{ts}"
        out_root.mkdir(parents=True, exist_ok=True)
        logger.info(f"[ShortClip] Output dir: {out_root}")

        # ── Fetch articles ──
        if article_ids:
            articles = [
                a for a in self._store.get_top_articles(limit=100, hours=hours * 10)
                if a.get("id") in article_ids
            ]
        else:
            articles = self._store.get_top_articles(limit=_MAX_ARTICLES, hours=hours)

        if not articles:
            raise RuntimeError(
                f"No analyzed articles found in the last {hours} hours. "
                "Please run: uv run main.py crawl && uv run main.py analyze"
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
                # ── Script generation (75 words / 30 seconds) ──
                script = _generate_short_clip_script(self._gemini, article)
                script_path = clip_dir / "script.txt"
                script_path.write_text(script, encoding="utf-8")
                clip_entry["script_path"] = str(script_path)

                if dry_run:
                    clips_result.append(clip_entry)
                    continue

                # ── Image: use existing screenshot if available, otherwise generate title card ──
                screenshot = article.get("screenshot_path")
                if screenshot and Path(screenshot).exists():
                    image_path = Path(screenshot)
                    logger.info(f"[ShortClip] Using existing screenshot: {image_path.name}")
                else:
                    topic_for_slide = {
                        "title": article.get("title", "News Update"),
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

                # ── Video composition (single image + audio) ──
                video_path = compose_video(
                    title_slide=image_path,
                    content_slides=[],          # short clips use one image only
                    audio_segments=audio_segments,
                    output_dir=clip_dir,
                )
                logger.info(f"[ShortClip] Video: {video_path}")

                # ── Verify video file was created ──
                if not Path(video_path).exists():
                    logger.error(
                        f"[ShortClip] Video file not found after compose_video(): {video_path}"
                    )
                    raise RuntimeError(f"Video file was not generated: {video_path}")

                clip_entry["video_path"] = str(video_path)

                # ── Register in ContentIndex (video file confirmed to exist) ──
                tags = _parse_json_field(article.get("topics"), [])
                entry = make_entry(
                    id=f"m3_clip_{article['id']}_{ts}",
                    module="M3",
                    content_type="short_clip",
                    title=article.get("title", ""),
                    topic_tags=tags or ["news"],
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



"""
WeaveCast M1 pipeline — main entry point.

Usage:
  python main.py                     # Process all topics (phases 1–5)
  python main.py --topic-index 1     # Process a specific topic only (backward-compat)
  python main.py --skip-upload       # Skip YouTube upload
  python main.py --phase 1           # Phase 1 only
  python main.py --phase 2           # Phase 2 only (requires Phase 1 output)
  python main.py --phase 3           # Phase 3 only
  python main.py --phase 4           # Phase 4 only
  python main.py --phase 5           # Phase 5 only

  Reuse an existing output directory with --output-dir:
  python main.py --phase 2 --output-dir output/briefing_20260310_172523
"""

import argparse
import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from google import genai

# Add project root to sys.path for shared/ and content_index imports
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from shared.source_collector import collect_all_topics
from shared.summarizer import generate_structured_summary
from shared.script_writer import generate_briefing_script, generate_clip_scripts
from shared.image_generator import (
    generate_title_slide,
    generate_news_lineup_image,
    generate_content_images,
    generate_briefing_images,
    generate_clip_image,
)
from shared.narrator import generate_narration
from shared.video_composer import compose_video

# ContentIndex
from content_index import ContentIndexManager, make_entry

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("weavecast.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("weavecast.main")

# ── Paths ─────────────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"
TOPICS_FILE = CONFIG_DIR / "topics.yaml"
ENV_FILE = _PROJECT_ROOT / ".env"   # Project-root .env shared by all modules
YT_SECRETS_FILE = CONFIG_DIR / "youtube_client_secrets.json"
YT_TOKEN_FILE = CONFIG_DIR / "youtube_token.json"


@dataclass
class OutputDirs:
    root: Path
    data: Path
    images: Path
    audio: Path
    video: Path
    clips: Path

    @classmethod
    def create(cls, base: Path) -> "OutputDirs":
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        root = base / "output" / f"briefing_{ts}"
        return cls._make(root)

    @classmethod
    def from_existing(cls, path: str | Path) -> "OutputDirs":
        root = Path(path) if not Path(path).is_absolute() else Path(path)
        if not root.is_absolute():
            root = BASE_DIR / root
        if not root.exists():
            raise FileNotFoundError(f"Output directory not found: {root}")
        return cls._make(root)

    @classmethod
    def _make(cls, root: Path) -> "OutputDirs":
        dirs = cls(
            root=root, data=root / "data", images=root / "images",
            audio=root / "audio", video=root / "video", clips=root / "clips",
        )
        for d in [dirs.data, dirs.images, dirs.audio, dirs.video, dirs.clips]:
            d.mkdir(parents=True, exist_ok=True)
        return dirs


# ── Utilities ─────────────────────────────────────────────────────────────────

def load_config() -> dict:
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_topics(config: dict, topic_index: int | None = None) -> list[dict]:
    topics = config["topics"]
    if topic_index is not None:
        if topic_index >= len(topics):
            raise IndexError(
                f"Topic index {topic_index} out of range (0–{len(topics)-1})"
            )
        return [topics[topic_index]]
    return topics


def save_json(data, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved: {path}")


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_text(text: str, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"Saved: {path}")


def load_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Phase runners ─────────────────────────────────────────────────────────────

def run_phase1(client: genai.Client, config: dict, topics: list[dict], dirs: OutputDirs) -> None:
    """Phase 1: Information collection + structured summarisation."""
    logger.info("=" * 60)
    logger.info("Phase 1: Information collection + structured summarisation")
    logger.info("=" * 60)

    screenshot_dir = dirs.data / "screenshots"
    raw_statements = collect_all_topics(client, config, screenshot_dir=screenshot_dir)
    save_json(raw_statements, dirs.data / "raw_statements.json")

    briefing_data = generate_structured_summary(client, topics, raw_statements)
    save_json(briefing_data, dirs.data / "briefing_data.json")

    tc = len([k for k in raw_statements if not k.startswith("__")])
    sc = len(briefing_data.get("briefing_sections", []))
    logger.info(f"Phase 1 complete: {tc} topic(s) collected, {sc} section(s) structured.")


def run_phase2(client: genai.Client, topics: list[dict], dirs: OutputDirs) -> None:
    """Phase 2: Script generation + image generation."""
    logger.info("=" * 60)
    logger.info("Phase 2: Script generation + image generation")
    logger.info("=" * 60)

    briefing_data = load_json(dirs.data / "briefing_data.json")
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")  # e.g. "March 15, 2026"

    # Full briefing script
    full_script = generate_briefing_script(client, briefing_data)
    save_text(full_script, dirs.data / "script.txt")

    # Per-clip scripts
    clip_scripts = generate_clip_scripts(client, briefing_data)
    save_json(clip_scripts, dirs.data / "clip_scripts.json")

    # Images
    title_slide = generate_title_slide(client, topics, dirs.images, date_str)
    lineup_image = generate_news_lineup_image(client, topics, dirs.images, date_str)
    # Generate per-topic images directly from briefing_data (no [IMAGE:] markers needed)
    content_slides = generate_briefing_images(
        client, briefing_data, topics, dirs.images, date_str,
    )

    # Per-clip images
    clip_image_paths = []
    for i, clip in enumerate(clip_scripts):
        clip_dir = dirs.clips / f"clip_{i+1:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        img_path = generate_clip_image(client, clip, clip_dir / "image.png")
        clip_image_paths.append(str(img_path))
        save_text(clip["script"], clip_dir / "script.txt")

    image_manifest = {
        "title_slide": str(title_slide),
        "news_lineup": str(lineup_image),
        "content_slides": [str(p) for p in content_slides],
        "clip_images": clip_image_paths,
    }
    save_json(image_manifest, dirs.data / "image_manifest.json")

    logger.info(
        f"Phase 2 complete: 1 title + 1 lineup + {len(content_slides)} content slide(s)"
        f" + {len(clip_image_paths)} clip image(s)."
    )


def run_phase3(client: genai.Client, dirs: OutputDirs) -> None:
    """Phase 3: TTS audio generation (full briefing + clips)."""
    logger.info("=" * 60)
    logger.info("Phase 3: Narration audio generation")
    logger.info("=" * 60)

    # Full briefing narration
    full_script = load_text(dirs.data / "script.txt")
    audio_paths = generate_narration(client, full_script, dirs.audio)
    audio_manifest = {"segments": [str(p) for p in audio_paths]}

    # Per-clip narration
    clip_scripts = load_json(dirs.data / "clip_scripts.json")
    clip_audio_paths = []
    for i, clip in enumerate(clip_scripts):
        clip_dir = dirs.clips / f"clip_{i+1:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        clip_audios = generate_narration(client, clip["script"], clip_dir)
        clip_audio_paths.append([str(p) for p in clip_audios])

    audio_manifest["clip_audios"] = clip_audio_paths
    save_json(audio_manifest, dirs.data / "audio_manifest.json")

    logger.info(
        f"Phase 3 complete: {len(audio_paths)} full segment(s)"
        f" + {len(clip_audio_paths)} clip(s)."
    )


def run_phase4(dirs: OutputDirs, topics: list[dict]) -> None:
    """Phase 4: Video composition (full briefing + clips)."""
    logger.info("=" * 60)
    logger.info("Phase 4: Video composition")
    logger.info("=" * 60)

    image_manifest = load_json(dirs.data / "image_manifest.json")
    audio_manifest = load_json(dirs.data / "audio_manifest.json")

    # Full briefing video
    title_slide = Path(image_manifest["title_slide"])
    content_slides = [Path(p) for p in image_manifest["content_slides"]]
    audio_segments = [Path(p) for p in audio_manifest["segments"]]

    output_video = compose_video(
        title_slide=title_slide,
        content_slides=content_slides,
        audio_segments=audio_segments,
        output_dir=dirs.root,
    )
    logger.info(f"Full briefing video: {output_video}")

    # Per-clip videos
    clip_scripts = load_json(dirs.data / "clip_scripts.json")
    clip_images = image_manifest.get("clip_images", [])
    clip_audios = audio_manifest.get("clip_audios", [])
    clip_video_paths = []

    clip_video_dir = dirs.video / "clips"
    clip_video_dir.mkdir(parents=True, exist_ok=True)

    for i in range(len(clip_scripts)):
        if i >= len(clip_images) or i >= len(clip_audios):
            logger.warning(f"Clip {i+1}: missing image or audio — skipping.")
            continue

        clip_dir = dirs.clips / f"clip_{i+1:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)

        topic_title = clip_scripts[i].get("topic_title", f"clip_{i+1}")
        safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in topic_title)
        clip_video_path = clip_video_dir / f"clip_{i+1:03d}_{safe_title}.mp4"

        try:
            clip_img = Path(clip_images[i])
            clip_auds = [Path(p) for p in clip_audios[i]]

            # Merged audio saved inside clip_dir; video output goes to video/clips/
            compose_video(
                title_slide=clip_img,
                content_slides=[],
                audio_segments=clip_auds,
                output_dir=clip_dir,
                output_video_path=clip_video_path,
                merged_audio_path=clip_dir / "full_narration.wav",
            )
            if clip_video_path.exists():
                clip_video_paths.append(str(clip_video_path))
                logger.info(f"Clip video: {clip_video_path}")
            else:
                logger.warning(f"Clip {i+1}: video not created.")
        except Exception as e:
            logger.error(f"Clip {i+1} composition failed: {e}")

    # Update manifest
    _write_manifest(dirs, topics, video_path=output_video, clip_video_paths=clip_video_paths)
    logger.info(f"Phase 4 complete: 1 full video + {len(clip_video_paths)} clip(s).")


def _write_manifest(
    dirs: OutputDirs, topics: list[dict],
    video_path: Path | None = None,
    clip_video_paths: list[str] | None = None,
    youtube_url: str | None = None,
    content_index_ids: list[str] | None = None,
) -> Path:
    manifest_path = dirs.root / "manifest.json"
    existing = {}
    if manifest_path.exists():
        try:
            existing = load_json(manifest_path)
        except Exception:
            pass

    topic_tags = list(set(tag for t in topics for tag in t.get("tags", [])))

    manifest = {
        **existing,
        "module": "M1",
        "brand": "WeaveCast",
        "generated_at": existing.get("generated_at", datetime.now(timezone.utc).isoformat()),
        "topics": [
            {
                "title_en": t.get("title_en", ""),
                "title_target_lang": t.get("title_target_lang", t.get("title_en", "")),
                "tags": t.get("tags", []),
                "importance_score": t.get("importance_score", 7.0),
            }
            for t in topics
        ],
        "topic_tags": topic_tags,
        "artifacts": {
            "video": str(video_path) if video_path else existing.get("artifacts", {}).get("video"),
            "clip_videos": clip_video_paths or existing.get("artifacts", {}).get("clip_videos", []),
            "script": str(dirs.data / "script.txt"),
            "clip_scripts": str(dirs.data / "clip_scripts.json"),
            "briefing_data": str(dirs.data / "briefing_data.json"),
            "image_manifest": str(dirs.data / "image_manifest.json"),
            "audio_manifest": str(dirs.data / "audio_manifest.json"),
            "news_lineup": existing.get("artifacts", {}).get("news_lineup"),
        },
        "output_dir": str(dirs.root),
    }

    if youtube_url:
        manifest["youtube_url"] = youtube_url
    if content_index_ids:
        manifest["content_index_ids"] = content_index_ids

    # Populate news_lineup from image_manifest if not already set
    img_manifest_path = dirs.data / "image_manifest.json"
    if img_manifest_path.exists() and not manifest["artifacts"]["news_lineup"]:
        try:
            img_data = load_json(img_manifest_path)
            manifest["artifacts"]["news_lineup"] = img_data.get("news_lineup")
        except Exception:
            pass

    save_json(manifest, manifest_path)
    return manifest_path


def run_phase5(dirs: OutputDirs, topics: list[dict]) -> None:
    """Phase 5: ContentIndex registration (+ optional YouTube upload)."""
    logger.info("=" * 60)
    logger.info("Phase 5: ContentIndex registration")
    logger.info("=" * 60)

    manifest = load_json(dirs.root / "manifest.json")
    ts = dirs.root.name   # briefing_YYYYMMDD_HHMMSS
    topic_tags = manifest.get("topic_tags", [])
    content_index_ids = []

    mgr = ContentIndexManager()

    # Load clip scripts upfront (also used for full-briefing description)
    clip_scripts_data = []
    clip_scripts_path = dirs.data / "clip_scripts.json"
    if clip_scripts_path.exists():
        clip_scripts_data = load_json(clip_scripts_path)

    # Full briefing video
    video_path = manifest["artifacts"].get("video")
    if video_path and Path(video_path).exists():
        entry_id = f"m1_{ts}"
        date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        clip_titles = [
            c.get("topic_title", f"Topic {i+1}")
            for i, c in enumerate(clip_scripts_data)
        ]
        briefing_desc = f"{date_str} international news briefing."
        if clip_titles:
            briefing_desc += " Topics: " + " / ".join(clip_titles)

        entry = make_entry(
            id=entry_id, module="M1", content_type="video",
            title=f"International News Briefing {date_str}",
            description=briefing_desc,
            topic_tags=topic_tags + ["briefing", "full"],
            importance_score=max((t.get("importance_score", 7) for t in topics), default=7),
            video_path=video_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] Full briefing registered: {entry_id}")

    # Clip videos
    clip_videos = manifest["artifacts"].get("clip_videos", [])

    for i, clip_path in enumerate(clip_videos):
        if not Path(clip_path).exists():
            continue
        topic_info = topics[i] if i < len(topics) else {}
        clip_info = clip_scripts_data[i] if i < len(clip_scripts_data) else {}

        clip_script_text = clip_info.get("script", "")
        clip_desc = clip_script_text[:300]
        if len(clip_script_text) > 300:
            clip_desc += "..."

        entry_id = f"m1_{ts}_clip_{i+1:03d}"
        entry = make_entry(
            id=entry_id, module="M1", content_type="video",
            title=clip_info.get(
                "topic_title",
                topic_info.get("title_en", f"Clip {i+1}"),
            ),
            description=clip_desc,
            topic_tags=topic_info.get("tags", []) + ["clip"],
            importance_score=topic_info.get("importance_score", 7.0),
            video_path=clip_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] Clip registered: {entry_id}")

    # News lineup image
    lineup_path = manifest["artifacts"].get("news_lineup")
    if lineup_path and Path(lineup_path).exists():
        entry_id = f"m1_{ts}_lineup"
        lineup_desc = "Today's news topics lineup image."
        if clip_scripts_data:
            lineup_titles = [c.get("topic_title", "") for c in clip_scripts_data if c.get("topic_title")]
            if lineup_titles:
                lineup_desc += " Topics: " + " / ".join(lineup_titles)

        entry = make_entry(
            id=entry_id, module="M1", content_type="image",
            title=f"Today's News Lineup {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            description=lineup_desc,
            topic_tags=["lineup", "index"] + topic_tags,
            importance_score=10.0,
            screenshot_path=lineup_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] News lineup image registered: {entry_id}")

    _write_manifest(dirs, topics, content_index_ids=content_index_ids)
    logger.info(f"Phase 5 complete: {len(content_index_ids)} item(s) registered.")


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="WeaveCast M1 pipeline")
    parser.add_argument("--topic-index", type=int, default=None,
                        help="Process a specific topic only (backward-compat)")
    parser.add_argument("--phase", type=int, default=0,
                        help="Run a specific phase only (1–5). 0 = all phases.")
    parser.add_argument("--skip-upload", action="store_true",
                        help="Skip YouTube upload")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="Reuse an existing output directory")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    client = genai.Client()

    config = load_config()
    topics = load_topics(config, args.topic_index)
    logger.info(f"Topics loaded: {len(topics)}")
    for t in topics:
        logger.info(f"  - {t.get('title_en', '')}")

    if args.output_dir:
        dirs = OutputDirs.from_existing(args.output_dir)
        logger.info(f"Reusing existing directory: {dirs.root}")
    else:
        dirs = OutputDirs.create(BASE_DIR)
        logger.info(f"Output directory: {dirs.root}")

    if args.phase == 0:
        run_phase1(client, config, topics, dirs)
        run_phase2(client, topics, dirs)
        run_phase3(client, dirs)
        run_phase4(dirs, topics)
        if not args.skip_upload:
            run_phase5(dirs, topics)
        else:
            run_phase5(dirs, topics)  # ContentIndex registration always runs
            logger.info("YouTube upload skipped.")
    elif args.phase == 1:
        run_phase1(client, config, topics, dirs)
    elif args.phase == 2:
        run_phase2(client, topics, dirs)
    elif args.phase == 3:
        run_phase3(client, dirs)
    elif args.phase == 4:
        run_phase4(dirs, topics)
    elif args.phase == 5:
        run_phase5(dirs, topics)
    else:
        logger.error(f"Invalid phase: {args.phase} (valid range: 0–5)")
        sys.exit(1)

    logger.info(f"Done. Output: {dirs.root}")


if __name__ == "__main__":
    main()

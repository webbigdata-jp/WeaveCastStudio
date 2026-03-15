"""
WeaveCast M1 パイプライン — メイン実行スクリプト

使い方:
  python main.py                     # 全トピック一括処理
  python main.py --topic-index 1     # 特定トピックのみ（後方互換）
  python main.py --skip-upload       # YouTube アップロードをスキップ
  python main.py --phase 1           # Phase 1 のみ
  python main.py --phase 2           # Phase 2 のみ（要 Phase 1 の出力）
  python main.py --phase 3           # Phase 3 のみ
  python main.py --phase 4           # Phase 4 のみ
  python main.py --phase 5           # Phase 5 のみ

  --output-dir で既存ディレクトリを再利用:
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

from agents.source_collector import collect_all_topics
from agents.summarizer import generate_structured_summary
from agents.script_writer import generate_briefing_script, generate_clip_scripts
from agents.image_generator import (
    generate_title_slide,
    generate_news_lineup_image,
    generate_content_images,
    generate_clip_image,
)
from agents.narrator import generate_narration
from agents.video_composer import compose_video

# ContentIndex
sys.path.insert(0, str(Path(__file__).parent.parent))
from content_index import ContentIndexManager, make_entry

# ── ロギング ──
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("weavecast.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("weavecast.main")

# ── パス ──
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"
TOPICS_FILE = CONFIG_DIR / "topics.yaml"
ENV_FILE = CONFIG_DIR / ".env"
YT_SECRETS_FILE = CONFIG_DIR / "youtube_client_secrets.json"
YT_TOKEN_FILE = CONFIG_DIR / "youtube_token.json"


@dataclass
class OutputDirs:
    root: Path
    data: Path
    images: Path
    audio: Path
    video: Path
    clips: Path  # ★新規

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
            raise FileNotFoundError(f"出力ディレクトリが見つかりません: {root}")
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


# ── ユーティリティ ──

def load_config() -> dict:
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def load_topics(config: dict, topic_index: int | None = None) -> list[dict]:
    topics = config["topics"]
    if topic_index is not None:
        if topic_index >= len(topics):
            raise IndexError(f"トピックインデックス {topic_index} が範囲外（0-{len(topics)-1}）")
        return [topics[topic_index]]
    return topics


def save_json(data, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"保存完了: {path}")


def load_json(path: Path):
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def save_text(text: str, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"保存完了: {path}")


def load_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


# ── Phase 実行 ──

def run_phase1(client: genai.Client, config: dict, topics: list[dict], dirs: OutputDirs) -> None:
    """Phase 1: 情報収集 + 構造化要約"""
    logger.info("=" * 60)
    logger.info("フェーズ 1: 情報収集 + 構造化要約")
    logger.info("=" * 60)

    # 全トピック一括収集（スクリーンショットはdata/screenshots/に保存）
    screenshot_dir = dirs.data / "screenshots"
    raw_statements = collect_all_topics(client, config, screenshot_dir=screenshot_dir)
    save_json(raw_statements, dirs.data / "raw_statements.json")

    # 構造化要約
    briefing_data = generate_structured_summary(client, topics, raw_statements)
    save_json(briefing_data, dirs.data / "briefing_data.json")

    tc = len([k for k in raw_statements if not k.startswith("__")])
    sc = len(briefing_data.get("briefing_sections", []))
    logger.info(f"フェーズ 1 完了: {tc}トピック収集、{sc}セクション構造化")


def run_phase2(client: genai.Client, topics: list[dict], dirs: OutputDirs) -> None:
    """Phase 2: 原稿生成 + 画像生成"""
    logger.info("=" * 60)
    logger.info("フェーズ 2: 原稿生成 + 画像生成")
    logger.info("=" * 60)

    briefing_data = load_json(dirs.data / "briefing_data.json")
    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")  # e.g. "March 15, 2026"

    # 全体原稿
    full_script = generate_briefing_script(client, briefing_data)
    save_text(full_script, dirs.data / "script.txt")

    # クリップ原稿
    clip_scripts = generate_clip_scripts(client, briefing_data)
    save_json(clip_scripts, dirs.data / "clip_scripts.json")

    # 画像生成
    title_slide = generate_title_slide(client, topics, dirs.images, date_str)
    lineup_image = generate_news_lineup_image(client, topics, dirs.images, date_str)
    content_slides = generate_content_images(client, full_script, dirs.images)

    # クリップ用画像
    clip_image_paths = []
    for i, clip in enumerate(clip_scripts):
        clip_dir = dirs.clips / f"clip_{i+1:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)
        img_path = generate_clip_image(client, clip, clip_dir / "image.png")
        clip_image_paths.append(str(img_path))
        # クリップ原稿も保存
        save_text(clip["script"], clip_dir / "script.txt")

    image_manifest = {
        "title_slide": str(title_slide),
        "news_lineup": str(lineup_image),
        "content_slides": [str(p) for p in content_slides],
        "clip_images": clip_image_paths,
    }
    save_json(image_manifest, dirs.data / "image_manifest.json")

    logger.info(
        f"フェーズ 2 完了: タイトル1枚 + 一覧1枚 + コンテンツ{len(content_slides)}枚"
        f" + クリップ{len(clip_image_paths)}枚"
    )


def run_phase3(client: genai.Client, dirs: OutputDirs) -> None:
    """Phase 3: TTS音声生成（全体 + クリップ）"""
    logger.info("=" * 60)
    logger.info("フェーズ 3: ナレーション音声生成")
    logger.info("=" * 60)

    # 全体ナレーション
    full_script = load_text(dirs.data / "script.txt")
    audio_paths = generate_narration(client, full_script, dirs.audio)
    audio_manifest = {"segments": [str(p) for p in audio_paths]}

    # クリップナレーション
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
        f"フェーズ 3 完了: 全体{len(audio_paths)}セグメント"
        f" + クリップ{len(clip_audio_paths)}件"
    )


def run_phase4(dirs: OutputDirs, topics: list[dict]) -> None:
    """Phase 4: 動画合成（全体 + クリップ）"""
    logger.info("=" * 60)
    logger.info("フェーズ 4: 動画合成")
    logger.info("=" * 60)

    image_manifest = load_json(dirs.data / "image_manifest.json")
    audio_manifest = load_json(dirs.data / "audio_manifest.json")

    # 全体ブリーフィング動画
    title_slide = Path(image_manifest["title_slide"])
    content_slides = [Path(p) for p in image_manifest["content_slides"]]
    audio_segments = [Path(p) for p in audio_manifest["segments"]]

    output_video = compose_video(
        title_slide=title_slide,
        content_slides=content_slides,
        audio_segments=audio_segments,
        output_dir=dirs.root,
    )
    logger.info(f"全体ブリーフィング動画: {output_video}")

    # クリップ動画
    clip_scripts = load_json(dirs.data / "clip_scripts.json")
    clip_images = image_manifest.get("clip_images", [])
    clip_audios = audio_manifest.get("clip_audios", [])
    clip_video_paths = []

    clip_video_dir = dirs.video / "clips"
    clip_video_dir.mkdir(parents=True, exist_ok=True)

    for i in range(len(clip_scripts)):
        if i >= len(clip_images) or i >= len(clip_audios):
            logger.warning(f"クリップ{i+1}: 画像または音声が不足。スキップ。")
            continue

        clip_dir = dirs.clips / f"clip_{i+1:03d}"
        clip_dir.mkdir(parents=True, exist_ok=True)

        topic_title = clip_scripts[i].get("topic_title", f"clip_{i+1}")
        safe_title = "".join(c if c.isalnum() or c in "._-" else "_" for c in topic_title)
        clip_video_path = clip_video_dir / f"clip_{i+1:03d}_{safe_title}.mp4"

        try:
            clip_img = Path(clip_images[i])
            clip_auds = [Path(p) for p in clip_audios[i]]

            # 結合音声はclip_dir内に保存、動画は直接video/clips/に出力
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
                logger.info(f"クリップ動画: {clip_video_path}")
            else:
                logger.warning(f"クリップ{i+1}: 動画生成されず")
        except Exception as e:
            logger.error(f"クリップ{i+1} 動画合成失敗: {e}")

    # マニフェスト更新
    _write_manifest(dirs, topics, video_path=output_video, clip_video_paths=clip_video_paths)
    logger.info(f"フェーズ 4 完了: 全体1本 + クリップ{len(clip_video_paths)}本")


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

    topic_tags = []
    for t in topics:
        topic_tags.extend(t.get("tags", []))
    topic_tags = list(set(topic_tags))

    manifest = {
        **existing,
        "module": "M1",
        "brand": "WeaveCast",
        "generated_at": existing.get("generated_at", datetime.now(timezone.utc).isoformat()),
        "topics": [
            {"title": t.get("title", ""), "title_en": t.get("title_en", ""),
             "tags": t.get("tags", []), "importance_score": t.get("importance_score", 7.0)}
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

    # news_lineup パスを image_manifest から取得
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
    """Phase 5: content_index登録（+ オプションでYouTubeアップロード）"""
    logger.info("=" * 60)
    logger.info("フェーズ 5: ContentIndex登録")
    logger.info("=" * 60)

    manifest = load_json(dirs.root / "manifest.json")
    ts = dirs.root.name  # briefing_YYYYMMDD_HHMMSS
    topic_tags = manifest.get("topic_tags", [])
    content_index_ids = []

    mgr = ContentIndexManager()

    # 全体ブリーフィング動画
    video_path = manifest["artifacts"].get("video")
    if video_path and Path(video_path).exists():
        entry_id = f"m1_{ts}"
        entry = make_entry(
            id=entry_id, module="M1", content_type="video",
            title=f"国際情勢ブリーフィング {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            topic_tags=topic_tags + ["briefing", "full"],
            importance_score=max((t.get("importance_score", 7) for t in topics), default=7),
            video_path=video_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] 全体ブリーフィング登録: {entry_id}")

    # クリップ動画
    clip_videos = manifest["artifacts"].get("clip_videos", [])
    clip_scripts_data = []
    clip_scripts_path = dirs.data / "clip_scripts.json"
    if clip_scripts_path.exists():
        clip_scripts_data = load_json(clip_scripts_path)

    for i, clip_path in enumerate(clip_videos):
        if not Path(clip_path).exists():
            continue
        topic_info = topics[i] if i < len(topics) else {}
        clip_info = clip_scripts_data[i] if i < len(clip_scripts_data) else {}

        entry_id = f"m1_{ts}_clip_{i+1:03d}"
        entry = make_entry(
            id=entry_id, module="M1", content_type="video",
            title=clip_info.get("topic_title", topic_info.get("title", f"クリップ{i+1}")),
            topic_tags=topic_info.get("tags", []) + ["clip"],
            importance_score=topic_info.get("importance_score", 7.0),
            video_path=clip_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] クリップ登録: {entry_id}")

    # ニュース一覧画像
    lineup_path = manifest["artifacts"].get("news_lineup")
    if lineup_path and Path(lineup_path).exists():
        entry_id = f"m1_{ts}_lineup"
        entry = make_entry(
            id=entry_id, module="M1", content_type="image",
            title=f"本日のニュース一覧 {datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            topic_tags=["lineup", "index"] + topic_tags,
            importance_score=10.0,
            screenshot_path=lineup_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        content_index_ids.append(entry_id)
        logger.info(f"[ContentIndex] ニュース一覧画像登録: {entry_id}")

    # マニフェスト更新
    _write_manifest(dirs, topics, content_index_ids=content_index_ids)
    logger.info(f"フェーズ 5 完了: {len(content_index_ids)}件登録")


# ── エントリポイント ──

def main() -> None:
    parser = argparse.ArgumentParser(description="WeaveCast M1 パイプライン")
    parser.add_argument("--topic-index", type=int, default=None,
                        help="特定トピックのみ処理（後方互換）")
    parser.add_argument("--phase", type=int, default=0,
                        help="特定フェーズのみ実行（1-5）。0=全フェーズ")
    parser.add_argument("--skip-upload", action="store_true",
                        help="YouTubeアップロードをスキップ")
    parser.add_argument("--output-dir", type=str, default=None,
                        help="既存の出力ディレクトリを再利用")
    args = parser.parse_args()

    load_dotenv(ENV_FILE)
    client = genai.Client()

    config = load_config()
    topics = load_topics(config, args.topic_index)
    logger.info(f"トピック数: {len(topics)}")
    for t in topics:
        logger.info(f"  - {t['title']}")

    if args.output_dir:
        dirs = OutputDirs.from_existing(args.output_dir)
        logger.info(f"既存ディレクトリ再利用: {dirs.root}")
    else:
        dirs = OutputDirs.create(BASE_DIR)
        logger.info(f"出力ディレクトリ: {dirs.root}")

    if args.phase == 0:
        run_phase1(client, config, topics, dirs)
        run_phase2(client, topics, dirs)
        run_phase3(client, dirs)
        run_phase4(dirs, topics)
        if not args.skip_upload:
            run_phase5(dirs, topics)
        else:
            run_phase5(dirs, topics)  # content_index登録は常に実行
            logger.info("YouTubeアップロードはスキップ")
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
        logger.error(f"無効なフェーズ: {args.phase}（0-5）")
        sys.exit(1)

    logger.info(f"完了。出力先: {dirs.root}")


if __name__ == "__main__":
    main()

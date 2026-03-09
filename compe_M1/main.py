"""
StoryWire M1 パイプライン — メイン実行スクリプト

使い方:
  python main.py                     # config/topics.yaml の最初のトピックを処理
  python main.py --topic-index 1     # 2番目のトピックを処理
  python main.py --skip-upload       # YouTube アップロードをスキップ
  python main.py --phase 1           # Phase 1 (STEP 2-3) のみ実行
  python main.py --phase 2           # Phase 2 (STEP 4-5) のみ（要 Phase 1 の出力）
  python main.py --phase 3           # Phase 3 (STEP 6) のみ
  python main.py --phase 4           # Phase 4 (STEP 7) のみ
  python main.py --phase 5           # Phase 5 (STEP 8) のみ
"""

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml
from dotenv import load_dotenv
from google import genai

# エージェントモジュール
from agents.source_collector import collect_government_statements
from agents.summarizer import generate_structured_summary
from agents.script_writer import generate_briefing_script
from agents.image_generator import generate_title_slide, generate_content_images
from agents.narrator import generate_narration
from agents.video_composer import compose_video
from uploader.youtube_uploader import upload_to_youtube

# ──────────────────────────────────────────
# ロギング設定
# ──────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("storywire.log", encoding="utf-8"),
    ],
)
logger = logging.getLogger("storywire.main")

# ──────────────────────────────────────────
# パス定義
# ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"
OUTPUT_DIR = BASE_DIR / "output"
DATA_DIR = OUTPUT_DIR / "data"
IMAGE_DIR = OUTPUT_DIR / "images"
AUDIO_DIR = OUTPUT_DIR / "audio"
VIDEO_DIR = OUTPUT_DIR / "video"

TOPICS_FILE = CONFIG_DIR / "topics.yaml"
ENV_FILE = CONFIG_DIR / ".env"
YT_SECRETS_FILE = CONFIG_DIR / "youtube_client_secrets.json"
YT_TOKEN_FILE = CONFIG_DIR / "youtube_token.json"


def ensure_directories() -> None:
    for d in [DATA_DIR, IMAGE_DIR, AUDIO_DIR, VIDEO_DIR]:
        d.mkdir(parents=True, exist_ok=True)


def load_topic(index: int) -> dict:
    with open(TOPICS_FILE, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f)
    topics = config["topics"]
    if index >= len(topics):
        raise IndexError(f"Topic index {index} out of range (0-{len(topics)-1})")
    topic = topics[index]
    topic["timestamp"] = datetime.now(timezone.utc).isoformat()
    return topic


def save_json(data: dict, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    logger.info(f"Saved: {path}")


def load_json(path: Path) -> dict:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_text(path: Path) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def save_text(text: str, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    logger.info(f"Saved: {path}")


# ──────────────────────────────────────────
# Phase 実行関数
# ──────────────────────────────────────────

def run_phase1(client: genai.Client, topic: dict) -> None:
    """Phase 1: STEP 2-3 — 情報収集 + 構造化要約"""
    logger.info("=" * 60)
    logger.info("PHASE 1: Source Collection + Structured Summary")
    logger.info("=" * 60)

    # STEP 2: 情報収集
    raw_statements = collect_government_statements(client, topic)
    save_json(raw_statements, DATA_DIR / "raw_statements.json")

    # STEP 3: 構造化要約
    briefing_data = generate_structured_summary(client, topic, raw_statements)
    save_json(briefing_data, DATA_DIR / "briefing_data.json")

    logger.info("Phase 1 complete.")
    logger.info(f"  Countries collected: {len(raw_statements)}")
    logger.info(f"  Briefing sections: {len(briefing_data.get('briefing_sections', []))}")


def run_phase2(client: genai.Client, topic: dict) -> None:
    """Phase 2: STEP 4-5 — 原稿生成 + 画像生成"""
    logger.info("=" * 60)
    logger.info("PHASE 2: Script Writing + Image Generation")
    logger.info("=" * 60)

    briefing_data = load_json(DATA_DIR / "briefing_data.json")

    # STEP 4: 原稿生成
    script_text = generate_briefing_script(client, briefing_data)
    save_text(script_text, DATA_DIR / "script.txt")

    # STEP 5: 画像生成
    title_slide = generate_title_slide(client, topic, IMAGE_DIR)
    content_slides = generate_content_images(client, script_text, IMAGE_DIR)

    # 画像パスリストを保存（後続Phaseで参照）
    image_manifest = {
        "title_slide": str(title_slide),
        "content_slides": [str(p) for p in content_slides],
    }
    save_json(image_manifest, DATA_DIR / "image_manifest.json")

    logger.info("Phase 2 complete.")
    logger.info(f"  Images generated: 1 title + {len(content_slides)} content slides")


def run_phase3(client: genai.Client) -> None:
    """Phase 3: STEP 6 — TTS音声生成"""
    logger.info("=" * 60)
    logger.info("PHASE 3: Narration (TTS)")
    logger.info("=" * 60)

    script_text = load_text(DATA_DIR / "script.txt")
    audio_paths = generate_narration(client, script_text, AUDIO_DIR)

    audio_manifest = {"segments": [str(p) for p in audio_paths]}
    save_json(audio_manifest, DATA_DIR / "audio_manifest.json")

    logger.info(f"Phase 3 complete. {len(audio_paths)} audio segments saved.")


def run_phase4() -> None:
    """Phase 4: STEP 7 — 動画合成"""
    logger.info("=" * 60)
    logger.info("PHASE 4: Video Composition (ffmpeg)")
    logger.info("=" * 60)

    image_manifest = load_json(DATA_DIR / "image_manifest.json")
    audio_manifest = load_json(DATA_DIR / "audio_manifest.json")

    title_slide = Path(image_manifest["title_slide"])
    content_slides = [Path(p) for p in image_manifest["content_slides"]]
    audio_segments = [Path(p) for p in audio_manifest["segments"]]

    output_video = compose_video(
        title_slide=title_slide,
        content_slides=content_slides,
        audio_segments=audio_segments,
        output_dir=OUTPUT_DIR,
    )

    logger.info(f"Phase 4 complete. Video: {output_video}")


def run_phase5() -> None:
    """Phase 5: STEP 8 — YouTubeアップロード"""
    logger.info("=" * 60)
    logger.info("PHASE 5: YouTube Upload")
    logger.info("=" * 60)

    if not YT_SECRETS_FILE.exists():
        logger.error(
            f"YouTube client secrets not found: {YT_SECRETS_FILE}\n"
            "Please download OAuth2 client secrets from Google Cloud Console "
            "and save as config/youtube_client_secrets.json"
        )
        sys.exit(1)

    briefing_data = load_json(DATA_DIR / "briefing_data.json")
    video_path = VIDEO_DIR / "briefing.mp4"

    if not video_path.exists():
        logger.error(f"Video not found: {video_path}. Run Phase 4 first.")
        sys.exit(1)

    video_id = upload_to_youtube(
        video_path=video_path,
        briefing_data=briefing_data,
        client_secrets_path=YT_SECRETS_FILE,
        token_path=YT_TOKEN_FILE,
        privacy_status="unlisted",  # 本番では "public" に変更
    )
    logger.info(f"Phase 5 complete. YouTube URL: https://youtube.com/watch?v={video_id}")


# ──────────────────────────────────────────
# エントリポイント
# ──────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="StoryWire M1 Pipeline")
    parser.add_argument(
        "--topic-index", type=int, default=0,
        help="Index of topic in topics.yaml (default: 0)"
    )
    parser.add_argument(
        "--phase", type=int, default=0,
        help="Run only a specific phase (1-5). Default 0 = run all phases."
    )
    parser.add_argument(
        "--skip-upload", action="store_true",
        help="Skip YouTube upload (Phase 5)"
    )
    args = parser.parse_args()

    # 環境変数ロード
    load_dotenv(ENV_FILE)
    ensure_directories()

    # Gemini クライアント初期化
    client = genai.Client()  # GOOGLE_API_KEY 環境変数から自動取得

    # トピックロード
    topic = load_topic(args.topic_index)
    logger.info(f"Topic: {topic['title']}")

    # Phase 実行
    if args.phase == 0:
        # 全フェーズ実行
        run_phase1(client, topic)
        run_phase2(client, topic)
        run_phase3(client)
        run_phase4()
        if not args.skip_upload:
            run_phase5()
        else:
            logger.info("YouTube upload skipped (--skip-upload)")
    elif args.phase == 1:
        run_phase1(client, topic)
    elif args.phase == 2:
        run_phase2(client, topic)
    elif args.phase == 3:
        run_phase3(client)
    elif args.phase == 4:
        run_phase4()
    elif args.phase == 5:
        run_phase5()
    else:
        logger.error(f"Invalid phase: {args.phase}. Must be 0-5.")
        sys.exit(1)

    logger.info("Done.")


if __name__ == "__main__":
    main()

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

  --output-dir を指定すると既存の出力ディレクトリを再利用する（Phase 2以降のみ実行時）:
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

# エージェントモジュール
from agents.source_collector import collect_government_statements
from agents.summarizer import generate_structured_summary
from agents.script_writer import generate_briefing_script
from agents.image_generator import generate_title_slide, generate_content_images
from agents.narrator import generate_narration
from agents.video_composer import compose_video
from uploader.youtube_uploader import upload_to_youtube

# ContentIndex（GeminiLiveAgent/ 共有モジュール）
sys.path.insert(0, str(Path(__file__).parent.parent))
from content_index import ContentIndexManager, make_entry

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
# 固定パス定義（実行をまたいで変わらないもののみ）
# ──────────────────────────────────────────
BASE_DIR = Path(__file__).parent
CONFIG_DIR = BASE_DIR / "config"

TOPICS_FILE    = CONFIG_DIR / "topics.yaml"
ENV_FILE       = CONFIG_DIR / ".env"
YT_SECRETS_FILE = CONFIG_DIR / "youtube_client_secrets.json"
YT_TOKEN_FILE  = CONFIG_DIR / "youtube_token.json"


# ──────────────────────────────────────────
# 実行単位の出力ディレクトリ
# ──────────────────────────────────────────

@dataclass
class OutputDirs:
    """1回の実行で使う出力ディレクトリ群をまとめるデータクラス"""
    root:   Path   # output/briefing_<timestamp>/
    data:   Path   # root/data/
    images: Path   # root/images/
    audio:  Path   # root/audio/
    video:  Path   # root/video/

    @classmethod
    def create(cls, base: Path) -> "OutputDirs":
        """タイムスタンプ付きディレクトリを新規作成して返す"""
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        root = base / "output" / f"briefing_{ts}"
        return cls._make(root)

    @classmethod
    def from_existing(cls, path: str | Path) -> "OutputDirs":
        """既存のディレクトリを再利用する（--phase 2以降のみ実行時）"""
        root = Path(path) if not Path(path).is_absolute() else Path(path)
        if not root.is_absolute():
            root = BASE_DIR / root
        if not root.exists():
            raise FileNotFoundError(f"Output dir not found: {root}")
        return cls._make(root)

    @classmethod
    def _make(cls, root: Path) -> "OutputDirs":
        dirs = cls(
            root=root,
            data=root / "data",
            images=root / "images",
            audio=root / "audio",
            video=root / "video",
        )
        for d in [dirs.data, dirs.images, dirs.audio, dirs.video]:
            d.mkdir(parents=True, exist_ok=True)
        return dirs


# ──────────────────────────────────────────
# ユーティリティ
# ──────────────────────────────────────────

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

def run_phase1(client: genai.Client, topic: dict, dirs: OutputDirs) -> None:
    """Phase 1: STEP 2-3 — 情報収集 + 構造化要約"""
    logger.info("=" * 60)
    logger.info("PHASE 1: Source Collection + Structured Summary")
    logger.info("=" * 60)

    # STEP 2: 情報収集
    raw_statements = collect_government_statements(client, topic)
    save_json(raw_statements, dirs.data / "raw_statements.json")

    # STEP 3: 構造化要約
    briefing_data = generate_structured_summary(client, topic, raw_statements)
    save_json(briefing_data, dirs.data / "briefing_data.json")

    logger.info("Phase 1 complete.")
    logger.info(f"  Countries collected: {len(raw_statements)}")
    logger.info(f"  Briefing sections: {len(briefing_data.get('briefing_sections', []))}")


def run_phase2(client: genai.Client, topic: dict, dirs: OutputDirs) -> None:
    """Phase 2: STEP 4-5 — 原稿生成 + 画像生成"""
    logger.info("=" * 60)
    logger.info("PHASE 2: Script Writing + Image Generation")
    logger.info("=" * 60)

    briefing_data = load_json(dirs.data / "briefing_data.json")

    # STEP 4: 原稿生成
    script_text = generate_briefing_script(client, briefing_data)
    save_text(script_text, dirs.data / "script.txt")

    # STEP 5: 画像生成
    title_slide = generate_title_slide(client, topic, dirs.images)
    content_slides = generate_content_images(client, script_text, dirs.images)

    image_manifest = {
        "title_slide": str(title_slide),
        "content_slides": [str(p) for p in content_slides],
    }
    save_json(image_manifest, dirs.data / "image_manifest.json")

    logger.info("Phase 2 complete.")
    logger.info(f"  Images generated: 1 title + {len(content_slides)} content slides")


def run_phase3(client: genai.Client, dirs: OutputDirs) -> None:
    """Phase 3: STEP 6 — TTS音声生成"""
    logger.info("=" * 60)
    logger.info("PHASE 3: Narration (TTS)")
    logger.info("=" * 60)

    script_text = load_text(dirs.data / "script.txt")
    audio_paths = generate_narration(client, script_text, dirs.audio)

    audio_manifest = {"segments": [str(p) for p in audio_paths]}
    save_json(audio_manifest, dirs.data / "audio_manifest.json")

    logger.info(f"Phase 3 complete. {len(audio_paths)} audio segments saved.")


def run_phase4(dirs: OutputDirs, topic: dict) -> None:
    """Phase 4: STEP 7 — 動画合成 + manifest.json 書き出し"""
    logger.info("=" * 60)
    logger.info("PHASE 4: Video Composition (ffmpeg)")
    logger.info("=" * 60)

    image_manifest = load_json(dirs.data / "image_manifest.json")
    audio_manifest = load_json(dirs.data / "audio_manifest.json")

    title_slide    = Path(image_manifest["title_slide"])
    content_slides = [Path(p) for p in image_manifest["content_slides"]]
    audio_segments = [Path(p) for p in audio_manifest["segments"]]

    output_video = compose_video(
        title_slide=title_slide,
        content_slides=content_slides,
        audio_segments=audio_segments,
        output_dir=dirs.root,
    )
    logger.info(f"Phase 4 complete. Video: {output_video}")

    # manifest.json を出力ルートに書き出す
    _write_manifest(dirs, topic, video_path=output_video)


def _write_manifest(
    dirs: OutputDirs,
    topic: dict,
    video_path: Path | None = None,
    youtube_url: str | None = None,
    content_index_id: str | None = None,
) -> Path:
    """
    briefing_<timestamp>/manifest.json を書き出す（上書き可）。

    M4 から「どのトピックか」「成果物はどこか」を検索できるよう、
    トピック情報・生成日時・全成果物パスを一か所に記録する。

    Returns:
        書き出した manifest.json のパス
    """
    manifest_path = dirs.root / "manifest.json"

    # 既存があれば読み込んで差分更新（Phase 4→5 の2回書き込みに対応）
    existing: dict = {}
    if manifest_path.exists():
        try:
            existing = load_json(manifest_path)
        except Exception:
            pass

    manifest = {
        **existing,
        "module": "M1",
        "generated_at": existing.get(
            "generated_at",
            datetime.now(timezone.utc).isoformat()
        ),
        "topic": {
            "title": topic.get("title", ""),
            "tags": topic.get("tags", []),
            "importance_score": topic.get("importance_score", 7.0),
            "timestamp": topic.get("timestamp", ""),
        },
        "artifacts": {
            "video": str(video_path) if video_path else existing.get("artifacts", {}).get("video"),
            "script": str(dirs.data / "script.txt"),
            "briefing_data": str(dirs.data / "briefing_data.json"),
            "image_manifest": str(dirs.data / "image_manifest.json"),
            "audio_manifest": str(dirs.data / "audio_manifest.json"),
            "title_slide": existing.get("artifacts", {}).get("title_slide"),
        },
        "output_dir": str(dirs.root),
    }

    # Phase 5 完了後にのみ追記するフィールド
    if youtube_url:
        manifest["youtube_url"] = youtube_url
    if content_index_id:
        manifest["content_index_id"] = content_index_id

    # title_slide パスは image_manifest.json から取得（Phase 2以降に存在）
    img_manifest_path = dirs.data / "image_manifest.json"
    if img_manifest_path.exists() and not manifest["artifacts"]["title_slide"]:
        try:
            img_data = load_json(img_manifest_path)
            manifest["artifacts"]["title_slide"] = img_data.get("title_slide")
        except Exception:
            pass

    save_json(manifest, manifest_path)
    logger.info(f"[Manifest] Written: {manifest_path}")
    return manifest_path


def run_phase5(dirs: OutputDirs, topic: dict) -> None:
    """Phase 5: STEP 8 — YouTubeアップロード + ContentIndex登録"""
    logger.info("=" * 60)
    logger.info("PHASE 5: YouTube Upload + ContentIndex")
    logger.info("=" * 60)

    if not YT_SECRETS_FILE.exists():
        logger.error(
            f"YouTube client secrets not found: {YT_SECRETS_FILE}\n"
            "Please download OAuth2 client secrets from Google Cloud Console "
            "and save as config/youtube_client_secrets.json"
        )
        sys.exit(1)

    briefing_data = load_json(dirs.data / "briefing_data.json")
    video_path = dirs.video / "briefing.mp4"

    if not video_path.exists():
        logger.error(f"Video not found: {video_path}. Run Phase 4 first.")
        sys.exit(1)

    # YouTube アップロード
    video_id = upload_to_youtube(
        video_path=video_path,
        briefing_data=briefing_data,
        client_secrets_path=YT_SECRETS_FILE,
        token_path=YT_TOKEN_FILE,
        privacy_status="unlisted",
    )
    youtube_url = f"https://youtube.com/watch?v={video_id}"
    logger.info(f"Phase 5 complete. YouTube URL: {youtube_url}")

    # ContentIndex 登録 → content_index_id を受け取って manifest 更新
    content_index_id = _register_content_index(dirs, topic, briefing_data)
    _write_manifest(
        dirs, topic,
        video_path=dirs.video / "briefing.mp4",
        youtube_url=youtube_url,
        content_index_id=content_index_id,
    )


def _register_content_index(
    dirs: OutputDirs, topic: dict, briefing_data: dict
) -> str | None:
    """
    生成した動画を ContentIndex に登録する。

    Returns:
        登録した entry の id（失敗時は None）
    """
    video_path = dirs.video / "briefing.mp4"
    if not video_path.exists():
        logger.warning("[ContentIndex] Video not found, skipping registration.")
        return None

    # topics.yaml の tags フィールド + "government", "official" を付与
    tags = topic.get("tags", []) + ["government", "official"]

    try:
        mgr = ContentIndexManager()
        entry = make_entry(
            id=f"m1_{dirs.root.name}",
            module="M1",
            content_type="video",
            title=topic["title"],
            topic_tags=tags,
            importance_score=topic.get("importance_score", 7.0),
            video_path=video_path,
            manifest_path=dirs.root / "manifest.json",
        )
        mgr.add_entry(entry)
        logger.info(f"[ContentIndex] Registered: {entry['id']} ({entry.get('duration_seconds')}s)")
        return entry["id"]
    except Exception as e:
        # ContentIndex 登録失敗はパイプライン全体を止めない
        logger.warning(f"[ContentIndex] Registration failed (non-fatal): {e}")
        return None


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
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="既存の出力ディレクトリを再利用する（--phase 2以降のみ実行時に指定）"
    )
    args = parser.parse_args()

    # 環境変数ロード
    load_dotenv(ENV_FILE)

    # Gemini クライアント初期化
    client = genai.Client()

    # トピックロード
    topic = load_topic(args.topic_index)
    logger.info(f"Topic: {topic['title']}")

    # 出力ディレクトリ決定
    if args.output_dir:
        # 既存ディレクトリ再利用（Phase 2以降のみ実行時）
        try:
            dirs = OutputDirs.from_existing(args.output_dir)
        except FileNotFoundError as e:
            logger.error(str(e))
            sys.exit(1)
        logger.info(f"Reusing output dir: {dirs.root}")
    else:
        # 新規作成（Phase 1 or 全フェーズ実行時）
        dirs = OutputDirs.create(BASE_DIR)
        logger.info(f"Output dir: {dirs.root}")

    # Phase 実行
    if args.phase == 0:
        run_phase1(client, topic, dirs)
        run_phase2(client, topic, dirs)
        run_phase3(client, dirs)
        run_phase4(dirs, topic)
        if not args.skip_upload:
            run_phase5(dirs, topic)
        else:
            logger.info("YouTube upload skipped (--skip-upload)")
            content_index_id = _register_content_index(
                dirs, topic, load_json(dirs.data / "briefing_data.json")
            )
            _write_manifest(
                dirs, topic,
                video_path=dirs.video / "briefing.mp4",
                content_index_id=content_index_id,
            )
    elif args.phase == 1:
        run_phase1(client, topic, dirs)
    elif args.phase == 2:
        run_phase2(client, topic, dirs)
    elif args.phase == 3:
        run_phase3(client, dirs)
    elif args.phase == 4:
        run_phase4(dirs, topic)
    elif args.phase == 5:
        run_phase5(dirs, topic)
    else:
        logger.error(f"Invalid phase: {args.phase}. Must be 0-5.")
        sys.exit(1)

    logger.info(f"Done. Output: {dirs.root}")


if __name__ == "__main__":
    main()


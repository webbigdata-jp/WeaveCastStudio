"""
test_phase3.py

Phase 3 テスト: BriefingComposer → 動画生成フルパイプライン

テスト内容:
  1. shared/ シンボリックリンク確認
  2. dry_run=True で原稿生成まで確認（API課金最小）
  3. 確認後に動画生成フル実行（画像 + TTS + ffmpeg）

使い方:
  # 原稿生成のみ（動画生成スキップ）
  uv run test_phase3.py --dry-run

  # フル実行（画像・TTS・動画生成あり）
  uv run test_phase3.py

  # ショートクリップ生成（dry-run）
  uv run test_phase3.py --short-clips --dry-run

  # ショートクリップ生成（フル実行）
  uv run test_phase3.py --short-clips
"""

import argparse
import logging
import sys
from pathlib import Path

# shared/ を sys.path に追加（M1 agents の import のため）
sys.path.insert(0, str(Path(__file__).parent))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_phase3")


def check_shared_symlink() -> bool:
    """shared/ シンボリックリンクの存在と有効性を確認する"""
    shared = Path("shared")
    if not shared.exists():
        logger.error(
            "❌ shared/ が見つかりません。\n"
            "以下のコマンドでシンボリックリンクを作成してください:\n"
            "  cd ~/disk_new/devpost/GeminiLiveAgent/compe_M3\n"
            "  ln -s ../compe_M1/agents shared"
        )
        return False

    if not shared.is_symlink():
        logger.error("❌ shared/ はシンボリックリンクではありません")
        return False

    # 必要なモジュールが存在するか確認
    required = [
        "shared/image_generator.py",
        "shared/narrator.py",
        "shared/video_composer.py",
        "shared/script_writer.py",
    ]
    missing = [f for f in required if not Path(f).exists()]
    if missing:
        logger.error(f"❌ shared/ 内に必要なファイルがありません: {missing}")
        return False

    logger.info(f"✅ shared/ -> {shared.resolve()}")
    return True


def check_analyzed_articles() -> int:
    """DB に分析済み記事があるか確認する"""
    from store.article_store import ArticleStore
    store = ArticleStore()
    stats = store.get_stats()
    analyzed = stats.get("analyzed", 0)
    logger.info(f"  DB stats: total={stats['total']}, analyzed={analyzed}")

    if analyzed == 0:
        logger.warning(
            "⚠️  分析済み記事がありません。\n"
            "先に以下を実行してください:\n"
            "  uv run test_phase1.py\n"
            "  uv run test_phase2.py"
        )
    return analyzed


def main():
    parser = argparse.ArgumentParser(description="Phase 3 Test: BriefingComposer")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="原稿生成まで実行し、画像・TTS・動画生成をスキップする"
    )
    parser.add_argument(
        "--hours", type=int, default=720,
        help="直近何時間の記事を対象にするか（デフォルト: 720 = 30日）"
    )
    parser.add_argument(
        "--short-clips", action="store_true",
        help="compose() の代わりに compose_short_clips() を実行する"
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="生成するクリップ数の上限（例: --limit 1 で1件のみ生成）"
    )
    args = parser.parse_args()

    logger.info("=== Phase 3 Test: Briefing Composer ===")
    logger.info(f"  dry_run     : {args.dry_run}")
    logger.info(f"  short_clips : {args.short_clips}")
    logger.info(f"  hours       : {args.hours}")
    logger.info(f"  limit       : {args.limit}")

    # ── 1. shared/ 確認 ──
    logger.info("\n[CHECK 1] shared/ symlink")
    if not check_shared_symlink():
        sys.exit(1)

    # ── 2. 分析済み記事確認 ──
    logger.info("\n[CHECK 2] Analyzed articles in DB")
    analyzed_count = check_analyzed_articles()
    if analyzed_count == 0:
        sys.exit(1)

    # ── 3. BriefingComposer 初期化 ──
    logger.info("\n[STEP 1] Initialize BriefingComposer")
    try:
        from composer.briefing_composer import BriefingComposer
        composer = BriefingComposer()
        logger.info("✅ BriefingComposer initialized")
    except Exception as e:
        logger.error(f"❌ BriefingComposer init failed: {e}")
        sys.exit(1)

    # ── 4. compose / compose_short_clips 実行 ──
    if args.short_clips:
        mode = "short_clips dry_run" if args.dry_run else "short_clips full pipeline"
        logger.info(f"\n[STEP 2] compose_short_clips() — {mode}")

        # --limit が指定された場合、article_ids で件数を絞る
        article_ids = None
        if args.limit is not None:
            from store.article_store import ArticleStore
            store = ArticleStore()
            top_articles = store.get_top_articles(limit=args.limit, hours=args.hours)
            article_ids = [a["id"] for a in top_articles]
            logger.info(f"  --limit {args.limit} 適用: article_ids={article_ids}")

        try:
            result = composer.compose_short_clips(
                hours=args.hours,
                article_ids=article_ids,
                dry_run=args.dry_run,
            )
        except RuntimeError as e:
            logger.error(f"❌ compose_short_clips() failed: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}", exc_info=True)
            sys.exit(1)

        # ── ショートクリップ結果確認 ──
        logger.info("\n[RESULT]")
        logger.info(f"  output_dir    : {result['output_dir']}")
        logger.info(f"  total         : {result['total']}")
        logger.info(f"  succeeded     : {result['succeeded']}")
        for i, clip in enumerate(result.get("clips", []), start=1):
            video_info = clip.get("video_path") or "(skipped)"
            logger.info(
                f"  clip {i:03d} | "
                f"article_id={clip['article_id']} | "
                f"title={clip.get('title','')[:40]} | "
                f"video={video_info}"
            )

        if args.dry_run:
            logger.info("\n✅ Phase 3 short_clips dry_run complete!")
        else:
            if result["succeeded"] == 0:
                logger.error("❌ ショートクリップが1件も生成されませんでした")
                sys.exit(1)
            logger.info(
                f"\n✅ Phase 3 short_clips complete! "
                f"{result['succeeded']}/{result['total']} clips generated."
            )

    else:
        mode = "dry_run (script only)" if args.dry_run else "full pipeline"
        logger.info(f"\n[STEP 2] compose() — {mode}")

        try:
            result = composer.compose(
                hours=args.hours,
                dry_run=args.dry_run,
            )
        except RuntimeError as e:
            logger.error(f"❌ compose() failed: {e}")
            sys.exit(1)
        except Exception as e:
            logger.error(f"❌ Unexpected error: {e}", exc_info=True)
            sys.exit(1)

        # ── 結果確認 ──
        logger.info("\n[RESULT]")
        logger.info(f"  output_dir        : {result['output_dir']}")
        logger.info(f"  article_count     : {result['article_count']}")
        logger.info(f"  briefing_plan     : {result['briefing_plan_path']}")
        logger.info(f"  script_path       : {result['script_path']}")
        logger.info(f"  video_path        : {result['video_path'] or '(skipped)'}")

        # 原稿の先頭300文字をプレビュー
        script_path = Path(result["script_path"])
        if script_path.exists():
            preview = script_path.read_text(encoding="utf-8")[:300]
            logger.info(f"\n[SCRIPT PREVIEW]\n{preview}...")

        if args.dry_run:
            logger.info(
                "\n✅ Phase 3 dry_run complete!\n"
                "原稿を確認後、フル実行するには:\n"
                "  uv run test_phase3.py"
            )
        else:
            video = Path(result["video_path"]) if result["video_path"] else None
            if video and video.exists():
                size_mb = video.stat().st_size / 1024 / 1024
                logger.info(f"\n✅ Phase 3 complete! Video: {video} ({size_mb:.1f} MB)")
            else:
                logger.error("❌ 動画ファイルが生成されませんでした")
                sys.exit(1)


if __name__ == "__main__":
    main()
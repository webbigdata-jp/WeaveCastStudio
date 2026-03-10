"""
test_phase4.py

Phase 4 テスト: CrawlScheduler

テスト内容:
  1. sources.yaml の読み込みと全ソース確認
  2. 単一ソース オンデマンド巡回（crawl_source_now）
  3. 全ソース一括巡回（crawl_all_now）※時間がかかるため --all オプションで実行
  4. 定期巡回スケジューラの起動・ジョブ登録確認・停止

使い方:
  # 単一ソース巡回のみ（デフォルト: un_news）
  uv run test_phase4.py

  # 巡回ソースを指定
  uv run test_phase4.py --source us_war_dept

  # 全ソース一括巡回
  uv run test_phase4.py --all

  # 定期スケジューラを起動して30秒後に停止（動作確認）
  uv run test_phase4.py --scheduler --duration 30
"""

import argparse
import logging
import sys
import time
from threading import Event

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_phase4")


def test_single_source(source_id: str) -> None:
    """単一ソースのオンデマンド巡回テスト"""
    from scheduler.crawl_scheduler import CrawlScheduler
    from store.article_store import ArticleStore

    logger.info(f"\n[TEST] crawl_source_now: {source_id}")
    scheduler = CrawlScheduler()

    try:
        articles = scheduler.crawl_source_now(source_id)
    except ValueError as e:
        logger.error(f"❌ {e}")
        sys.exit(1)

    logger.info(f"\n[RESULT] {source_id}")
    logger.info(f"  Articles crawled : {len(articles)}")
    for a in articles:
        logger.info(
            f"  {'[TOP]' if a.get('is_top_page') else '[ART]'} "
            f"{a.get('title', '')[:60]} | "
            f"text={len(a.get('text_content') or '')} chars"
        )

    store = ArticleStore()
    stats = store.get_stats()
    logger.info(f"\n[DB STATS] total={stats['total']}, analyzed={stats['analyzed']}")
    logger.info("✅ crawl_source_now OK")


def test_crawl_all() -> None:
    """全ソース一括巡回テスト"""
    from scheduler.crawl_scheduler import CrawlScheduler
    from store.article_store import ArticleStore

    logger.info("\n[TEST] crawl_all_now")
    scheduler = CrawlScheduler()
    results = scheduler.crawl_all_now()

    logger.info("\n[RESULT] crawl_all_now")
    total = 0
    for source_id, articles in results.items():
        logger.info(f"  {source_id}: {len(articles)} articles")
        total += len(articles)
    logger.info(f"  Total: {total} articles")

    store = ArticleStore()
    stats = store.get_stats()
    logger.info(f"\n[DB STATS]")
    logger.info(f"  Total    : {stats['total']}")
    logger.info(f"  Analyzed : {stats['analyzed']}")
    for src, cnt in stats["by_source"].items():
        logger.info(f"  {src}: {cnt}")
    logger.info("✅ crawl_all_now OK")


def test_scheduler(duration_sec: int) -> None:
    """定期スケジューラの起動・ジョブ登録・停止テスト"""
    from scheduler.crawl_scheduler import CrawlScheduler

    logger.info(f"\n[TEST] BackgroundScheduler (duration={duration_sec}s)")
    scheduler = CrawlScheduler()
    stop_event = Event()

    scheduler.start()

    # ジョブ登録確認
    status = scheduler.get_status()
    logger.info(f"  Running     : {status['running']}")
    logger.info(f"  Source count: {status['source_count']}")
    logger.info(f"  Jobs registered: {len(status['jobs'])}")
    for job in status["jobs"]:
        logger.info(f"    {job['id']} | next_run: {job['next_run']}")

    assert status["running"], "Scheduler should be running"
    assert len(status["jobs"]) == status["source_count"], \
        "Job count should match source count"
    logger.info(f"✅ Scheduler started with {len(status['jobs'])} jobs")

    # duration_sec 秒後に停止
    logger.info(f"  Waiting {duration_sec}s before stopping...")
    try:
        time.sleep(duration_sec)
    except KeyboardInterrupt:
        logger.info("  KeyboardInterrupt received")

    scheduler.stop()
    status = scheduler.get_status()
    assert not status["running"], "Scheduler should be stopped"
    logger.info("✅ Scheduler stopped cleanly")


def main():
    parser = argparse.ArgumentParser(description="Phase 4 Test: CrawlScheduler")
    parser.add_argument(
        "--source", default="un_news",
        help="単一ソース巡回時のソースID（デフォルト: un_news）"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="全ソース一括巡回テストを実行する"
    )
    parser.add_argument(
        "--scheduler", action="store_true",
        help="定期スケジューラの起動確認テストを実行する"
    )
    parser.add_argument(
        "--duration", type=int, default=30,
        help="--scheduler モード時の動作確認時間（秒、デフォルト: 30）"
    )
    args = parser.parse_args()

    logger.info("=== Phase 4 Test: CrawlScheduler ===")

    # sources.yaml の読み込み確認
    logger.info("\n[CHECK] sources.yaml")
    try:
        from scheduler.crawl_scheduler import CrawlScheduler
        scheduler = CrawlScheduler()
        status = scheduler.get_status()
        logger.info(f"  ✅ {status['source_count']} sources loaded")
    except FileNotFoundError as e:
        logger.error(f"❌ {e}")
        sys.exit(1)

    if args.all:
        test_crawl_all()
    elif args.scheduler:
        test_scheduler(args.duration)
    else:
        test_single_source(args.source)

    logger.info("\n✅ Phase 4 test complete!")


if __name__ == "__main__":
    main()



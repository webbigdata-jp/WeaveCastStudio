"""
test_phase1.py

Phase 1（Playwright Crawler + Article Store）の動作確認スクリプト。
sources.yamlの1サイトのみ巡回し、DBへの格納・検索を確認する。

使い方:
  python test_phase1.py                      # デフォルト: un_news
  python test_phase1.py --source centcom     # 指定ソースで実行
  python test_phase1.py --source reuters_mideast
  python test_phase1.py --list-sources       # 登録済みソース一覧を表示
"""

import argparse
import logging
import sys
from pathlib import Path

import yaml

# プロジェクトルートをパスに追加
sys.path.insert(0, str(Path(__file__).parent))

from crawler.drission_crawler import DrissionCrawler
from store.article_store import ArticleStore

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_phase1")


def load_sources(config_path: str = "config/sources.yaml") -> list[dict]:
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def run_test(source_id: str) -> None:
    sources = load_sources()
    source = next((s for s in sources if s["id"] == source_id), None)

    if source is None:
        logger.error(
            f"Source '{source_id}' not found. "
            f"Available: {[s['id'] for s in sources]}"
        )
        sys.exit(1)

    logger.info(f"=== Phase 1 Test: {source['name']} ===")
    logger.info(f"  URL        : {source['url']}")
    logger.info(f"  Credibility: {source['credibility']}/5  Tier: {source['tier']}")

    # ── Crawler ──
    crawler = DrissionCrawler(output_dir="data/crawl")
    articles = crawler.crawl_source(source)

    logger.info(f"\n[RESULT] {len(articles)} articles crawled")
    for i, a in enumerate(articles):
        logger.info(
            f"  [{i}] {a['title'][:70]}\n"
            f"       URL       : {a['url'][:80]}\n"
            f"       Screenshot: {a.get('screenshot_path', 'N/A')}\n"
            f"       Text len  : {len(a.get('text_content', ''))}"
        )

    # ── ArticleStore ──
    store = ArticleStore(db_path="data/articles.db")
    saved_count = 0
    for article in articles:
        article_id = store.save_article(article)
        if article_id:
            saved_count += 1

    stats = store.get_stats()
    logger.info(f"\n[DB STATS]")
    logger.info(f"  Total articles : {stats['total']}")
    logger.info(f"  Analyzed       : {stats['analyzed']}")
    logger.info(f"  Unanalyzed     : {stats['unanalyzed']}")
    logger.info(f"  New saves      : {saved_count}")
    logger.info(f"  By source      : {stats['by_source']}")

    # ── 未分析記事の確認 ──
    unanalyzed = store.get_unanalyzed(limit=5)
    logger.info(f"\n[UNANALYZED (top 5)]")
    for a in unanalyzed:
        logger.info(f"  id={a['id']} | {a['title'][:60]}")

    logger.info("\n✅ Phase 1 test complete!")
    logger.info(
        f"   Screenshots saved in: data/crawl/{source_id}/"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase 1 Test")
    parser.add_argument(
        "--source",
        default="un_news",
        help="Source ID to crawl (default: un_news)",
    )
    parser.add_argument(
        "--list-sources",
        action="store_true",
        help="List all registered sources and exit",
    )
    args = parser.parse_args()

    if args.list_sources:
        sources = load_sources()
        print("\nRegistered sources:")
        for s in sources:
            print(
                f"  {s['id']:<25} Tier {s['tier']}  "
                f"credibility={s['credibility']}/5  "
                f"interval={s.get('crawl_interval_min', 15)}min"
            )
        return

    run_test(args.source)


if __name__ == "__main__":
    main()

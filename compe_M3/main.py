"""
compe_M3/main.py

M3 Fact Checker / Crawler — Unified CLI

Subcommands:
  crawl     — Crawl sources and save to DB (replaces test_phase1)
  analyze   — Gemini analysis (replaces test_phase2)
  compose   — Generate briefing video / short clips (replaces test_phase3)
  schedule  — Periodic crawl scheduler (replaces test_phase4)
  pipeline  — Run crawl → analyze → compose in one shot (replaces test_phase4 --all)

Usage:
  # Crawl sources
  uv run main.py crawl                          # Default: un_news
  uv run main.py crawl --source centcom          # Specific source
  uv run main.py crawl --all                     # All sources at once
  uv run main.py crawl --list-sources            # List registered sources

  # Analyze
  uv run main.py analyze                         # Batch-analyze unanalyzed articles
  uv run main.py analyze --limit 3               # Limit to 3 articles

  # Generate briefing video
  uv run main.py compose                         # Full run
  uv run main.py compose --dry-run               # Script only (skip video generation)
  uv run main.py compose --short-clips           # Short clip mode
  uv run main.py compose --short-clips --limit 1 # Single clip

  # Scheduler
  uv run main.py schedule                        # Daemon mode (Ctrl+C to stop)
  uv run main.py schedule --duration 30          # Auto-stop after 30s (for testing)

  # Full pipeline
  uv run main.py pipeline                        # crawl_all → analyze → compose
  uv run main.py pipeline --dry-run              # Run compose in dry_run mode

  # Common options
  uv run main.py --debug crawl                   # Enable debug output
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from threading import Event

import yaml

# ── sys.path setup ──
# M3 root (for crawler/, store/, analyst/, composer/ imports)
_M3_ROOT = Path(__file__).parent
if str(_M3_ROOT) not in sys.path:
    sys.path.insert(0, str(_M3_ROOT))

# Project root (for content_index, shared/ imports)
_PROJECT_ROOT = _M3_ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("compe_m3")


# ══════════════════════════════════════════════
# Subcommand implementations
# ══════════════════════════════════════════════


def cmd_crawl(args: argparse.Namespace) -> None:
    """Crawl sources and save to DB"""
    from crawler.drission_crawler import DrissionCrawler
    from store.article_store import ArticleStore

    if args.list_sources:
        sources = _load_sources()
        print("\nRegistered sources:")
        for s in sources:
            print(
                f"  {s['id']:<25} Tier {s['tier']}  "
                f"credibility={s['credibility']}/5  "
                f"interval={s.get('crawl_interval_min', 15)}min"
            )
        return

    if args.all:
        _crawl_all(args)
        return

    # Single source crawl
    source_id = args.source
    sources = _load_sources()
    source = next((s for s in sources if s["id"] == source_id), None)
    if source is None:
        logger.error(
            f"Source '{source_id}' not found. "
            f"Available: {[s['id'] for s in sources]}"
        )
        sys.exit(1)

    logger.info(f"=== Crawl: {source['name']} ===")
    logger.info(f"  URL        : {source['url']}")
    logger.info(f"  Credibility: {source['credibility']}/5  Tier: {source['tier']}")

    crawler = DrissionCrawler(output_dir="data/crawl")
    articles = crawler.crawl_source(source)

    store = ArticleStore(db_path="data/articles.db")
    saved_count = 0
    for article in articles:
        article_id = store.save_article(article)
        if article_id:
            saved_count += 1

    logger.info(f"Crawled: {len(articles)} articles, {saved_count} new saves")

    if args.debug:
        _print_crawl_debug(articles, store, source_id)


def _crawl_all(args: argparse.Namespace) -> None:
    """Crawl all sources at once"""
    from scheduler.crawl_scheduler import CrawlScheduler
    from store.article_store import ArticleStore

    logger.info("=== Crawl All Sources ===")
    scheduler = CrawlScheduler()
    results = scheduler.crawl_all_now()

    total = sum(len(v) for v in results.values())
    logger.info(f"Total: {total} articles from {len(results)} sources")

    if args.debug:
        for source_id, articles in results.items():
            logger.info(f"  {source_id}: {len(articles)} articles")
        store = ArticleStore()
        stats = store.get_stats()
        _print_db_stats(stats)


def cmd_analyze(args: argparse.Namespace) -> None:
    """Run Gemini analysis on unanalyzed articles"""
    from analyst.gemini_client import GeminiClient
    from analyst.gemini_analyst import GeminiAnalyst
    from store.article_store import ArticleStore

    logger.info("=== Analyze Articles ===")

    # Verify GeminiClient can initialise
    try:
        client = GeminiClient()
    except EnvironmentError as e:
        logger.error(f"GeminiClient init failed: {e}")
        sys.exit(1)

    store = ArticleStore()
    unanalyzed = store.get_unanalyzed(limit=args.limit)

    if not unanalyzed:
        logger.info("No unanalyzed articles found.")
        if args.debug:
            stats = store.get_stats()
            _print_db_stats(stats)
        return

    logger.info(f"{len(unanalyzed)} unanalyzed articles found")

    if args.debug:
        for a in unanalyzed:
            logger.info(
                f"  id={a['id']} | {a['title'][:60]} | "
                f"text={len(a.get('text_content') or '')} chars"
            )

    analyst = GeminiAnalyst()
    results = analyst.batch_analyze(unanalyzed)

    for r in results:
        store.update_analysis(r["article_id"], r)
        logger.info(
            f"  id={r['article_id']} | "
            f"score={r['importance_score']:.1f} | "
            f"{r['summary'][:60]}"
        )

    logger.info(f"Analyzed: {len(results)}/{len(unanalyzed)} articles")

    if args.debug:
        stats = store.get_stats()
        _print_db_stats(stats)


def cmd_compose(args: argparse.Namespace) -> None:
    """Generate briefing video / short clips"""
    logger.info("=== Compose ===")
    logger.info(f"  mode       : {'short_clips' if args.short_clips else 'briefing'}")
    logger.info(f"  dry_run    : {args.dry_run}")
    logger.info(f"  hours      : {args.hours}")

    # Check shared/ symlink (backward compat: handle legacy environments with old symlink)
    # Verify that shared/ under the project root (WeaveCastStudio/) is importable
    try:
        from shared import narrator  # noqa: F401
    except ImportError:
        logger.error(
            "shared/ module not found.\n"
            "Please ensure the shared/ directory exists under the project root "
            "(WeaveCastStudio/)."
        )
        sys.exit(1)

    # Verify there are analyzed articles to work with
    from store.article_store import ArticleStore
    store = ArticleStore()
    stats = store.get_stats()
    if stats.get("analyzed", 0) == 0:
        logger.error(
            "No analyzed articles found.\n"
            "Please run the following first:\n"
            "  uv run main.py crawl\n"
            "  uv run main.py analyze"
        )
        sys.exit(1)
    logger.info(f"  DB stats   : total={stats['total']}, analyzed={stats['analyzed']}")

    from composer.briefing_composer import BriefingComposer
    try:
        composer = BriefingComposer()
    except Exception as e:
        logger.error(f"BriefingComposer init failed: {e}")
        sys.exit(1)

    if args.short_clips:
        _compose_short_clips(composer, args, store)
    else:
        _compose_briefing(composer, args)


def _compose_briefing(composer, args: argparse.Namespace) -> None:
    """Generate full briefing video"""
    try:
        result = composer.compose(hours=args.hours, dry_run=args.dry_run)
    except RuntimeError as e:
        logger.error(f"compose() failed: {e}")
        sys.exit(1)

    logger.info(f"Output dir   : {result['output_dir']}")
    logger.info(f"Articles     : {result['article_count']}")
    logger.info(f"Script       : {result['script_path']}")
    logger.info(f"Video        : {result['video_path'] or '(skipped)'}")

    if args.debug:
        script_path = Path(result["script_path"])
        if script_path.exists():
            preview = script_path.read_text(encoding="utf-8")[:300]
            logger.info(f"[SCRIPT PREVIEW]\n{preview}...")

    if args.dry_run:
        logger.info("dry_run complete. To generate video: uv run main.py compose")
    elif result["video_path"]:
        video = Path(result["video_path"])
        if video.exists():
            size_mb = video.stat().st_size / 1024 / 1024
            logger.info(f"Video: {video} ({size_mb:.1f} MB)")
        else:
            logger.error("Video file was not generated")
            sys.exit(1)


def _compose_short_clips(composer, args: argparse.Namespace, store) -> None:
    """Generate short clips"""
    article_ids = None
    if args.limit is not None:
        top_articles = store.get_top_articles(limit=args.limit, hours=args.hours)
        article_ids = [a["id"] for a in top_articles]
        logger.info(f"  --limit {args.limit}: article_ids={article_ids}")

    try:
        result = composer.compose_short_clips(
            hours=args.hours,
            article_ids=article_ids,
            dry_run=args.dry_run,
        )
    except RuntimeError as e:
        logger.error(f"compose_short_clips() failed: {e}")
        sys.exit(1)

    logger.info(f"Output dir   : {result['output_dir']}")
    logger.info(f"Total        : {result['total']}")
    logger.info(f"Succeeded    : {result['succeeded']}")

    if args.debug:
        for i, clip in enumerate(result.get("clips", []), start=1):
            video_info = clip.get("video_path") or "(skipped)"
            logger.info(
                f"  clip {i:03d} | "
                f"article_id={clip['article_id']} | "
                f"title={clip.get('title', '')[:40]} | "
                f"video={video_info}"
            )

    if args.dry_run:
        logger.info("dry_run complete. To generate clips: uv run main.py compose --short-clips")
    elif result["succeeded"] == 0:
        logger.error("No short clips were generated")
        sys.exit(1)
    else:
        logger.info(f"{result['succeeded']}/{result['total']} clips generated.")


def cmd_schedule(args: argparse.Namespace) -> None:
    """Periodic crawl scheduler"""
    from scheduler.crawl_scheduler import CrawlScheduler

    logger.info("=== Scheduler ===")
    scheduler = CrawlScheduler()

    if args.duration:
        # Test mode: auto-stop after the specified number of seconds
        logger.info(f"Test mode: running for {args.duration}s")
        scheduler.start()

        status = scheduler.get_status()
        logger.info(f"  Running     : {status['running']}")
        logger.info(f"  Sources     : {status['source_count']}")
        logger.info(f"  Jobs        : {len(status['jobs'])}")

        if args.debug:
            for job in status["jobs"]:
                logger.info(f"    {job['id']} | next_run: {job['next_run']}")

        try:
            time.sleep(args.duration)
        except KeyboardInterrupt:
            logger.info("KeyboardInterrupt received")

        scheduler.stop()
        logger.info("Scheduler stopped cleanly")
    else:
        # Daemon mode: run until Ctrl+C
        scheduler.run_forever()


def cmd_pipeline(args: argparse.Namespace) -> None:
    """Run crawl_all → analyze → compose in one shot"""
    from scheduler.crawl_scheduler import CrawlScheduler
    from analyst.gemini_analyst import GeminiAnalyst
    from store.article_store import ArticleStore
    from composer.briefing_composer import BriefingComposer

    logger.info("=== Pipeline: crawl → analyze → compose ===")

    # ── Step 1: Crawl all sources ──
    logger.info("[1/3] Crawling all sources...")
    scheduler = CrawlScheduler()
    results = scheduler.crawl_all_now()
    total_crawled = sum(len(v) for v in results.values())
    logger.info(f"  Crawled: {total_crawled} articles from {len(results)} sources")

    # ── Step 2: Analyze unanalyzed articles ──
    logger.info("[2/3] Analyzing unanalyzed articles...")
    store = ArticleStore()
    unanalyzed = store.get_unanalyzed(limit=50)
    if unanalyzed:
        analyst = GeminiAnalyst()
        analyzed = analyst.batch_analyze(unanalyzed)
        for r in analyzed:
            store.update_analysis(r["article_id"], r)
        logger.info(f"  Analyzed: {len(analyzed)} articles")
    else:
        logger.info("  No unanalyzed articles")

    # ── Step 3: Generate briefing ──
    logger.info("[3/3] Composing briefing...")
    composer = BriefingComposer()
    try:
        result = composer.compose(
            hours=args.hours,
            dry_run=args.dry_run,
        )
        logger.info(f"  Output: {result['output_dir']}")
        logger.info(f"  Video : {result['video_path'] or '(dry_run)'}")
    except RuntimeError as e:
        logger.error(f"  Compose failed: {e}")
        sys.exit(1)

    logger.info("Pipeline complete!")

    if args.debug:
        stats = store.get_stats()
        _print_db_stats(stats)


# ══════════════════════════════════════════════
# Helpers
# ══════════════════════════════════════════════


def _load_sources(config_path: str = "config/sources.yaml") -> list[dict]:
    """Load sources from sources.yaml"""
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def _print_db_stats(stats: dict) -> None:
    """Print DB statistics to log (called only when --debug is set)"""
    logger.info("[DB STATS]")
    logger.info(f"  Total    : {stats['total']}")
    logger.info(f"  Analyzed : {stats['analyzed']}")
    logger.info(f"  Unanalyzed: {stats['unanalyzed']}")
    for src, cnt in stats.get("by_source", {}).items():
        logger.info(f"  {src}: {cnt}")


def _print_crawl_debug(articles: list[dict], store, source_id: str) -> None:
    """Print crawl results for debug output"""
    for i, a in enumerate(articles):
        logger.info(
            f"  [{i}] {a['title'][:70]}\n"
            f"       URL       : {a['url'][:80]}\n"
            f"       Screenshot: {a.get('screenshot_path', 'N/A')}\n"
            f"       Text len  : {len(a.get('text_content', ''))}"
        )

    stats = store.get_stats()
    _print_db_stats(stats)

    unanalyzed = store.get_unanalyzed(limit=5)
    logger.info(f"[UNANALYZED (top 5)]")
    for a in unanalyzed:
        logger.info(f"  id={a['id']} | {a['title'][:60]}")

    logger.info(f"Screenshots saved in: data/crawl/{source_id}/")


# ══════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WeaveCastStudio M3 — Fact Checker / Crawler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="Enable debug output (DB stats, detailed logs, etc.)",
    )

    subparsers = parser.add_subparsers(dest="command", help="Subcommand")

    # ── crawl ──
    p_crawl = subparsers.add_parser("crawl", help="Crawl sources and save to DB")
    p_crawl.add_argument("--source", default="un_news", help="Source ID to crawl")
    p_crawl.add_argument("--all", action="store_true", help="Crawl all sources at once")
    p_crawl.add_argument("--list-sources", action="store_true", help="List registered sources")

    # ── analyze ──
    p_analyze = subparsers.add_parser("analyze", help="Run Gemini analysis")
    p_analyze.add_argument("--limit", type=int, default=20, help="Max number of articles to analyze")

    # ── compose ──
    p_compose = subparsers.add_parser("compose", help="Generate briefing / short clips")
    p_compose.add_argument("--dry-run", action="store_true", help="Generate script only (skip video)")
    p_compose.add_argument("--short-clips", action="store_true", help="Short clip mode")
    p_compose.add_argument("--hours", type=int, default=720, help="Time window for articles (hours)")
    p_compose.add_argument("--limit", type=int, default=None, help="Max number of clips to generate")

    # ── schedule ──
    p_schedule = subparsers.add_parser("schedule", help="Periodic crawl scheduler")
    p_schedule.add_argument("--duration", type=int, default=None,
                            help="Seconds before auto-stop (omit for daemon mode until Ctrl+C)")

    # ── pipeline ──
    p_pipeline = subparsers.add_parser("pipeline", help="Run crawl → analyze → compose in one shot")
    p_pipeline.add_argument("--dry-run", action="store_true", help="Run compose in dry_run mode")
    p_pipeline.add_argument("--hours", type=int, default=720, help="Time window for compose (hours)")

    args = parser.parse_args()

    # ── Logging setup ──
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # ── Dispatch subcommand ──
    commands = {
        "crawl": cmd_crawl,
        "analyze": cmd_analyze,
        "compose": cmd_compose,
        "schedule": cmd_schedule,
        "pipeline": cmd_pipeline,
    }
    commands[args.command](args)


if __name__ == "__main__":
    main()

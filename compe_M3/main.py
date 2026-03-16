"""
compe_M3/main.py

M3 Fact Checker / Crawler — 統合 CLI

サブコマンド:
  crawl     — ソース巡回 + DB保存（旧 test_phase1）
  analyze   — Gemini 分析（旧 test_phase2）
  compose   — ブリーフィング動画 / ショートクリップ生成（旧 test_phase3）
  schedule  — 定期巡回スケジューラ（旧 test_phase4）
  pipeline  — crawl → analyze → compose 一括実行（旧 test_phase4 --all 相当）

使い方:
  # ソース巡回
  uv run main.py crawl                          # デフォルト: un_news
  uv run main.py crawl --source centcom          # 指定ソース
  uv run main.py crawl --all                     # 全ソース一括
  uv run main.py crawl --list-sources            # 登録ソース一覧

  # 分析
  uv run main.py analyze                         # 未分析記事をバッチ分析
  uv run main.py analyze --limit 3               # 最大3件のみ

  # ブリーフィング動画生成
  uv run main.py compose                         # フル実行
  uv run main.py compose --dry-run               # 原稿のみ（動画生成スキップ）
  uv run main.py compose --short-clips           # ショートクリップモード
  uv run main.py compose --short-clips --limit 1 # 1件のみ

  # 定期スケジューラ
  uv run main.py schedule                        # デーモン起動（Ctrl+C で停止）
  uv run main.py schedule --duration 30          # 30秒後に自動停止（動作確認用）

  # 一括パイプライン
  uv run main.py pipeline                        # crawl_all → analyze → compose
  uv run main.py pipeline --dry-run              # compose を dry_run で実行

  # 共通オプション
  uv run main.py --debug crawl                   # デバッグ出力あり
"""

import argparse
import logging
import sys
import time
from pathlib import Path
from threading import Event

import yaml

# ── sys.path 設定 ──
# M3 ルート（crawler/, store/, analyst/, composer/ の import 用）
_M3_ROOT = Path(__file__).parent
if str(_M3_ROOT) not in sys.path:
    sys.path.insert(0, str(_M3_ROOT))

# プロジェクトルート（content_index, shared/ の import 用）
_PROJECT_ROOT = _M3_ROOT.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger("compe_m3")


# ══════════════════════════════════════════════
# サブコマンド実装
# ══════════════════════════════════════════════


def cmd_crawl(args: argparse.Namespace) -> None:
    """ソース巡回 + DB保存"""
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

    # 単一ソース巡回
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
    """全ソース一括巡回"""
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
    """Gemini 分析"""
    from analyst.gemini_client import GeminiClient
    from analyst.gemini_analyst import GeminiAnalyst
    from store.article_store import ArticleStore

    logger.info("=== Analyze Articles ===")

    # GeminiClient 初期化確認
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
    """ブリーフィング動画 / ショートクリップ生成"""
    logger.info("=== Compose ===")
    logger.info(f"  mode       : {'short_clips' if args.short_clips else 'briefing'}")
    logger.info(f"  dry_run    : {args.dry_run}")
    logger.info(f"  hours      : {args.hours}")

    # shared/ symlink チェック（後方互換: 旧環境で symlink が残っている場合への対応）
    # プロジェクトルートの shared/ が存在し import 可能であることを確認
    try:
        from shared import narrator  # noqa: F401
    except ImportError:
        logger.error(
            "shared/ モジュールが見つかりません。\n"
            "プロジェクトルート（WeaveCastStudio/）に shared/ ディレクトリが "
            "存在することを確認してください。"
        )
        sys.exit(1)

    # 分析済み記事の存在確認
    from store.article_store import ArticleStore
    store = ArticleStore()
    stats = store.get_stats()
    if stats.get("analyzed", 0) == 0:
        logger.error(
            "分析済み記事がありません。\n"
            "先に以下を実行してください:\n"
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
    """ブリーフィング動画生成"""
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
            logger.error("動画ファイルが生成されませんでした")
            sys.exit(1)


def _compose_short_clips(composer, args: argparse.Namespace, store) -> None:
    """ショートクリップ生成"""
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
        logger.error("ショートクリップが1件も生成されませんでした")
        sys.exit(1)
    else:
        logger.info(f"{result['succeeded']}/{result['total']} clips generated.")


def cmd_schedule(args: argparse.Namespace) -> None:
    """定期巡回スケジューラ"""
    from scheduler.crawl_scheduler import CrawlScheduler

    logger.info("=== Scheduler ===")
    scheduler = CrawlScheduler()

    if args.duration:
        # 動作確認モード: 指定秒数後に自動停止
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
        # デーモンモード: Ctrl+C まで常駐
        scheduler.run_forever()


def cmd_pipeline(args: argparse.Namespace) -> None:
    """crawl_all → analyze → compose 一括実行"""
    from scheduler.crawl_scheduler import CrawlScheduler
    from analyst.gemini_analyst import GeminiAnalyst
    from store.article_store import ArticleStore
    from composer.briefing_composer import BriefingComposer

    logger.info("=== Pipeline: crawl → analyze → compose ===")

    # ── Step 1: 全ソース巡回 ──
    logger.info("[1/3] Crawling all sources...")
    scheduler = CrawlScheduler()
    results = scheduler.crawl_all_now()
    total_crawled = sum(len(v) for v in results.values())
    logger.info(f"  Crawled: {total_crawled} articles from {len(results)} sources")

    # ── Step 2: 未分析記事を分析 ──
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

    # ── Step 3: ブリーフィング生成 ──
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
# ヘルパー
# ══════════════════════════════════════════════


def _load_sources(config_path: str = "config/sources.yaml") -> list[dict]:
    """sources.yaml を読み込む"""
    with open(config_path, encoding="utf-8") as f:
        return yaml.safe_load(f)["sources"]


def _print_db_stats(stats: dict) -> None:
    """DB統計をログ出力（--debug時のみ呼ばれる）"""
    logger.info("[DB STATS]")
    logger.info(f"  Total    : {stats['total']}")
    logger.info(f"  Analyzed : {stats['analyzed']}")
    logger.info(f"  Unanalyzed: {stats['unanalyzed']}")
    for src, cnt in stats.get("by_source", {}).items():
        logger.info(f"  {src}: {cnt}")


def _print_crawl_debug(articles: list[dict], store, source_id: str) -> None:
    """巡回結果のデバッグ出力"""
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
# エントリポイント
# ══════════════════════════════════════════════


def main() -> None:
    parser = argparse.ArgumentParser(
        description="WeaveCastStudio M3 — Fact Checker / Crawler",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--debug", action="store_true",
        help="デバッグ出力を有効にする（DB統計、詳細ログ等）",
    )

    subparsers = parser.add_subparsers(dest="command", help="サブコマンド")

    # ── crawl ──
    p_crawl = subparsers.add_parser("crawl", help="ソース巡回 + DB保存")
    p_crawl.add_argument("--source", default="un_news", help="巡回するソースID")
    p_crawl.add_argument("--all", action="store_true", help="全ソース一括巡回")
    p_crawl.add_argument("--list-sources", action="store_true", help="登録ソース一覧を表示")

    # ── analyze ──
    p_analyze = subparsers.add_parser("analyze", help="Gemini 分析")
    p_analyze.add_argument("--limit", type=int, default=20, help="最大分析件数")

    # ── compose ──
    p_compose = subparsers.add_parser("compose", help="ブリーフィング / クリップ生成")
    p_compose.add_argument("--dry-run", action="store_true", help="原稿のみ生成")
    p_compose.add_argument("--short-clips", action="store_true", help="ショートクリップモード")
    p_compose.add_argument("--hours", type=int, default=720, help="対象期間（時間）")
    p_compose.add_argument("--limit", type=int, default=None, help="生成数上限")

    # ── schedule ──
    p_schedule = subparsers.add_parser("schedule", help="定期巡回スケジューラ")
    p_schedule.add_argument("--duration", type=int, default=None,
                            help="自動停止までの秒数（省略時はCtrl+Cまで常駐）")

    # ── pipeline ──
    p_pipeline = subparsers.add_parser("pipeline", help="crawl → analyze → compose 一括実行")
    p_pipeline.add_argument("--dry-run", action="store_true", help="compose を dry_run で実行")
    p_pipeline.add_argument("--hours", type=int, default=720, help="compose の対象期間（時間）")

    args = parser.parse_args()

    # ── ロギング設定 ──
    log_level = logging.DEBUG if args.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not args.command:
        parser.print_help()
        sys.exit(0)

    # ── サブコマンド実行 ──
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

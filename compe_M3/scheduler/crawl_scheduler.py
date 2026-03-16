"""
scheduler/crawl_scheduler.py

Periodic crawl scheduler using APScheduler (BackgroundScheduler).

Design notes:
  - Uses BackgroundScheduler (thread-based) — works on Windows / WSL / Linux
    without depending on systemd or cron.
  - DrissionCrawler uses a synchronous API, which plays well with threads.
  - Responsible for crawling + ArticleStore persistence only.
    Gemini analysis and video generation are left to the caller.
  - Each source gets its own independent job (max_instances=1 prevents overlap).

Typical usage:
  # Periodic crawl (daemon)
  scheduler = CrawlScheduler()
  scheduler.start()
  # ... other work on the main thread ...
  scheduler.stop()

  # On-demand full crawl
  scheduler = CrawlScheduler()
  results = scheduler.crawl_all_now()

  # On-demand single-source crawl
  results = scheduler.crawl_source_now("un_news")
"""

import logging
import sys
import time
from pathlib import Path
from threading import Event

import yaml
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from crawler.drission_crawler import DrissionCrawler
from store.article_store import ArticleStore

# Add project root to sys.path (for content_index import)
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# Default path to sources.yaml
_DEFAULT_CONFIG = Path("config/sources.yaml")

# Default crawl interval per source when crawl_interval_min is absent in sources.yaml
_DEFAULT_INTERVAL_MIN = 15


class CrawlScheduler:
    """
    Periodic source crawler driven by sources.yaml.

    Args:
        config_path: path to sources.yaml
        output_dir: directory for crawl artifacts
    """

    def __init__(
        self,
        config_path: str | Path = _DEFAULT_CONFIG,
        output_dir: str = "data/crawl",
    ):
        self._config_path = Path(config_path)
        self._sources = self._load_sources()
        self._crawler = DrissionCrawler(output_dir=output_dir)
        self._store = ArticleStore()
        self._scheduler = BackgroundScheduler(
            job_defaults={
                "coalesce": True,          # merge multiple delayed runs into one
                "max_instances": 1,        # prevent concurrent runs of the same job
                "misfire_grace_time": 60,  # tolerate up to 60s of timing drift
            }
        )
        self._running = False

    # ──────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────

    def start(self) -> None:
        """
        Start periodic crawling in the background.
        Registers one job per source according to its crawl_interval_min.
        """
        if self._running:
            logger.warning("[Scheduler] Already running")
            return

        for source in self._sources:
            interval_min = source.get("crawl_interval_min", _DEFAULT_INTERVAL_MIN)
            job_id = f"crawl_{source['id']}"

            self._scheduler.add_job(
                func=self._crawl_and_store,
                trigger=IntervalTrigger(minutes=interval_min),
                args=[source],
                id=job_id,
                name=f"Crawl: {source['name']}",
                replace_existing=True,
            )
            logger.info(
                f"[Scheduler] Registered: {source['name']} "
                f"(every {interval_min} min, id={job_id})"
            )

        self._scheduler.start()
        self._running = True
        logger.info(
            f"[Scheduler] Started with {len(self._sources)} sources"
        )

    def stop(self, wait: bool = True) -> None:
        """
        Stop the periodic crawler.

        Args:
            wait: if True, block until all running jobs complete
        """
        if not self._running:
            return
        self._scheduler.shutdown(wait=wait)
        self._running = False
        logger.info("[Scheduler] Stopped")

    def crawl_all_now(self) -> dict[str, list[dict]]:
        """
        Crawl all sources immediately (on-demand).
        Runs independently of the background scheduler.

        Returns:
            dict mapping source_id → list of article dicts
        """
        logger.info(f"[Scheduler] crawl_all_now: {len(self._sources)} sources")
        results: dict[str, list[dict]] = {}

        for source in self._sources:
            source_id = source["id"]
            try:
                articles = self._crawl_and_store(source)
                results[source_id] = articles
            except Exception as e:
                logger.error(
                    f"[Scheduler] crawl_all_now failed for {source_id}: {e}",
                    exc_info=True,
                )
                results[source_id] = []

        total = sum(len(v) for v in results.values())
        logger.info(
            f"[Scheduler] crawl_all_now complete: "
            f"{total} articles total"
        )
        return results

    def crawl_source_now(self, source_id: str) -> list[dict]:
        """
        Crawl a single source immediately (on-demand).

        Args:
            source_id: the id field from sources.yaml
        Returns:
            list of crawled article dicts
        Raises:
            ValueError: if source_id is not found
        """
        source = self._find_source(source_id)
        if source is None:
            raise ValueError(
                f"Source '{source_id}' not found. "
                f"Available: {[s['id'] for s in self._sources]}"
            )
        logger.info(f"[Scheduler] crawl_source_now: {source['name']}")
        return self._crawl_and_store(source)

    def get_status(self) -> dict:
        """
        Return the current scheduler state (for debug / monitoring).

        Returns:
            {
              "running": bool,
              "source_count": int,
              "jobs": [{"id": str, "name": str, "next_run": str}, ...]
            }
        """
        jobs = []
        if self._running:
            for job in self._scheduler.get_jobs():
                next_run = job.next_run_time
                jobs.append({
                    "id": job.id,
                    "name": job.name,
                    "next_run": next_run.isoformat() if next_run else "paused",
                })
        return {
            "running": self._running,
            "source_count": len(self._sources),
            "jobs": jobs,
        }

    def run_forever(self, stop_event: Event | None = None) -> None:
        """
        Call start() then block the main thread indefinitely.
        Exits on Ctrl+C or when stop_event.set() is called.

        Args:
            stop_event: threading.Event — set it to trigger a clean shutdown
                        (useful for tests and integration scenarios)
        """
        self.start()
        logger.info("[Scheduler] Running. Press Ctrl+C to stop.")
        try:
            while True:
                if stop_event and stop_event.is_set():
                    break
                time.sleep(1)
        except KeyboardInterrupt:
            logger.info("[Scheduler] KeyboardInterrupt received")
        finally:
            self.stop()

    # ──────────────────────────────────────────
    # Internal helpers
    # ──────────────────────────────────────────

    _last_new_saves: int = 0  # simple counter for crawl_all_now

    def _crawl_and_store(self, source: dict) -> list[dict]:
        """
        Crawl one source, persist results to ArticleStore, and return the article list.
        Used both as an APScheduler job function and as a direct call.
        """
        logger.info(f"[Scheduler] Crawling: {source['name']}")
        try:
            articles = self._crawler.crawl_source(source)
        except Exception as e:
            logger.error(
                f"[Scheduler] Crawl failed for {source['id']}: {e}",
                exc_info=True,
            )
            return []

        new_saves = 0
        for article in articles:
            article_id = self._store.save_article(article)
            if article_id is not None:
                article["id"] = article_id
                new_saves += 1

        self._last_new_saves = new_saves
        logger.info(
            f"[Scheduler] {source['name']}: "
            f"{len(articles)} crawled, {new_saves} new saves"
        )
        # Register articles with importance_score >= 9.0 as BREAKING in ContentIndex immediately
        from content_index import ContentIndexManager, make_entry
        mgr = ContentIndexManager()
        for article in articles:
            score = article.get("importance_score") or 0.0
            if score >= 9.0 and article.get("screenshot_path"):
                tags = _parse_json_field(article.get("topics"), [])
                entry = make_entry(
                    id=f"m3_breaking_{article['source_id']}_{article['id']}",
                    module="M3",
                    content_type="screenshot",
                    title=article.get("title", ""),
                    topic_tags=tags,
                    source_id=article.get("source_id"),
                    source_name=article.get("source_name"),
                    importance_score=score,
                    is_breaking=True,
                    screenshot_path=article.get("screenshot_path"),
                )
                mgr.add_entry(entry)
                logger.info(f"[Scheduler] BREAKING registered: {entry['id']}")

        return articles

    def _load_sources(self) -> list[dict]:
        """Load and return sources from sources.yaml"""
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"sources.yaml not found: {self._config_path}"
            )
        with open(self._config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        sources = config.get("sources", [])
        logger.info(f"[Scheduler] Loaded {len(sources)} sources from {self._config_path}")
        return sources

    def _find_source(self, source_id: str) -> dict | None:
        """Return the source config matching source_id, or None"""
        for s in self._sources:
            if s["id"] == source_id:
                return s
        return None


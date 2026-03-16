"""
scheduler/crawl_scheduler.py

APScheduler (BackgroundScheduler) を使った定期巡回スケジューラ。

設計方針:
  - BackgroundScheduler（スレッドベース）を使用
    → Windows / WSL / Linux いずれでも動作（systemd/cron 非依存）
  - DrissionCrawler は同期 API のため、スレッドと相性が良い
  - 巡回 + ArticleStore 保存まで担当。Gemini 分析・動画生成は呼び出し元が判断
  - ソースごとに独立したジョブを登録（max_instances=1 で多重実行を防止）

主な使い方:
  # 定期巡回（デーモン起動）
  scheduler = CrawlScheduler()
  scheduler.start()
  # ... メインスレッドで他の処理 ...
  scheduler.stop()

  # オンデマンド全巡回
  scheduler = CrawlScheduler()
  results = scheduler.crawl_all_now()

  # オンデマンド単一ソース巡回
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

# プロジェクトルートを sys.path に追加（content_index の import 用）
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

logger = logging.getLogger(__name__)

# sources.yaml のデフォルトパス
_DEFAULT_CONFIG = Path("config/sources.yaml")

# ソースごとのデフォルト巡回間隔（sources.yaml に crawl_interval_min がない場合）
_DEFAULT_INTERVAL_MIN = 15


class CrawlScheduler:
    """
    sources.yaml に基づいてソースを定期巡回するスケジューラ。

    Args:
        config_path: sources.yaml のパス
        output_dir: クロール成果物の保存先ディレクトリ
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
                "coalesce": True,       # 遅延した複数実行は1回にまとめる
                "max_instances": 1,     # 同一ジョブの多重実行を防止
                "misfire_grace_time": 60,  # 60秒以内のズレは許容
            }
        )
        self._running = False

    # ──────────────────────────────────────────
    # 公開インターフェース
    # ──────────────────────────────────────────

    def start(self) -> None:
        """
        バックグラウンドで定期巡回を開始する。
        各ソースの crawl_interval_min に従ってジョブを登録する。
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
        定期巡回を停止する。

        Args:
            wait: True の場合、実行中のジョブが完了するまで待機する
        """
        if not self._running:
            return
        self._scheduler.shutdown(wait=wait)
        self._running = False
        logger.info("[Scheduler] Stopped")

    def crawl_all_now(self) -> dict[str, list[dict]]:
        """
        全ソースを即座に一括巡回する（オンデマンド）。
        バックグラウンドスケジューラとは独立して実行される。

        Returns:
            {source_id: [article_dict, ...]} の辞書
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
        new_saves = self._last_new_saves  # _crawl_and_store 内で更新
        logger.info(
            f"[Scheduler] crawl_all_now complete: "
            f"{total} articles total"
        )
        return results

    def crawl_source_now(self, source_id: str) -> list[dict]:
        """
        指定ソースを即座に巡回する（オンデマンド）。

        Args:
            source_id: sources.yaml の id フィールド
        Returns:
            巡回した記事のリスト
        Raises:
            ValueError: source_id が見つからない場合
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
        スケジューラの現在状態を返す（デバッグ・モニタリング用）。

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
        start() 後にメインスレッドをブロックして常駐する。
        Ctrl+C または stop_event.set() で終了する。

        Args:
            stop_event: threading.Event。set() されると終了する（テスト・統合用）
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
    # 内部処理
    # ──────────────────────────────────────────

    _last_new_saves: int = 0  # crawl_all_now 向けの簡易カウンタ

    def _crawl_and_store(self, source: dict) -> list[dict]:
        """
        1 ソースを巡回して ArticleStore に保存し、記事リストを返す。
        APScheduler のジョブ関数としても、直接呼び出しとしても使用する。
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
        # 重要度9.0以上の記事は BREAKING フラグで ContentIndex に即時登録
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
        """sources.yaml を読み込んで返す"""
        if not self._config_path.exists():
            raise FileNotFoundError(
                f"sources.yaml が見つかりません: {self._config_path}"
            )
        with open(self._config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        sources = config.get("sources", [])
        logger.info(f"[Scheduler] Loaded {len(sources)} sources from {self._config_path}")
        return sources

    def _find_source(self, source_id: str) -> dict | None:
        """source_id に一致するソース設定を返す"""
        for s in self._sources:
            if s["id"] == source_id:
                return s
        return None


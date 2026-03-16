"""
store/article_store.py

SQLite store for crawl results (articles, screenshots, metadata).
Also handles writing back Gemini analysis results.
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class ArticleStore:
    """SQLite store for crawl results and Gemini analysis output"""

    def __init__(self, db_path: str = "data/articles.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """Initialise tables and indexes"""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id      TEXT    NOT NULL,
                    source_name    TEXT    NOT NULL,
                    url            TEXT    NOT NULL,
                    url_hash       TEXT    NOT NULL,
                    title          TEXT,
                    text_content   TEXT,
                    screenshot_path TEXT,
                    html_path      TEXT,
                    credibility    INTEGER,
                    tier           INTEGER,
                    is_top_page    BOOLEAN DEFAULT FALSE,

                    -- Gemini analysis results (populated after analyze step)
                    summary            TEXT,
                    importance_score   REAL,
                    importance_reason  TEXT,
                    topics             TEXT,   -- JSON array
                    key_entities       TEXT,   -- JSON array
                    sentiment          TEXT,
                    has_actionable_intel BOOLEAN,
                    ai_image_path      TEXT,

                    crawled_at    TEXT NOT NULL,
                    analyzed_at   TEXT,
                    used_in_briefing BOOLEAN DEFAULT FALSE,
                    is_breaking      BOOLEAN DEFAULT FALSE,

                    UNIQUE(url_hash, crawled_at)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_crawled_at
                ON articles(crawled_at DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_importance
                ON articles(importance_score DESC)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_analyzed
                ON articles(analyzed_at)
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_breaking
                ON articles(is_breaking, importance_score DESC)
            """)
            conn.commit()
        logger.debug(f"ArticleStore initialized: {self.db_path}")

    # ──────────────────────────────────────────
    # Write methods
    # ──────────────────────────────────────────

    def save_article(self, article: dict) -> Optional[int]:
        """
        Save an article and return its new row ID.
        Returns None if the url_hash + crawled_at combination already exists (duplicate skip).

        Args:
            article: dict returned by DrissionCrawler
        Returns:
            int: inserted row ID, or None on duplicate
        """
        url_hash = hashlib.md5(article["url"].encode()).hexdigest()

        with sqlite3.connect(self.db_path) as conn:
            cursor = conn.execute("""
                INSERT OR IGNORE INTO articles (
                    source_id, source_name, url, url_hash,
                    title, text_content, screenshot_path, html_path,
                    credibility, tier, is_top_page, crawled_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                article["source_id"],
                article["source_name"],
                article["url"],
                url_hash,
                article.get("title"),
                article.get("text_content"),
                article.get("screenshot_path"),
                article.get("html_path"),
                article.get("credibility"),
                article.get("tier"),
                article.get("is_top_page", False),
                article["crawled_at"],
            ))
            conn.commit()

            if cursor.rowcount == 0:
                logger.debug(f"Duplicate skipped: {article['url']}")
                return None

            new_id = cursor.lastrowid
            logger.debug(f"Saved article id={new_id}: {article.get('title', '')[:60]}")
            return new_id

    def update_analysis(self, article_id: int, analysis: dict) -> None:
        """
        Write Gemini analysis results back to the DB.

        Args:
            article_id: articles.id
            analysis: dict returned by GeminiAnalyst
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                UPDATE articles SET
                    summary               = ?,
                    importance_score      = ?,
                    importance_reason     = ?,
                    topics                = ?,
                    key_entities          = ?,
                    sentiment             = ?,
                    has_actionable_intel  = ?,
                    ai_image_path         = ?,
                    analyzed_at           = ?
                WHERE id = ?
            """, (
                analysis.get("summary"),
                analysis.get("importance_score"),
                analysis.get("importance_reason"),
                json.dumps(analysis.get("topics", []), ensure_ascii=False),
                json.dumps(analysis.get("key_entities", []), ensure_ascii=False),
                analysis.get("sentiment"),
                analysis.get("has_actionable_intel", False),
                analysis.get("ai_image_path"),
                datetime.now(timezone.utc).isoformat(),
                article_id,
            ))
            conn.commit()
        logger.debug(f"Analysis updated for article id={article_id}")

    def mark_used_in_briefing(self, article_ids: list[int]) -> None:
        """Mark articles as used in a briefing"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE articles SET used_in_briefing = TRUE WHERE id = ?",
                [(aid,) for aid in article_ids],
            )
            conn.commit()

    def mark_breaking(self, article_ids: list[int], breaking: bool = True) -> None:
        """
        Set (or clear) the BREAKING flag on articles.

        Called by CrawlScheduler when importance_score >= 9.0 is detected.
        M4 monitors this flag to trigger emergency ticker overlays.

        Args:
            article_ids: list of article IDs to update
            breaking: True to set BREAKING, False to clear
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE articles SET is_breaking = ? WHERE id = ?",
                [(breaking, aid) for aid in article_ids],
            )
            conn.commit()
        logger.info(
            f"[ArticleStore] mark_breaking({breaking}) applied to {len(article_ids)} articles"
        )

    # ──────────────────────────────────────────
    # Read methods
    # ──────────────────────────────────────────

    def get_unanalyzed(self, limit: int = 20) -> list[dict]:
        """
        Fetch unanalyzed articles ordered by priority (Tier ASC, then newest first).

        Args:
            limit: maximum number of rows to return
        Returns:
            list[dict]: article data
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM articles
                WHERE analyzed_at IS NULL
                ORDER BY tier ASC, crawled_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        return [dict(r) for r in rows]

    def get_top_articles(self, limit: int = 15, hours: int = 6) -> list[dict]:
        """
        Fetch analyzed articles from the last N hours, sorted by importance then credibility.

        Args:
            limit: maximum number of rows to return
            hours: look-back window in hours
        Returns:
            list[dict]: analyzed articles
        """
        cutoff = datetime.now(timezone.utc).isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM articles
                WHERE importance_score IS NOT NULL
                  AND crawled_at >= datetime(?, '-' || ? || ' hours')
                ORDER BY importance_score DESC, credibility DESC
                LIMIT ?
            """, (cutoff, hours, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_by_source(self, source_id: str, limit: int = 10) -> list[dict]:
        """Fetch the most recent articles for a specific source"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM articles
                WHERE source_id = ?
                ORDER BY crawled_at DESC
                LIMIT ?
            """, (source_id, limit)).fetchall()
        return [dict(r) for r in rows]

    def get_by_topic(self, topic: str, limit: int = 10) -> list[dict]:
        """Fetch articles whose topics field contains the given keyword (JSON string search)"""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM articles
                WHERE topics LIKE ?
                  AND importance_score IS NOT NULL
                ORDER BY importance_score DESC
                LIMIT ?
            """, (f'%{topic}%', limit)).fetchall()
        return [dict(r) for r in rows]

    # ──────────────────────────────────────────
    # Search methods for M4
    # ──────────────────────────────────────────

    def search(
        self,
        query: str,
        min_importance: float = 0.0,
        limit: int = 10,
        hours: int | None = None,
    ) -> list[dict]:
        """
        OR-search across title, summary, topics, and key_entities by keyword.

        Designed for natural-language queries from M4 (e.g. "give me a report on the UN").
        Only analyzed articles (importance_score IS NOT NULL) are searched.

        Args:
            query: search keyword (e.g. "UN", "climate", "military")
            min_importance: lower bound for importance_score (0.0 = no filter)
            limit: maximum number of rows to return
            hours: restrict to the last N hours (None = all time)
        Returns:
            list[dict]: articles sorted by importance desc
        """
        like = f"%{query}%"
        params: list = [like, like, like, like, min_importance]

        time_filter = ""
        if hours is not None:
            cutoff = datetime.now(timezone.utc).isoformat()
            time_filter = "AND crawled_at >= datetime(?, '-' || ? || ' hours')"
            params += [cutoff, hours]

        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT * FROM articles
                WHERE importance_score IS NOT NULL
                  AND importance_score >= ?
                  AND (
                      title        LIKE ?
                   OR summary      LIKE ?
                   OR topics       LIKE ?
                   OR key_entities LIKE ?
                  )
                  {time_filter}
                ORDER BY importance_score DESC
                LIMIT ?
            """, [min_importance, like, like, like, like]
                + ([cutoff, hours] if hours is not None else [])
                + [limit]
            ).fetchall()
        logger.debug(f"[ArticleStore] search({query!r}) → {len(rows)} hits")
        return [dict(r) for r in rows]

    def get_breaking(self, limit: int = 10) -> list[dict]:
        """
        Fetch articles flagged as BREAKING, sorted by importance desc.

        CrawlScheduler calls mark_breaking() during crawl when importance_score >= 9.0.
        M4 polls this method to detect emergency ticker events.

        Args:
            limit: maximum number of rows to return
        Returns:
            list[dict]: articles with is_breaking=True, sorted by importance desc
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("""
                SELECT * FROM articles
                WHERE is_breaking = TRUE
                  AND importance_score IS NOT NULL
                ORDER BY importance_score DESC, crawled_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
        logger.debug(f"[ArticleStore] get_breaking() → {len(rows)} hits")
        return [dict(r) for r in rows]

    def get_today_titles(
        self,
        min_importance: float = 0.0,
        analyzed_only: bool = True,
    ) -> list[dict]:
        """
        Return a lightweight list of article titles from the last 24 hours, sorted by importance desc.

        Used by M4 to build "today's top news" summaries for Gemini.
        Returns only lightweight fields: id, title, importance_score, topics,
        source_name, crawled_at, is_breaking.

        Args:
            min_importance: lower bound for importance_score (0.0 = all)
            analyzed_only: if True, return only articles with importance_score IS NOT NULL
        Returns:
            list[dict]: lightweight article dicts
        """
        analysis_filter = "AND importance_score IS NOT NULL" if analyzed_only else ""
        cutoff = datetime.now(timezone.utc).isoformat()

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f"""
                SELECT
                    id, title, importance_score, topics,
                    source_name, crawled_at, is_breaking
                FROM articles
                WHERE crawled_at >= datetime(?, '-24 hours')
                  AND importance_score >= ?
                  {analysis_filter}
                ORDER BY importance_score DESC, crawled_at DESC
            """, (cutoff, min_importance)).fetchall()
        logger.debug(f"[ArticleStore] get_today_titles() → {len(rows)} articles")
        return [dict(r) for r in rows]

    def get_stats(self) -> dict:
        """Return a storage summary dict (for debug / monitoring)"""
        with sqlite3.connect(self.db_path) as conn:
            total = conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
            analyzed = conn.execute(
                "SELECT COUNT(*) FROM articles WHERE analyzed_at IS NOT NULL"
            ).fetchone()[0]
            by_source = conn.execute("""
                SELECT source_id, COUNT(*) as cnt
                FROM articles
                GROUP BY source_id
                ORDER BY cnt DESC
            """).fetchall()
        return {
            "total": total,
            "analyzed": analyzed,
            "unanalyzed": total - analyzed,
            "by_source": {row[0]: row[1] for row in by_source},
        }



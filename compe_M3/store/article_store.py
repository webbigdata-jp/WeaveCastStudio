"""
store/article_store.py

巡回結果（記事・キャプチャ・メタデータ）をSQLiteに格納・検索するストア。
Gemini分析結果の書き戻しも担当する。
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
    """巡回結果を格納・検索するSQLiteストア"""

    def __init__(self, db_path: str = "data/articles.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        """テーブルとインデックスを初期化する"""
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

                    -- Gemini分析結果（analyze後に更新）
                    summary            TEXT,
                    importance_score   REAL,
                    importance_reason  TEXT,
                    topics             TEXT,   -- JSON配列
                    key_entities       TEXT,   -- JSON配列
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
    # 書き込み系
    # ──────────────────────────────────────────

    def save_article(self, article: dict) -> Optional[int]:
        """
        記事を保存し、新規挿入された場合はそのIDを返す。
        同一 url_hash + crawled_at が既存なら None を返す（重複スキップ）。

        Args:
            article: PlaywrightCrawlerが返すdict
        Returns:
            int: 挿入されたrow ID（重複の場合はNone）
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
        Gemini分析結果をDBに書き戻す。

        Args:
            article_id: articles.id
            analysis: GeminiAnalystが返すdict
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
        """ブリーフィングに使用した記事をフラグ立て"""
        with sqlite3.connect(self.db_path) as conn:
            conn.executemany(
                "UPDATE articles SET used_in_briefing = TRUE WHERE id = ?",
                [(aid,) for aid in article_ids],
            )
            conn.commit()

    def mark_breaking(self, article_ids: list[int], breaking: bool = True) -> None:
        """
        BREAKINGフラグを立てる（または解除する）。

        CrawlScheduler が importance_score >= 9.0 の記事を検知した際に呼び出す想定。
        M4 はこのフラグを監視して緊急テロップを流す。

        Args:
            article_ids: フラグを更新する記事IDのリスト
            breaking: True=BREAKING扱い、False=解除
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
    # 読み取り系
    # ──────────────────────────────────────────

    def get_unanalyzed(self, limit: int = 20) -> list[dict]:
        """
        未分析の記事を優先度順（Tier昇順 → 新しい順）で取得する。

        Args:
            limit: 最大取得件数
        Returns:
            list[dict]: 記事データのリスト
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
        直近N時間の記事を重要度スコア降順・信頼性降順で取得する。

        Args:
            limit: 最大取得件数
            hours: 直近何時間以内か
        Returns:
            list[dict]: 分析済み記事のリスト
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
        """特定ソースの最新記事を取得"""
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
        """topicsフィールドにキーワードを含む記事を取得（JSON文字列検索）"""
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
    # M4向け検索メソッド
    # ──────────────────────────────────────────

    def search(
        self,
        query: str,
        min_importance: float = 0.0,
        limit: int = 10,
        hours: int | None = None,
    ) -> list[dict]:
        """
        キーワードでtitle・summary・topics・key_entitiesを横断的にOR検索する。

        M4からの「国連についてレポートを出して」のような自然語クエリを想定。
        分析済み記事（importance_score IS NOT NULL）のみが対象。

        Args:
            query: 検索キーワード（例: "UN", "Iran", "military"）
            min_importance: 重要度スコアの下限（0.0 = フィルタなし）
            limit: 最大取得件数
            hours: 直近N時間以内に限定する場合に指定（None = 全期間）
        Returns:
            list[dict]: 重要度降順の記事リスト
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
        BREAKINGフラグが立っている記事を重要度降順で取得する。

        CrawlScheduler がクロール時に importance_score >= 9.0 の記事に
        mark_breaking() を呼び出しておく前提。
        M4 はこのメソッドを定期ポーリングして緊急テロップを検知する。

        Args:
            limit: 最大取得件数
        Returns:
            list[dict]: is_breaking=True の記事リスト（重要度降順）
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
        直近24時間の記事タイトル一覧を重要度降順で返す。

        M4 が「本日の主要ニュース一覧」を生成する際の入力として使用。
        Gemini に渡しやすいよう、id・title・importance_score・topics の
        軽量フィールドのみを返す。

        Args:
            min_importance: 重要度スコアの下限（0.0 = 全件）
            analyzed_only: True の場合、分析済み（importance_score IS NOT NULL）のみ返す
        Returns:
            list[dict]: 各要素は id, title, importance_score, topics, source_name,
                        crawled_at, is_breaking のみを含む軽量dict
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
        """格納状況のサマリを返す（デバッグ用）"""
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



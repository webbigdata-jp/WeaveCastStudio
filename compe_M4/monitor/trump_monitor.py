"""
compe_M4/monitor/trump_monitor.py

Trump Truth Social 監視モジュール。
trumpstruth.org を定期ポーリングし、新着投稿を Gemini で重要度判定して
Breaking ニュースとして ArticleStore に投入する。

- M4 起動時に TrumpMonitor(db_path=...).start() でバックグラウンド動作
- 将来的に同インターフェースで Reddit 等を追加可能な設計

依存: requests, beautifulsoup4, google-genai
"""

from __future__ import annotations

import json
import logging
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

# ── 定数 ──────────────────────────────────────────────────────────
_POLL_URL        = "https://trumpstruth.org/"
_HEADERS         = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}
_REQUEST_TIMEOUT  = 20          # seconds
_DEFAULT_INTERVAL = 300         # 5 minutes

# Gemini モデル（標準 generate_content 用）
_JUDGE_MODEL = "gemini-2.5-flash"

# Breaking 判定の importance_score 閾値
_BREAKING_THRESHOLD = 7.5

# state ファイル名（compe_M4/ 直下に保存）
_STATE_FILENAME = "trump_monitor_state.json"


# ══════════════════════════════════════════════════════════════════
# TrumpMonitor
# ══════════════════════════════════════════════════════════════════

class TrumpMonitor(threading.Thread):
    """
    バックグラウンドスレッドで trumpstruth.org を監視し、
    新着投稿を Gemini で判定して ArticleStore に投入する。

    Usage:
        monitor = TrumpMonitor(db_path=_DB_PATH, api_key=API_KEY, lang=_LANG)
        monitor.start()   # _main() 内で呼ぶ
        ...
        monitor.stop()    # finally ブロックで呼ぶ
    """

    def __init__(
        self,
        db_path: str,
        api_key: str,
        lang,                        # LanguageConfig (bcp47_code, prompt_lang)
        interval: int = _DEFAULT_INTERVAL,
        state_dir: Path | None = None,
    ):
        super().__init__(name="TrumpMonitor", daemon=True)
        self._db_path    = db_path
        self._api_key    = api_key
        self._lang       = lang
        self._interval   = interval
        self._stop_event = threading.Event()

        # state ファイル: compe_M4/ 直下
        _dir = state_dir or Path(__file__).resolve().parent.parent
        self._state_file = _dir / _STATE_FILENAME

        # ArticleStore は遅延 import（M3 が sys.path に入っている前提）
        self._store = None

    # ── 公開メソッド ──────────────────────────────────────────────

    def stop(self):
        """スレッドを停止する。"""
        self._stop_event.set()

    # ── スレッドメインループ ──────────────────────────────────────

    def run(self):
        logger.info("[TrumpMonitor] Started (interval=%ds)", self._interval)
        self._store = self._init_store()

        # 初回は即時実行
        self._tick()

        while not self._stop_event.wait(timeout=self._interval):
            self._tick()

        logger.info("[TrumpMonitor] Stopped")

    def _tick(self):
        """1回のポーリングサイクル。"""
        try:
            posts = self._fetch()
            if not posts:
                return

            last_id = self._load_last_id()
            new_posts = [p for p in posts if p["status_id"] > last_id]

            if not new_posts:
                logger.debug("[TrumpMonitor] No new posts (last_id=%d)", last_id)
                return

            logger.info("[TrumpMonitor] %d new post(s) found", len(new_posts))

            # 新着を古い順に処理
            for post in sorted(new_posts, key=lambda p: p["status_id"]):
                self._process(post)

            # 最大 ID を保存
            max_id = max(p["status_id"] for p in new_posts)
            self._save_last_id(max_id)

        except Exception as e:
            logger.error("[TrumpMonitor] _tick error: %s", e, exc_info=True)

    # ── スクレイピング ────────────────────────────────────────────

    def _fetch(self) -> list[dict]:
        """
        trumpstruth.org トップページをスクレイピングして投稿リストを返す。

        Returns:
            [{"status_id": int, "text": str, "url": str, "posted_at": str}, ...]
        """
        try:
            resp = requests.get(_POLL_URL, headers=_HEADERS, timeout=_REQUEST_TIMEOUT)
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.warning("[TrumpMonitor] Fetch failed: %s", e)
            return []

        soup = BeautifulSoup(resp.text, "html.parser")
        posts = []

        for div in soup.find_all("div", class_="status"):
            status_url = div.get("data-status-url", "")
            # status_id を URL の末尾数字から取得
            try:
                status_id = int(status_url.rstrip("/").split("/")[-1])
            except (ValueError, IndexError):
                continue

            # 本文テキスト
            content_div = div.find("div", class_="status__content")
            if not content_div:
                # ReTruth のみで本文なし → スキップ
                continue
            text = content_div.get_text(separator=" ", strip=True)
            if not text:
                continue

            # 投稿日時（meta-item の 2 番目 <a>）
            meta_items = div.find_all("a", class_="status-info__meta-item")
            posted_at = meta_items[1].get_text(strip=True) if len(meta_items) >= 2 else ""

            posts.append({
                "status_id": status_id,
                "text":      text,
                "url":       status_url,
                "posted_at": posted_at,
            })

        logger.debug("[TrumpMonitor] Fetched %d post(s) from page", len(posts))
        return posts

    # ── Gemini 判定 ───────────────────────────────────────────────

    def _judge(self, post: dict) -> dict | None:
        """
        Gemini で投稿の重要度を判定する。

        Returns:
            {
              "is_breaking": bool,
              "summary": str,           # _lang.prompt_lang で記述
              "importance_score": float,
              "topics": list[str],
            }
            判定失敗時は None
        """
        prompt = f"""You are a news importance classifier for a live broadcast system.

Analyze the following Truth Social post by Donald Trump and respond ONLY with a JSON object.
No preamble, no markdown fences.

Post:
\"\"\"
{post['text']}
\"\"\"

Respond with this JSON schema:
{{
  "is_breaking": <true if this is breaking/major news, false for rants/personal opinions/retweets>,
  "importance_score": <0.0 to 10.0, where 10 = world-changing event>,
  "summary": <one-sentence summary in {self._lang.prompt_lang}>,
  "topics": <list of English topic keywords, e.g. ["iran", "military", "oil"]>
}}

Guidelines for is_breaking = true:
- Policy announcements, military actions, diplomatic developments
- Economic measures with global impact
- Statements about ongoing conflicts or crises
- Score >= {_BREAKING_THRESHOLD}

Guidelines for is_breaking = false:
- Personal grievances, media criticism, general opinions
- Simple reposts or shares with no new information
- Routine political commentary
"""

        try:
            from google import genai as _genai  # noqa: PLC0415

            client = _genai.Client(api_key=self._api_key)
            response = client.models.generate_content(
                model=_JUDGE_MODEL,
                contents=prompt,
            )
            raw = response.text.strip()

            # ```json ... ``` フェンスが付いていれば除去
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
                raw = raw.strip()

            result = json.loads(raw)

            # importance_score が閾値以上なら is_breaking を強制 True に補正
            score = float(result.get("importance_score", 0.0))
            if score >= _BREAKING_THRESHOLD:
                result["is_breaking"] = True

            return result

        except Exception as e:
            logger.error("[TrumpMonitor] Gemini judge error: %s", e, exc_info=True)
            return None

    # ── ArticleStore への保存 ─────────────────────────────────────

    def _process(self, post: dict):
        """投稿を判定して必要なら ArticleStore に保存する。"""
        logger.info(
            "[TrumpMonitor] Judging post id=%d: %.60s...",
            post["status_id"], post["text"]
        )

        judgment = self._judge(post)
        if judgment is None:
            logger.warning("[TrumpMonitor] Judgment failed for id=%d", post["status_id"])
            return

        score       = float(judgment.get("importance_score", 0.0))
        is_breaking = bool(judgment.get("is_breaking", False))
        summary     = judgment.get("summary", post["text"][:100])
        topics      = judgment.get("topics", [])

        logger.info(
            "[TrumpMonitor] id=%d score=%.1f breaking=%s summary=%s",
            post["status_id"], score, is_breaking, summary
        )

        # 重要度が低すぎる投稿は保存しない
        if score < 3.0:
            logger.info("[TrumpMonitor] Skipped (score too low)")
            return

        if self._store is None:
            logger.warning("[TrumpMonitor] ArticleStore not available, skipping save")
            return

        try:
            now = datetime.now(timezone.utc).isoformat()
            row_id = self._store.save_article({
                "source_id":    "truthsocial",
                "source_name":  "Truth Social",
                "url":          post["url"],
                "title":        summary,
                "text_content": post["text"],
                "credibility":  7,
                "tier":         1,
                "is_top_page":  False,
                "crawled_at":   now,
            })

            if not row_id:
                logger.info(
                    "[TrumpMonitor] Post id=%d already exists in DB, skipping",
                    post["status_id"]
                )
                return

            self._store.update_analysis(row_id, {
                "summary":          summary,
                "importance_score": score,
                "topics":           topics,
            })

            if is_breaking:
                self._store.mark_breaking([row_id], True)
                logger.info(
                    "🚨 [TrumpMonitor] BREAKING saved: row_id=%d score=%.1f",
                    row_id, score
                )
            else:
                logger.info(
                    "[TrumpMonitor] Saved (not breaking): row_id=%d score=%.1f",
                    row_id, score
                )

        except Exception as e:
            logger.error("[TrumpMonitor] DB save error: %s", e, exc_info=True)

    # ── 状態ファイル ──────────────────────────────────────────────

    def _load_last_id(self) -> int:
        """最後に処理した status_id を読み込む。ファイルがなければ 0。"""
        try:
            if self._state_file.exists():
                data = json.loads(self._state_file.read_text(encoding="utf-8"))
                return int(data.get("last_status_id", 0))
        except Exception as e:
            logger.warning("[TrumpMonitor] Failed to load state: %s", e)
        return 0

    def _save_last_id(self, status_id: int):
        """最後に処理した status_id を保存する。"""
        try:
            self._state_file.write_text(
                json.dumps(
                    {
                        "last_status_id": status_id,
                        "updated_at":     datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("[TrumpMonitor] Failed to save state: %s", e)

    # ── ArticleStore 初期化 ───────────────────────────────────────

    def _init_store(self):
        """ArticleStore を初期化して返す。失敗時は None。"""
        try:
            from store.article_store import ArticleStore  # noqa: PLC0415
            store = ArticleStore(db_path=self._db_path)
            logger.info("[TrumpMonitor] ArticleStore initialized: %s", self._db_path)
            return store
        except Exception as e:
            logger.error("[TrumpMonitor] ArticleStore init failed: %s", e)
            return None

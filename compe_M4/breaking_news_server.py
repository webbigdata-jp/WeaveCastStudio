"""
compe_M4/breaking_news_server.py

OBS ブラウザソース向けの Breaking News ティッカー配信サーバ。

【機能】
  - ArticleStore を定期ポーリングし、今日のニュースと速報を取得
  - SSE (Server-Sent Events) で OBS ブラウザソースにリアルタイム配信
  - GET /overlay  → ティッカー表示用 HTML を返す
  - GET /events   → SSE ストリーム（ティッカー更新・速報検知）
  - POST /breaking → 手動での速報差し込み（将来拡張用）

【OBS 設定】
  ソース → ブラウザ を追加
  URL: http://localhost:8765/overlay
  幅: 1920  高さ: 1080

【依存】
  uv add aiohttp
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional

from aiohttp import web

logger = logging.getLogger(__name__)

# ── 設定 ────────────────────────────────────────────────────────
DEFAULT_PORT = 8765
POLL_INTERVAL_SEC = 30          # ArticleStore ポーリング間隔
TICKER_HEADLINE_MAX = 20        # ティッカーに表示する最大記事数
MIN_IMPORTANCE = 3.0            # ティッカーに載せる最低 importance_score

# overlay HTML のパス
_HERE = Path(__file__).resolve().parent
OVERLAY_HTML_PATH = _HERE / "overlay" / "ticker.html"


# ══════════════════════════════════════════════════════════════════
# TickerState: ポーリング結果を保持する共有状態
# ══════════════════════════════════════════════════════════════════

class TickerState:
    """
    ArticleStore のポーリング結果を保持し、
    SSE クライアントへの配信データを生成するクラス。
    """

    def __init__(self):
        self.headlines: list[dict] = []       # 通常ニュース一覧
        self.breaking: list[dict] = []        # 速報一覧
        self._last_breaking_ids: set = set()  # 前回ポーリング時の速報 ID
        self._version: int = 0                # 更新カウンタ
        self._event = asyncio.Event()         # 更新通知用

    def update(self, headlines: list[dict], breaking: list[dict]) -> bool:
        """
        ポーリング結果を反映する。変更があれば True を返す。

        Args:
            headlines: get_today_titles() の結果
            breaking: get_breaking() の結果
        Returns:
            bool: 内容に変更があった場合 True
        """
        new_breaking_ids = {a["id"] for a in breaking}
        headlines_changed = self._headlines_changed(headlines)
        breaking_changed = new_breaking_ids != self._last_breaking_ids

        if not headlines_changed and not breaking_changed:
            return False

        self.headlines = headlines
        self.breaking = breaking
        self._last_breaking_ids = new_breaking_ids
        self._version += 1
        self._event.set()
        self._event.clear()

        if breaking_changed and breaking:
            logger.info(
                f"[Ticker] 🚨 速報検知: {len(breaking)} 件 "
                f"(IDs: {new_breaking_ids})"
            )
        return True

    def inject_manual_breaking(self, headline: str, source: str = "MANUAL") -> None:
        """手動速報を差し込む（POST /breaking 用）。"""
        entry = {
            "id": f"manual_{int(time.time())}",
            "title": headline,
            "source_name": source,
            "importance_score": 10.0,
            "is_breaking": True,
        }
        self.breaking.insert(0, entry)
        self._version += 1
        self._event.set()
        self._event.clear()
        logger.info(f"[Ticker] 手動速報差し込み: {headline}")

    def to_sse_data(self) -> dict:
        """SSE 配信用の JSON データを生成する。"""
        return {
            "version": self._version,
            "has_breaking": len(self.breaking) > 0,
            "breaking": [
                {
                    "id": a["id"],
                    "title": a.get("title", ""),
                    "source": a.get("source_name", ""),
                    "score": a.get("importance_score", 0),
                }
                for a in self.breaking[:5]  # 速報は最大5件
            ],
            "headlines": [
                {
                    "id": a["id"],
                    "title": a.get("title", ""),
                    "source": a.get("source_name", ""),
                    "score": a.get("importance_score", 0),
                    "is_breaking": bool(a.get("is_breaking")),
                }
                for a in self.headlines[:TICKER_HEADLINE_MAX]
            ],
        }

    @property
    def version(self) -> int:
        return self._version

    async def wait_for_update(self, timeout: float = 30.0) -> bool:
        """更新が来るか timeout まで待つ。更新があれば True。"""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _headlines_changed(self, new_headlines: list[dict]) -> bool:
        """ヘッドラインリストに変更があったか簡易チェック。"""
        if len(new_headlines) != len(self.headlines):
            return True
        new_ids = [a["id"] for a in new_headlines]
        old_ids = [a["id"] for a in self.headlines]
        return new_ids != old_ids


# ══════════════════════════════════════════════════════════════════
# ArticleStore ポーリングタスク
# ══════════════════════════════════════════════════════════════════

async def poll_article_store(
    state: TickerState,
    db_path: str,
    interval: float = POLL_INTERVAL_SEC,
):
    """
    ArticleStore を定期ポーリングし、TickerState を更新する。

    Args:
        state: 共有 TickerState
        db_path: ArticleStore の DB パス
        interval: ポーリング間隔（秒）
    """
    # import は遅延（起動時に sys.path が設定済みである前提）
    from store.article_store import ArticleStore

    logger.info(
        f"[Ticker] ArticleStore ポーリング開始 "
        f"(interval={interval}s, db={db_path})"
    )

    while True:
        try:
            store = ArticleStore(db_path=db_path)
            headlines = store.get_today_titles(min_importance=MIN_IMPORTANCE)
            breaking = store.get_breaking()
            changed = state.update(headlines, breaking)
            if changed:
                logger.debug(
                    f"[Ticker] 更新: headlines={len(headlines)}, "
                    f"breaking={len(breaking)}"
                )
        except Exception as e:
            logger.error(f"[Ticker] ポーリングエラー: {e}", exc_info=True)

        await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════
# HTTP ハンドラ
# ══════════════════════════════════════════════════════════════════

async def handle_overlay(request: web.Request) -> web.Response:
    """GET /overlay — ティッカー HTML を返す。"""
    html_path = request.app["overlay_html_path"]
    if not html_path.exists():
        return web.Response(
            status=404,
            text=f"overlay HTML not found: {html_path}",
        )
    html = html_path.read_text(encoding="utf-8")
    return web.Response(text=html, content_type="text/html")


async def handle_events(request: web.Request) -> web.StreamResponse:
    """
    GET /events — SSE ストリーム。
    TickerState が更新されるたびにイベントを送信する。
    接続維持のため、更新がなくても30秒ごとに heartbeat を送る。
    """
    state: TickerState = request.app["ticker_state"]

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["Access-Control-Allow-Origin"] = "*"
    await response.prepare(request)

    logger.info("[SSE] クライアント接続")

    # 初回データ送信
    data = json.dumps(state.to_sse_data(), ensure_ascii=False)
    await response.write(f"event: ticker\ndata: {data}\n\n".encode("utf-8"))

    last_version = state.version
    try:
        while True:
            updated = await state.wait_for_update(timeout=30.0)
            if updated or state.version != last_version:
                data = json.dumps(state.to_sse_data(), ensure_ascii=False)
                event_type = "breaking" if state.breaking else "ticker"
                await response.write(
                    f"event: {event_type}\ndata: {data}\n\n".encode("utf-8")
                )
                last_version = state.version
            else:
                # heartbeat
                await response.write(b": heartbeat\n\n")
    except (ConnectionResetError, ConnectionAbortedError):
        logger.info("[SSE] クライアント切断")
    except asyncio.CancelledError:
        logger.info("[SSE] SSE タスクキャンセル")

    return response


async def handle_post_breaking(request: web.Request) -> web.Response:
    """
    POST /breaking — 手動速報差し込み。

    Body JSON:
        {"headline": "速報テキスト", "source": "MANUAL"}
    """
    state: TickerState = request.app["ticker_state"]
    try:
        body = await request.json()
    except Exception:
        return web.json_response({"error": "invalid JSON"}, status=400)

    headline = body.get("headline", "").strip()
    if not headline:
        return web.json_response({"error": "headline is required"}, status=400)

    source = body.get("source", "MANUAL")
    state.inject_manual_breaking(headline, source)

    return web.json_response({"result": "ok", "headline": headline})


async def handle_status(request: web.Request) -> web.Response:
    """GET /status — 現在のティッカー状態を返す（デバッグ用）。"""
    state: TickerState = request.app["ticker_state"]
    return web.json_response(state.to_sse_data())


# ══════════════════════════════════════════════════════════════════
# サーバ起動
# ══════════════════════════════════════════════════════════════════

def create_app(
    ticker_state: TickerState,
    overlay_html_path: Optional[Path] = None,
) -> web.Application:
    """
    aiohttp Application を生成する。

    Args:
        ticker_state: 共有 TickerState
        overlay_html_path: ticker.html のパス（None でデフォルト）
    Returns:
        web.Application
    """
    app = web.Application()
    app["ticker_state"] = ticker_state
    app["overlay_html_path"] = overlay_html_path or OVERLAY_HTML_PATH

    app.router.add_get("/overlay", handle_overlay)
    app.router.add_get("/events", handle_events)
    app.router.add_post("/breaking", handle_post_breaking)
    app.router.add_get("/status", handle_status)

    return app


async def start_server(
    ticker_state: TickerState,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    overlay_html_path: Optional[Path] = None,
) -> web.AppRunner:
    """
    サーバを非ブロッキングで起動する。
    gemini_live_client.py から呼び出す用。

    Args:
        ticker_state: 共有 TickerState
        host: バインドアドレス
        port: ポート番号
        overlay_html_path: ticker.html のパス
    Returns:
        web.AppRunner（停止時に cleanup() を呼ぶ）
    """
    app = create_app(ticker_state, overlay_html_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"[Ticker] HTTP サーバ起動: http://{host}:{port}/overlay")
    return runner


# ══════════════════════════════════════════════════════════════════
# スタンドアロン起動（テスト用）
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # コマンドライン引数で DB パスを受け取る
    if len(sys.argv) >= 2:
        db_path = sys.argv[1]
    else:
        # デフォルト: compe_M3/data/articles.db
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
        db_path = str(_PROJECT_ROOT / "compe_M3" / "data" / "articles.db")

    print(f"DB: {db_path}")
    print(f"Overlay: http://127.0.0.1:{DEFAULT_PORT}/overlay")
    print(f"SSE:     http://127.0.0.1:{DEFAULT_PORT}/events")
    print(f"Status:  http://127.0.0.1:{DEFAULT_PORT}/status")

    state = TickerState()

    async def _run():
        runner = await start_server(state)
        poll_task = asyncio.create_task(
            poll_article_store(state, db_path)
        )
        try:
            # 永続実行
            await asyncio.Event().wait()
        finally:
            poll_task.cancel()
            await runner.cleanup()

    asyncio.run(_run())

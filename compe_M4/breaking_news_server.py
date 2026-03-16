"""
compe_M4/breaking_news_server.py

Breaking news ticker delivery server for OBS browser sources.

Features:
  - Polls ArticleStore periodically to fetch today's news and breaking items
  - Delivers real-time updates to OBS browser sources via SSE (Server-Sent Events)
  - GET /overlay  -> Returns the ticker display HTML
  - GET /events   -> SSE stream (ticker updates and breaking news detection)
  - POST /breaking -> Manual breaking-news injection (reserved for future use)

OBS setup:
  Sources -> Add Browser source
  URL: http://localhost:8765/overlay
  Width: 1920  Height: 1080

Dependencies:
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

# ── Configuration ────────────────────────────────────────────────────────
DEFAULT_PORT = 8765
POLL_INTERVAL_SEC = 30          # ArticleStore polling interval
TICKER_HEADLINE_MAX = 20        # Maximum number of articles shown in the ticker
MIN_IMPORTANCE = 3.0            # Minimum importance_score to include in the ticker

# Path to the overlay HTML
_HERE = Path(__file__).resolve().parent
OVERLAY_HTML_PATH = _HERE / "overlay" / "ticker.html"


# ══════════════════════════════════════════════════════════════════
# TickerState: holds polling results as shared state
# ══════════════════════════════════════════════════════════════════

class TickerState:
    """
    Holds ArticleStore polling results and generates data
    for delivery to SSE clients.
    """

    def __init__(self):
        self.headlines: list[dict] = []       # Regular news list
        self.breaking: list[dict] = []        # Breaking news list
        self._last_breaking_ids: set = set()  # Breaking news IDs from the previous poll
        self._version: int = 0                # Update counter
        self._event = asyncio.Event()         # Update notification event

    def update(self, headlines: list[dict], breaking: list[dict]) -> bool:
        """
        Apply polling results. Returns True if anything changed.

        Args:
            headlines: result of get_today_titles()
            breaking:  result of get_breaking()
        Returns:
            bool: True if the content has changed
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
                f"[Ticker] Breaking news detected: {len(breaking)} item(s) "
                f"(IDs: {new_breaking_ids})"
            )
        return True

    def inject_manual_breaking(self, headline: str, source: str = "MANUAL") -> None:
        """Inject a manual breaking news item (used by POST /breaking)."""
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
        logger.info(f"[Ticker] Manual breaking news injected: {headline}")

    def to_sse_data(self) -> dict:
        """Generate JSON data for SSE delivery."""
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
                for a in self.breaking[:5]  # Maximum 5 breaking items
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
        """Wait up to timeout for an update. Returns True if an update arrived."""
        try:
            await asyncio.wait_for(self._event.wait(), timeout=timeout)
            return True
        except asyncio.TimeoutError:
            return False

    def _headlines_changed(self, new_headlines: list[dict]) -> bool:
        """Quick check for changes in the headline list."""
        if len(new_headlines) != len(self.headlines):
            return True
        for new, old in zip(new_headlines, self.headlines):
            if new["id"] != old["id"]:
                return True
            if bool(new.get("is_breaking")) != bool(old.get("is_breaking")):
                return True
        return False


# ══════════════════════════════════════════════════════════════════
# ArticleStore polling task
# ══════════════════════════════════════════════════════════════════

async def poll_article_store(
    state: TickerState,
    db_path: str,
    interval: float = POLL_INTERVAL_SEC,
):
    """
    Poll ArticleStore periodically and update TickerState.

    Args:
        state:    shared TickerState
        db_path:  path to the ArticleStore database
        interval: polling interval in seconds
    """
    # Deferred import (assumes sys.path is configured at startup)
    from store.article_store import ArticleStore

    logger.info(
        f"[Ticker] ArticleStore polling started "
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
                    f"[Ticker] Updated: headlines={len(headlines)}, "
                    f"breaking={len(breaking)}"
                )
        except Exception as e:
            logger.error(f"[Ticker] Polling error: {e}", exc_info=True)

        await asyncio.sleep(interval)


# ══════════════════════════════════════════════════════════════════
# HTTP handlers
# ══════════════════════════════════════════════════════════════════

async def handle_overlay(request: web.Request) -> web.Response:
    """GET /overlay — Return the ticker HTML."""
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
    GET /events — SSE stream.
    Sends an event whenever TickerState is updated.
    Sends a heartbeat every 30 seconds to keep the connection alive.
    """
    state: TickerState = request.app["ticker_state"]

    response = web.StreamResponse()
    response.headers["Content-Type"] = "text/event-stream"
    response.headers["Cache-Control"] = "no-cache"
    response.headers["Connection"] = "keep-alive"
    response.headers["Access-Control-Allow-Origin"] = "*"
    await response.prepare(request)

    logger.info("[SSE] Client connected")

    # Send initial data
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
                # Heartbeat
                await response.write(b": heartbeat\n\n")
    except (ConnectionResetError, ConnectionAbortedError):
        logger.info("[SSE] Client disconnected")
    except asyncio.CancelledError:
        logger.info("[SSE] SSE task cancelled")

    return response


async def handle_post_breaking(request: web.Request) -> web.Response:
    """
    POST /breaking — Inject a manual breaking news item.

    Request body (JSON):
        {"headline": "Breaking news text", "source": "MANUAL"}
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
    """GET /status — Return the current ticker state (for debugging)."""
    state: TickerState = request.app["ticker_state"]
    return web.json_response(state.to_sse_data())


# ══════════════════════════════════════════════════════════════════
# Server startup
# ══════════════════════════════════════════════════════════════════

def create_app(
    ticker_state: TickerState,
    overlay_html_path: Optional[Path] = None,
) -> web.Application:
    """
    Create the aiohttp Application.

    Args:
        ticker_state:      shared TickerState
        overlay_html_path: path to ticker.html (uses default if None)
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
    Start the server in non-blocking mode.
    Called from gemini_live_client.py.

    Args:
        ticker_state:      shared TickerState
        host:              bind address
        port:              port number
        overlay_html_path: path to ticker.html
    Returns:
        web.AppRunner (call cleanup() to stop)
    """
    app = create_app(ticker_state, overlay_html_path)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"[Ticker] HTTP server started: http://{host}:{port}/overlay")
    return runner


# ══════════════════════════════════════════════════════════════════
# Standalone entry point (for testing)
# ══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    import sys

    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    # Accept DB path as a command-line argument
    if len(sys.argv) >= 2:
        db_path = sys.argv[1]
    else:
        # Default: compe_M3/data/articles.db
        _PROJECT_ROOT = Path(__file__).resolve().parent.parent
        db_path = str(_PROJECT_ROOT / "compe_M3" / "data" / "articles.db")

    print(f"DB:      {db_path}")
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
            # Run indefinitely
            await asyncio.Event().wait()
        finally:
            poll_task.cancel()
            await runner.cleanup()

    asyncio.run(_run())

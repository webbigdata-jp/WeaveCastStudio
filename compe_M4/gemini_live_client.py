"""
compe_M4/gemini_live_client.py

Live broadcast client that receives voice commands from a journalist via the
Gemini Live API and controls MediaWindow through Function Calling.

PTT mode:
  Microphone audio is sent to Gemini only while F9 is held down.
  Releasing F9 signals end-of-speech to Gemini.
  Barge-in (interrupting Gemini mid-response) is supported via F9.

Keyboard shortcuts:
  F5: Play / pause toggle
  F6: Stop
  F7: Minimize media window
  F8: Restore media window
  F9: Push-to-talk (hold and speak)

Content selection strategy:
  At startup, both ArticleStore.get_today_titles() and the ContentIndex video
  list are injected into system_instruction.
  For natural-language requests such as "show a report about the UN", Gemini
  selects the most semantically relevant content_id and calls play_video itself.
  store.search() is provided as a supplementary tool, used only when Gemini
  decides it is necessary.

Dependencies:
    uv add pyaudio google-genai keyboard python-dotenv python-vlc Pillow

Usage:
    python gemini_live_client.py
"""

import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import keyboard as kb
import pyaudio
from google import genai
from google.genai import types
from dotenv import load_dotenv

from breaking_news_server import (
    TickerState,
    start_server as start_ticker_server,
    poll_article_store,
    DEFAULT_PORT as TICKER_PORT,
)
from monitor.trump_monitor import TrumpMonitor


# ── Add project root to sys.path ──────────────────────────────────
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from media_window import (                             # noqa: E402
    MediaWindow,
    ImageAssetManager,
    register_hotkeys,
    resolve_content_path,
)
from content_index import ContentIndexManager          # noqa: E402
from shared.language_utils import get_language_config  # noqa: E402

# ArticleStore lives under compe_M3/, so add it to sys.path
_M3_ROOT = _PROJECT_ROOT / "compe_M3"
if str(_M3_ROOT) not in sys.path:
    sys.path.insert(0, str(_M3_ROOT))

# ── Logging ──────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# Import ArticleStore (continue startup even if unavailable)
try:
    from store.article_store import ArticleStore
    _ARTICLE_STORE_AVAILABLE = True
except ImportError:
    logger.warning("ArticleStore could not be imported. Starting without article list.")
    _ARTICLE_STORE_AVAILABLE = False

# ── Environment variables ─────────────────────────────────────────────
load_dotenv(_PROJECT_ROOT / ".env")
API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY is not set. "
        "Please check WeaveCastStudio/.env."
    )

# ── Language configuration (reads LANGUAGE from .env) ────────────────
_LANG = get_language_config()
logger.info(f"Language: {_LANG.bcp47_code} ({_LANG.prompt_lang})")

# ── Gemini model ─────────────────────────────────────────────────────
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# ── Audio settings (fixed to Live API specification) ─────────────────
FORMAT       = pyaudio.paInt16
CHANNELS     = 1
SEND_RATE    = 16000   # Microphone input: 16 kHz
RECEIVE_RATE = 24000   # Gemini output: 24 kHz
CHUNK_SIZE   = 1024

# ── PTT key ──────────────────────────────────────────────────────────
PTT_KEY = "f9"

# ── ArticleStore DB path ──────────────────────────────────────────────
_DB_PATH = str(_M3_ROOT / "data" / "articles.db")


# ══════════════════════════════════════════════════════════════════
# Startup data loading
# ══════════════════════════════════════════════════════════════════

def load_today_titles() -> list[dict]:
    """Load today's article title list from ArticleStore."""
    if not _ARTICLE_STORE_AVAILABLE:
        return []
    try:
        store = ArticleStore(db_path=_DB_PATH)
        titles = store.get_today_titles(min_importance=3.0)
        logger.info(f"ArticleStore: loaded {len(titles)} article title(s)")
        return titles
    except Exception as e:
        logger.warning(f"Failed to fetch from ArticleStore: {e}")
        return []


def load_content_list() -> list[dict]:
    """
    Load the list of playable content from ContentIndex.
    Includes both videos and screenshots (still images).
    Returns:
        [{content_id, title, type, score, topic_tags, path, media_type}, ...]
    """
    mgr = ContentIndexManager()
    all_entries = mgr.get_all(sort_by_importance=True)

    content_list = []
    for entry in all_entries:
        resolved = resolve_content_path(entry)
        if not resolved:
            continue

        # Determine media type from file extension
        ext = Path(resolved).suffix.lower()
        if ext in (".mp4", ".avi", ".mkv", ".mov", ".webm"):
            media_type = "video"
        elif ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp"):
            media_type = "image"
        else:
            media_type = "video"  # fallback

        content_list.append({
            "content_id": entry["id"],
            "title": entry.get("title", "(no title)"),
            "type": entry.get("type", "?"),
            "score": entry.get("importance_score") or 0.0,
            "topic_tags": entry.get("topic_tags") or [],
            "path": resolved,
            "media_type": media_type,
        })

    logger.info(f"ContentIndex: loaded {len(content_list)} content item(s)")
    return content_list


# ══════════════════════════════════════════════════════════════════
# System instruction builder
# ══════════════════════════════════════════════════════════════════

def _build_system_instruction(
    today_titles: list[dict],
    content_list: list[dict],
    image_assets: list[dict],
    prompt_lang: str,
) -> str:
    # ── Today's article list (up to 30 items) ──
    if today_titles:
        articles_lines = []
        for a in today_titles[:30]:
            breaking = " [BREAKING]" if a.get("is_breaking") else ""
            topics_str = ""
            try:
                t = json.loads(a.get("topics") or "[]")
                if t:
                    topics_str = f" topics={t}"
            except Exception:
                pass
            articles_lines.append(
                f"  - [article id={a['id']}] {a.get('title', '')}{breaking}"
                f" (score={a.get('importance_score', '?')}"
                f", src={a.get('source_name', '')}){topics_str}"
            )
        articles_section = "[TODAY'S TOP ARTICLES]\n" + "\n".join(articles_lines)
    else:
        articles_section = "[TODAY'S TOP ARTICLES]\n  (no data)"

    # ── Playable content list ──
    if content_list:
        videos_lines = []
        for v in content_list:
            tags = ", ".join(v["topic_tags"]) if v["topic_tags"] else "-"
            videos_lines.append(
                f"  - [content_id={v['content_id']}] [{v['type']}] {v['title']}"
                f" (score={v['score']}, tags={tags}, media={v.get('media_type', '?')})"
            )
        videos_section = "[PLAYABLE VIDEOS / CLIPS]\n" + "\n".join(videos_lines)
    else:
        videos_section = "[PLAYABLE VIDEOS / CLIPS]\n  (none)"

    # ── Still-image asset list ──
    if image_assets:
        images_lines = []
        for a in image_assets:
            tags = ", ".join(a.get("topic_tags", []))
            images_lines.append(
                f"  - [image_id={a['id']}] {a['title']} (tags={tags})"
            )
        images_section = "[AVAILABLE STILL-IMAGE ASSETS]\n" + "\n".join(images_lines)
    else:
        images_section = "[AVAILABLE STILL-IMAGE ASSETS]\n  (none)"

    return f"""You are the AI news broadcast director for WeaveCastStudio.
A journalist is giving you voice commands during a live OBS stream.
Call the appropriate tools to play videos, stop playback, display still images,
and manage the media window as instructed.

Always respond in {prompt_lang}.

{articles_section}

{videos_section}

{images_section}

[VIDEO SELECTION RULES]
- When the journalist says something like "play a video about X" or "show a report on X",
  select the most relevant content_id from the video list above and call play_video.
- Use the topics and title fields in the article list to find the best match.
- When multiple videos match, prefer the one with the higher score.
- If no suitable video is found in the list, run search_articles first, then decide.

[STILL-IMAGE DISPLAY RULES]
- For instructions such as "show the map of the Strait of Hormuz" or "show the Iran map",
  use show_image.
- Specify the asset using image_id.
- Direct specification via file_path is also supported.
- If a still image is requested while a video is playing, stop the video and switch to the image.

[RESPONSE RULES]
- Keep responses concise; the journalist is live on air.
- When calling a tool, briefly announce what you are doing
  (e.g. "Playing the video.", "Displaying the map.").
- No unnecessary preamble.
- A news ticker is always scrolling at the bottom of the screen.
- Articles with is_breaking=True in ArticleStore are highlighted in red on the ticker.
- If the journalist asks "What is the breaking news?" or similar,
  search for is_breaking articles with search_articles, explain verbally,
  and display any related screenshots or videos with show_image / play_video.
"""


# ══════════════════════════════════════════════════════════════════
# Tool definitions
# ══════════════════════════════════════════════════════════════════

def _make_tools() -> list[dict]:
    return[
        {
            "function_declarations":[
                {
                    "name": "play_video",
                    "description": (
                        "Play the video identified by the given content_id. "
                        "Select the most appropriate content_id from the video list "
                        "in system_instruction and call this function."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content_id": {
                                "type": "string",
                                "description": "content_id of the video to play (choose from the video list)",
                            }
                        },
                        "required": ["content_id"],
                    },
                },
                {
                    "name": "stop_video",
                    "description": "Stop the currently playing video or still image and minimize the window.",
                },
                {
                    "name": "pause_video",
                    "description": "Pause the currently playing video.",
                },
                {
                    "name": "resume_video",
                    "description": "Resume a paused video.",
                },
                {
                    "name": "show_image",
                    "description": (
                        "Display a still image in the media window. "
                        "Specify the asset by image_id (from the still-image asset list) "
                        "or by file_path (absolute file path). "
                        "If a video is playing, it will be stopped automatically."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_id": {
                                "type": "string",
                                "description": "ID of the still-image asset (e.g. hormuz_map). Provide either image_id or file_path.",
                            },
                            "file_path": {
                                "type": "string",
                                "description": "Absolute path to the image file. Used when image_id is not specified.",
                            },
                        },
                    },
                },
                {
                    "name": "minimize_window",
                    "description": "Move the media window off-screen (minimize).",
                },
                {
                    "name": "restore_window",
                    "description": "Restore the minimized media window.",
                },
                {
                    "name": "search_articles",
                    "description": (
                        "Search ArticleStore by keyword and return matching articles. "
                        "Use as a fallback when no suitable video is found in the list."
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "Search keyword (English recommended: 'UN', 'Iran', etc.)",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "Maximum number of results to return (default 5)",
                            },
                        },
                        "required":["query"],
                    },
                },
                {
                    "name": "list_videos",
                    "description": "Read out the list of currently playable videos and clips.",
                },
            ]
        }
    ]


# ══════════════════════════════════════════════════════════════════
# ToolExecutor
# ══════════════════════════════════════════════════════════════════

class ToolExecutor:
    def __init__(
        self,
        window: MediaWindow,
        content_list: list[dict],
        image_asset_mgr: ImageAssetManager,
    ):
        self._window = window
        self._content_map: dict[str, dict] = {
            v["content_id"]: v for v in content_list
        }
        self._content_list = content_list
        self._image_assets = image_asset_mgr

    def execute(self, name: str, args: dict) -> dict:
        logger.info(f"[ToolCall] {name}({args})")
        try:
            if name == "play_video":
                return self._play_video(args.get("content_id", ""))
            elif name == "stop_video":
                self._window.stop()
                return {"result": "Stopped"}
            elif name == "pause_video":
                self._window.pause()
                return {"result": "Paused"}
            elif name == "resume_video":
                self._window.resume()
                return {"result": "Resumed"}
            elif name == "show_image":
                return self._show_image(
                    args.get("image_id", ""),
                    args.get("file_path", ""),
                )
            elif name == "minimize_window":
                self._window.minimize()
                return {"result": "Window minimized"}
            elif name == "restore_window":
                self._window.restore()
                return {"result": "Window restored"}
            elif name == "search_articles":
                return self._search_articles(
                    args.get("query", ""),
                    int(args.get("limit", 5)),
                )
            elif name == "list_videos":
                return self._list_videos()
            else:
                return {"error": f"Unknown tool: {name}"}
        except Exception as e:
            logger.error(f"[ToolCall] {name} execution error: {e}", exc_info=True)
            return {"error": str(e)}

    def _play_video(self, content_id: str) -> dict:
        if not content_id:
            return {"error": "content_id is required"}
        entry = self._content_map.get(content_id)
        if entry is None:
            return {"error": f"content_id '{content_id}' not found"}

        path = entry["path"]
        media_type = entry.get("media_type", "video")

        if media_type == "image":
            ok = self._window.show_image(path)
        else:
            if self._window.is_playing():
                ok = self._window.swap_video(path)
            else:
                ok = self._window.play_video(path)

        if ok:
            return {"result": f"Now showing: {entry['title']}"}
        return {"error": f"Playback failed: {content_id}"}

    def _show_image(self, image_id: str, file_path: str) -> dict:
        if image_id:
            path = self._image_assets.get_path(image_id)
            if not path:
                return {"error": f"Still-image asset '{image_id}' not found"}
            asset = self._image_assets.get(image_id)
            title = asset.get("title", image_id) if asset else image_id
        elif file_path:
            path = file_path
            title = Path(file_path).name
        else:
            return {"error": "Specify either image_id or file_path"}

        ok = self._window.show_image(path)
        if ok:
            return {"result": f"Now showing: {title}"}
        return {"error": f"Failed to display image: {path}"}

    def _search_articles(self, query: str, limit: int = 5) -> dict:
        if not _ARTICLE_STORE_AVAILABLE:
            return {"error": "ArticleStore is not available"}
        try:
            store = ArticleStore(db_path=_DB_PATH)
            results = store.search(query, min_importance=3.0, limit=limit)
            if not results:
                return {"result": f"No articles found for '{query}'"}
            lines = [
                f"id={r['id']}: {r.get('title', '')} "
                f"(score={r.get('importance_score', '?')}, src={r.get('source_name', '')})"
                for r in results
            ]
            return {"result": "\n".join(lines)}
        except Exception as e:
            return {"error": str(e)}

    def _list_videos(self) -> dict:
        if not self._content_list:
            return {"result": "No playable videos available"}
        lines = [
            f"{v['content_id']}: [{v['type']}] {v['title']} (score={v['score']})"
            for v in self._content_list
        ]
        return {"result": "\n".join(lines)}


# ══════════════════════════════════════════════════════════════════
# GeminiLiveClient (PTT + content search + still-image support)
# ══════════════════════════════════════════════════════════════════

class GeminiLiveClient:
    def __init__(
        self,
        window: MediaWindow,
        content_list: list[dict],
        today_titles: list[dict],
        image_asset_mgr: ImageAssetManager,
    ):
        self._window = window
        self._content_list = content_list
        self._today_titles = today_titles
        self._image_asset_mgr = image_asset_mgr
        self._executor = ToolExecutor(window, content_list, image_asset_mgr)

        self._client = genai.Client(api_key=API_KEY)
        self._pya = pyaudio.PyAudio()
        self._audio_out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
        self._session = None
        self._running = False

        # ── Session reconnection ──
        self._resumption_handle: str | None = None   # Session Resumption handle
        self._session_lost = asyncio.Event()          # Signals session disconnect
        self._max_reconnect_attempts = 5
        self._reconnecting = False                    # True while reconnecting

    def _build_config(self) -> dict:
        return {
            "response_modalities": ["AUDIO"],
            "tools": _make_tools(),
            "system_instruction": _build_system_instruction(
                self._today_titles,
                self._content_list,
                self._image_asset_mgr.list_all(),
                _LANG.prompt_lang,
            ),
            "input_audio_transcription": {},
            "output_audio_transcription": {},
            # ── Session persistence ──
            "session_resumption": types.SessionResumptionConfig(
                handle=self._resumption_handle,
            ),
            "context_window_compression": types.ContextWindowCompressionConfig(
                sliding_window=types.SlidingWindow(),
            ),
            "speech_config": types.SpeechConfig(),
        }

    async def _task_ptt_mic(self):
            mic_info = self._pya.get_default_input_device_info()
            logger.info(f"Microphone: {mic_info['name']}")
            stream = await asyncio.to_thread(
                self._pya.open,
                format=FORMAT, channels=CHANNELS, rate=SEND_RATE,
                input=True, input_device_index=int(mic_info["index"]),
                frames_per_buffer=CHUNK_SIZE,
            )
            was_pressed = False
            silence_chunks_left = 0

            try:
                while self._running:
                    data = await asyncio.to_thread(stream.read, CHUNK_SIZE, False)
                    is_pressed = kb.is_pressed(PTT_KEY)

                    if is_pressed and not was_pressed:
                        logger.info(f"[PTT] Start ({PTT_KEY.upper()} pressed)")
                        print(f"\n🎙  Speak now... (hold {PTT_KEY.upper()})")
                        silence_chunks_left = 0

                    elif was_pressed and not is_pressed:
                        logger.info("[PTT] End (audio send stopped)")
                        print("⏹  Stopped. Gemini is processing...\n")
                        silence_chunks_left = 30

                    # Do not send while session is disconnected
                    if self._session and not self._reconnecting:
                        if is_pressed:
                            try:
                                await self._session.send_realtime_input(
                                    audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_RATE}")
                                )
                            except Exception:
                                pass  # Ignore send errors immediately after disconnect
                        elif silence_chunks_left > 0:
                            try:
                                silence_data = b'\x00' * len(data)
                                await self._session.send_realtime_input(
                                    audio=types.Blob(data=silence_data, mime_type=f"audio/pcm;rate={SEND_RATE}")
                                )
                            except Exception:
                                pass
                            silence_chunks_left -= 1

                    was_pressed = is_pressed
            finally:
                stream.stop_stream()
                stream.close()

    async def _task_receive(self):
        # receive() ends iteration on turn_complete, so we must call
        # receive() again in a while loop to continue receiving.
        # (ref: https://github.com/googleapis/python-genai/issues/1224)
        while self._running:
            try:
                async for response in self._session.receive():
                    # ── Store Session Resumption handle ──
                    if response.session_resumption_update:
                        update = response.session_resumption_update
                        if update.resumable and update.new_handle:
                            self._resumption_handle = update.new_handle
                            logger.debug(
                                f"[Session] resumption handle updated: "
                                f"{self._resumption_handle[:20]}..."
                            )
                    # ── GoAway message (disconnection imminent) ──
                    if response.go_away is not None:
                        logger.warning(
                            f"[Session] GoAway received — time left: "
                            f"{response.go_away.time_left}"
                        )
                        print("⚠️  Server will disconnect shortly. Reconnecting automatically...")
                    if response.data is not None:
                        await self._audio_out_queue.put(response.data)
                    if response.text:
                        logger.debug(f"[Gemini TEXT] {response.text}")
                    if response.server_content and response.server_content.input_transcription:
                        t = response.server_content.input_transcription.text
                        if t:
                            print(f"📝 [You]     {t}")
                    if response.server_content and response.server_content.output_transcription:
                        t = response.server_content.output_transcription.text
                        if t:
                            print(f"🤖 [Gemini] {t}")
                    if response.server_content and response.server_content.turn_complete:
                        await self._audio_out_queue.put(None)
                        print(f"─── (Hold {PTT_KEY.upper()} to speak) ───\n")
                    if response.tool_call:
                        asyncio.create_task(self._handle_tool_call(response.tool_call))
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"[RECV] Exception in receive(): {type(e).__name__}: {e}")
                # Notify session loss and exit receive loop
                self._session_lost.set()
                return  # Reconnection is handled in run()

    async def _handle_tool_call(self, tool_call):
            try:
                function_responses = []
                for fc in tool_call.function_calls:
                    args = dict(fc.args) if fc.args else {}
                    result = await asyncio.to_thread(self._executor.execute, fc.name, args)
                    function_responses.append(
                        types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                    )
                    logger.info(f"[ToolResult] {fc.name} -> {result}")

                if hasattr(self._session, "send_tool_response"):
                    await self._session.send_tool_response(function_responses=function_responses)
                elif hasattr(self._session, "send"):
                    await self._session.send(input={"function_responses": function_responses})
                else:
                    logger.error("No method found on session to send tool response.")

            except Exception as e:
                logger.error(f"[_handle_tool_call] Error sending tool response: {e}", exc_info=True)

    async def _task_play_audio(self):
        stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT, channels=CHANNELS, rate=RECEIVE_RATE,
            output=True, frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while self._running:
                chunk = await self._audio_out_queue.get()
                if chunk is None:
                    continue
                await asyncio.to_thread(stream.write, chunk)
        finally:
            stream.stop_stream()
            stream.close()

    async def run(self):
        self._running = True

        # ── Initial connection / info display ──
        print("\n" + "═" * 55)
        print("  WeaveCastStudio M4 - Gemini Live PTT Mode")
        print("═" * 55)
        print(f"  Articles: {len(self._today_titles)}  /  Videos: {len(self._content_list)}")
        print(f"  Still-image assets: {len(self._image_asset_mgr.list_all())}")
        print(f"  Language: {_LANG.bcp47_code} ({_LANG.prompt_lang})")
        print(f"  Hold {PTT_KEY.upper()} and speak")
        print("  F5=play/pause  F6=stop  F7=minimize  F8=restore")
        print("  Ctrl+C to exit")
        print("═" * 55)
        if self._content_list:
            print("\n[PLAYABLE CONTENT]")
            for v in self._content_list:
                tags = ", ".join(v["topic_tags"]) if v["topic_tags"] else "-"
                mt = v.get("media_type", "?")
                print(
                    f"  [{v['content_id']}] [{v['type']}|{mt}] {v['title']}"
                    f" (score={v['score']:.1f}, tags={tags})"
                )
        else:
            print("\n[PLAYABLE CONTENT]\n  (none)")

        image_assets = self._image_asset_mgr.list_all()
        if image_assets:
            print("\n[STILL-IMAGE ASSETS]")
            for a in image_assets:
                tags = ", ".join(a.get("topic_tags", []))
                print(f"  [{a['id']}] {a['title']} (tags={tags})")
        print()

        # ── mic / speaker tasks (persist across sessions) ──
        mic_task = asyncio.create_task(self._task_ptt_mic())
        speaker_task = asyncio.create_task(self._task_play_audio())

        consecutive_failures = 0

        try:
            while self._running:
                # ── Connect session ──
                config = self._build_config()
                handle_info = (
                    " (resumption handle present)"
                    if self._resumption_handle else " (new session)"
                )
                logger.info(
                    f"Connecting to Gemini Live API... (model={MODEL}){handle_info}"
                )
                if consecutive_failures > 0:
                    print(
                        f"🔄 Reconnecting... "
                        f"(attempt {consecutive_failures}/{self._max_reconnect_attempts})"
                    )

                try:
                    async with self._client.aio.live.connect(
                        model=MODEL, config=config,
                    ) as session:
                        self._session = session
                        self._reconnecting = False
                        self._session_lost.clear()
                        consecutive_failures = 0  # Reset on successful connection

                        if self._resumption_handle:
                            print("✅ Session reconnected (conversation context restored)")
                        else:
                            print("✅ Session connected")
                        print(f"─── (Hold {PTT_KEY.upper()} to speak) ───\n")

                        # Launch receive task and wait for disconnect
                        recv_task = asyncio.create_task(self._task_receive())
                        try:
                            # Wait until session_lost is set or recv_task finishes
                            await self._session_lost.wait()
                        finally:
                            recv_task.cancel()
                            try:
                                await recv_task
                            except asyncio.CancelledError:
                                pass

                except Exception as e:
                    logger.error(
                        f"[Session] Connection/session error: {type(e).__name__}: {e}"
                    )

                if not self._running:
                    break

                # ── Reconnect backoff ──
                consecutive_failures += 1
                self._reconnecting = True
                self._session = None

                if consecutive_failures >= self._max_reconnect_attempts:
                    logger.error(
                        f"[Session] Reconnection failed {self._max_reconnect_attempts} time(s). "
                        f"Exiting."
                    )
                    print(
                        f"❌ Reconnection failed {self._max_reconnect_attempts} time(s). "
                        f"Exiting."
                    )
                    break

                delay = min(2 ** consecutive_failures, 30)
                logger.info(f"[Session] Reconnecting in {delay}s...")
                print(f"⏳ Reconnecting in {delay}s...")
                await asyncio.sleep(delay)

        except KeyboardInterrupt:
            pass
        finally:
            self._running = False
            mic_task.cancel()
            speaker_task.cancel()
            for t in (mic_task, speaker_task):
                try:
                    await t
                except asyncio.CancelledError:
                    pass
            logger.info("Session ended")

    def close(self):
        self._running = False
        self._pya.terminate()


# ══════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════

async def _main():
    # Download still-image assets
    image_asset_mgr = ImageAssetManager()
    image_asset_mgr.ensure_downloaded()

    today_titles = load_today_titles()
    content_list = load_content_list()

    if not content_list:
        logger.warning(
            "No playable content found in ContentIndex. "
            "Generate videos with M1/M3 before launching."
        )

    # Launch MediaWindow (separate thread)
    window = MediaWindow()
    window.start()
    register_hotkeys(window)

    # ── Start Breaking News ticker server ──
    ticker_state = TickerState()
    ticker_runner = await start_ticker_server(ticker_state)
    ticker_poll_task = asyncio.create_task(
        poll_article_store(ticker_state, _DB_PATH)
    )
    logger.info(
        f"Breaking News ticker started: http://127.0.0.1:{TICKER_PORT}/overlay"
    )

    # ── Start Trump Truth Social monitor ──
    trump_monitor = TrumpMonitor(
        db_path=_DB_PATH,
        api_key=API_KEY,
        lang=_LANG,
    )
    trump_monitor.start()
    logger.info("[TrumpMonitor] Background monitor started (interval=300s)")

    client = GeminiLiveClient(window, content_list, today_titles, image_asset_mgr)
    try:
        await client.run()
    finally:
        # Stop Trump monitor
        trump_monitor.stop()
        logger.info("[TrumpMonitor] Background monitor stopped")

        # Stop ticker
        ticker_poll_task.cancel()
        try:
            await ticker_poll_task
        except asyncio.CancelledError:
            pass
        await ticker_runner.cleanup()
        logger.info("Breaking News ticker stopped")

        window.close()
        client.close()



if __name__ == "__main__":
    asyncio.run(_main())

"""
compe_M4/gemini_live_client.py

Gemini Live API を使ってジャーナリストの音声指示を受け取り、
Function Calling 経由で MediaWindow を操作するクライアント。

【PTT モード】
  F9 を押している間だけマイク音声を Gemini に送信する。
  離した瞬間に audio_stream_end を送って Gemini に発話終了を伝える。
  Gemini が応答中でも F9 で割り込み（barge-in）可能。

【キーボードショートカット】
  F5: 再生/一時停止トグル
  F6: 停止
  F7: ウィンドウ最小化
  F8: ウィンドウ復元
  F9: PTT（押しながら話す）

【コンテンツ検索方針（A案）】
  起動時に ArticleStore.get_today_titles() と ContentIndex の動画一覧を
  両方 system_instruction に渡す。
  「国連についてレポートを」のような自然語指示に対して Gemini が
  意味的に最適な content_id を自分で選んで play_video を呼び出す。
  store.search() は補助ツールとして提供し、Gemini が必要と判断した場合のみ使用。

依存:
    uv add pyaudio google-genai keyboard python-dotenv python-vlc Pillow

使い方:
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

# ── プロジェクトルートを sys.path に追加 ──────────────────────────
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

# ArticleStore は compe_M3/ 配下なので sys.path に追加
_M3_ROOT = _PROJECT_ROOT / "compe_M3"
if str(_M3_ROOT) not in sys.path:
    sys.path.insert(0, str(_M3_ROOT))

# ── ロギング ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

# ArticleStore の import（失敗しても起動継続）
try:
    from store.article_store import ArticleStore
    _ARTICLE_STORE_AVAILABLE = True
except ImportError:
    logger.warning("ArticleStore が import できません。記事一覧なしで起動します。")
    _ARTICLE_STORE_AVAILABLE = False

# ── 環境変数 ────────────────────────────────────────────────────────
load_dotenv(_PROJECT_ROOT / "compe_M1" / "config" / ".env")
API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    raise EnvironmentError(
        "GOOGLE_API_KEY が設定されていません。"
        "compe_M1/config/.env を確認してください。"
    )

# ── Gemini モデル ───────────────────────────────────────────────────
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"

# ── 音声設定（Live API の仕様に合わせて固定） ───────────────────────
FORMAT       = pyaudio.paInt16
CHANNELS     = 1
SEND_RATE    = 16000   # マイク入力: 16kHz
RECEIVE_RATE = 24000   # Gemini 出力: 24kHz
CHUNK_SIZE   = 1024

# ── PTT キー ────────────────────────────────────────────────────────
PTT_KEY = "f9"

# ── ArticleStore DB パス ────────────────────────────────────────────
_DB_PATH = str(_M3_ROOT / "data" / "articles.db")


# ══════════════════════════════════════════════════════════════════
# 起動時データロード
# ══════════════════════════════════════════════════════════════════

def load_today_titles() -> list[dict]:
    """ArticleStore から本日の記事タイトル一覧を取得する。"""
    if not _ARTICLE_STORE_AVAILABLE:
        return []
    try:
        store = ArticleStore(db_path=_DB_PATH)
        titles = store.get_today_titles(min_importance=3.0)
        logger.info(f"ArticleStore: {len(titles)} 件の記事タイトルをロード")
        return titles
    except Exception as e:
        logger.warning(f"ArticleStore からの取得に失敗: {e}")
        return []


def load_content_list() -> list[dict]:
    """
    ContentIndex から再生可能なコンテンツ一覧を取得する。
    動画だけでなくスクリーンショット（静止画）も含む。
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

        # メディア種別を判定
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

    logger.info(f"ContentIndex: {len(content_list)} 件のコンテンツをロード")
    return content_list


# ══════════════════════════════════════════════════════════════════
# System Instruction 生成
# ══════════════════════════════════════════════════════════════════

def _build_system_instruction(
    today_titles: list[dict],
    content_list: list[dict],
    image_assets: list[dict],
) -> str:
    # ── 本日の記事一覧（上限30件）──
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
                f"  - [記事 id={a['id']}] {a.get('title', '')}{breaking}"
                f" (score={a.get('importance_score', '?')}"
                f", src={a.get('source_name', '')}){topics_str}"
            )
        articles_section = "【本日の主要記事】\n" + "\n".join(articles_lines)
    else:
        articles_section = "【本日の主要記事】\n  （データなし）"

    # ── 再生可能なコンテンツ一覧 ──
    if content_list:
        videos_lines = []
        for v in content_list:
            tags = ", ".join(v["topic_tags"]) if v["topic_tags"] else "-"
            videos_lines.append(
                f"  - [content_id={v['content_id']}] [{v['type']}] {v['title']}"
                f" (score={v['score']}, tags={tags}, media={v.get('media_type', '?')})"
            )
        videos_section = "【再生可能な動画・クリップ一覧】\n" + "\n".join(videos_lines)
    else:
        videos_section = "【再生可能な動画・クリップ一覧】\n  （動画なし）"

    # ── 静止画アセット一覧 ──
    if image_assets:
        images_lines = []
        for a in image_assets:
            tags = ", ".join(a.get("topic_tags", []))
            images_lines.append(
                f"  - [image_id={a['id']}] {a['title']} (tags={tags})"
            )
        images_section = "【表示可能な静止画アセット】\n" + "\n".join(images_lines)
    else:
        images_section = "【表示可能な静止画アセット】\n  （なし）"

    return f"""あなたは StoryWire のニュース放送 AI ディレクターです。
ジャーナリストが OBS でライブ配信中に音声で指示を出します。
指示に応じて動画の再生・停止・静止画表示・ウィンドウ操作などのツールを呼び出してください。

{articles_section}

{videos_section}

{images_section}

【動画選択のルール】
- ジャーナリストが「〇〇についての動画を流して」「〇〇のレポートをお願い」などと指示したら、
  上記の動画一覧から最も関連性の高い content_id を選んで play_video を呼び出す。
- 記事一覧の topics や title も参考にして最適なコンテンツを選ぶこと。
- 該当動画が複数ある場合は score が高いものを優先する。
- 動画一覧に適切なものが見当たらない場合は search_articles ツールで追加検索してから判断する。

【静止画表示のルール】
- 「ホルムズ海峡の地図を出して」「イランの地図を見せて」のような指示には show_image を使う。
- image_id で静止画アセットを指定する。
- file_path での直接指定も可能。
- 動画再生中に静止画を指示された場合は、動画を停止して静止画に切り替える。

【応答ルール】
- 応答は日本語で簡潔に行う。
- ツールを呼び出す場合は「〇〇の動画を再生します」「〇〇の地図を表示します」など一言添える。
- ジャーナリストはライブ配信中のため、余計な前置きは不要。"""


# ══════════════════════════════════════════════════════════════════
# ツール定義
# ══════════════════════════════════════════════════════════════════

def _make_tools() -> list[dict]:
    return[
        {
            "function_declarations":[
                {
                    "name": "play_video",
                    "description": (
                        "指定した content_id の動画を再生する。"
                        "system_instruction の動画一覧から最適な content_id を選んで呼び出すこと。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "content_id": {
                                "type": "string",
                                "description": "再生する動画の content_id（動画一覧から選ぶ）",
                            }
                        },
                        "required": ["content_id"],
                    },
                },
                {
                    "name": "stop_video",
                    "description": "現在再生中の動画・静止画を停止してウィンドウを最小化する。",
                },
                {
                    "name": "pause_video",
                    "description": "再生中の動画を一時停止する。",
                },
                {
                    "name": "resume_video",
                    "description": "一時停止中の動画を再開する。",
                },
                {
                    "name": "show_image",
                    "description": (
                        "静止画をウィンドウに表示する。"
                        "image_id（静止画アセット一覧の ID）または file_path（ファイルパス）で指定する。"
                        "動画再生中の場合は自動的に停止して静止画に切り替わる。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "image_id": {
                                "type": "string",
                                "description": "静止画アセットの ID（例: hormuz_map）。image_id または file_path のいずれかを指定する。",
                            },
                            "file_path": {
                                "type": "string",
                                "description": "画像ファイルの絶対パス。image_id が指定されていない場合に使用する。",
                            },
                        },
                    },
                },
                {
                    "name": "minimize_window",
                    "description": "メディアウィンドウを最小化する。",
                },
                {
                    "name": "restore_window",
                    "description": "最小化されたメディアウィンドウを復元する。",
                },
                {
                    "name": "search_articles",
                    "description": (
                        "ArticleStore をキーワードで検索し関連記事を返す。"
                        "動画一覧に適切なものが見当たらない場合の補助ツール。"
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "query": {
                                "type": "string",
                                "description": "検索キーワード（英語推奨: 'UN', 'Iran' など）",
                            },
                            "limit": {
                                "type": "integer",
                                "description": "最大取得件数（デフォルト5）",
                            },
                        },
                        "required":["query"],
                    },
                },
                {
                    "name": "list_videos",
                    "description": "現在再生可能な動画・クリップの一覧を読み上げる。",
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
                return {"result": "停止しました"}
            elif name == "pause_video":
                self._window.pause()
                return {"result": "一時停止しました"}
            elif name == "resume_video":
                self._window.resume()
                return {"result": "再開しました"}
            elif name == "show_image":
                return self._show_image(
                    args.get("image_id", ""),
                    args.get("file_path", ""),
                )
            elif name == "minimize_window":
                self._window.minimize()
                return {"result": "ウィンドウを最小化しました"}
            elif name == "restore_window":
                self._window.restore()
                return {"result": "ウィンドウを復元しました"}
            elif name == "search_articles":
                return self._search_articles(
                    args.get("query", ""),
                    int(args.get("limit", 5)),
                )
            elif name == "list_videos":
                return self._list_videos()
            else:
                return {"error": f"未知のツール: {name}"}
        except Exception as e:
            logger.error(f"[ToolCall] {name} 実行エラー: {e}", exc_info=True)
            return {"error": str(e)}

    def _play_video(self, content_id: str) -> dict:
        if not content_id:
            return {"error": "content_id が指定されていません"}
        entry = self._content_map.get(content_id)
        if entry is None:
            return {"error": f"content_id '{content_id}' が見つかりません"}

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
            return {"result": f"「{entry['title']}」を表示中"}
        return {"error": f"再生に失敗しました: {content_id}"}

    def _show_image(self, image_id: str, file_path: str) -> dict:
        if image_id:
            path = self._image_assets.get_path(image_id)
            if not path:
                return {"error": f"静止画アセット '{image_id}' が見つかりません"}
            asset = self._image_assets.get(image_id)
            title = asset.get("title", image_id) if asset else image_id
        elif file_path:
            path = file_path
            title = Path(file_path).name
        else:
            return {"error": "image_id または file_path を指定してください"}

        ok = self._window.show_image(path)
        if ok:
            return {"result": f"「{title}」を表示中"}
        return {"error": f"画像の表示に失敗しました: {path}"}

    def _search_articles(self, query: str, limit: int = 5) -> dict:
        if not _ARTICLE_STORE_AVAILABLE:
            return {"error": "ArticleStore が利用できません"}
        try:
            store = ArticleStore(db_path=_DB_PATH)
            results = store.search(query, min_importance=3.0, limit=limit)
            if not results:
                return {"result": f"'{query}' に関する記事は見つかりませんでした"}
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
            return {"result": "再生可能な動画がありません"}
        lines = [
            f"{v['content_id']}: [{v['type']}] {v['title']} (score={v['score']})"
            for v in self._content_list
        ]
        return {"result": "\n".join(lines)}


# ══════════════════════════════════════════════════════════════════
# GeminiLiveClient（PTT + コンテンツ検索 + 静止画対応版）
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

    def _build_config(self) -> dict:
        return {
            "response_modalities": ["AUDIO"],
            "tools": _make_tools(),
            "system_instruction": _build_system_instruction(
                self._today_titles,
                self._content_list,
                self._image_asset_mgr.list_all(),
            ),
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

    async def _task_ptt_mic(self):
            mic_info = self._pya.get_default_input_device_info()
            logger.info(f"マイク: {mic_info['name']}")
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
                        logger.info(f"[PTT] 開始 ({PTT_KEY.upper()} 押下)")
                        print(f"\n🎙  話しかけてください... ({PTT_KEY.upper()} を押している間)")
                        silence_chunks_left = 0

                    elif was_pressed and not is_pressed:
                        logger.info("[PTT] 終了（音声送信停止）")
                        print("⏹  送信停止。Gemini が処理中...\n")
                        silence_chunks_left = 30

                    if self._session:
                        if is_pressed:
                            await self._session.send_realtime_input(
                                audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_RATE}")
                            )
                        elif silence_chunks_left > 0:
                            silence_data = b'\x00' * len(data)
                            await self._session.send_realtime_input(
                                audio=types.Blob(data=silence_data, mime_type=f"audio/pcm;rate={SEND_RATE}")
                            )
                            silence_chunks_left -= 1

                    was_pressed = is_pressed
            finally:
                stream.stop_stream()
                stream.close()

    async def _task_receive(self):
        # receive() は turn_complete でイテレーションが終了するため、
        # while ループで再度 receive() を呼び直す必要がある。
        # (参考: https://github.com/googleapis/python-genai/issues/1224)
        while self._running:
            try:
                async for response in self._session.receive():
                    if response.data is not None:
                        await self._audio_out_queue.put(response.data)
                    if response.text:
                        logger.debug(f"[Gemini TEXT] {response.text}")
                    if response.server_content and response.server_content.input_transcription:
                        t = response.server_content.input_transcription.text
                        if t:
                            print(f"📝 [あなた]  {t}")
                    if response.server_content and response.server_content.output_transcription:
                        t = response.server_content.output_transcription.text
                        if t:
                            print(f"🤖 [Gemini] {t}")
                    if response.server_content and response.server_content.turn_complete:
                        await self._audio_out_queue.put(None)
                        print("─── (F9 で話しかけてください) ───\n")
                    if response.tool_call:
                        asyncio.create_task(self._handle_tool_call(response.tool_call))
            except Exception as e:
                if not self._running:
                    break
                logger.warning(f"[RECV] receive() で例外: {type(e).__name__}: {e}")
                await asyncio.sleep(0.1)

    async def _handle_tool_call(self, tool_call):
            try:
                function_responses = []
                for fc in tool_call.function_calls:
                    args = dict(fc.args) if fc.args else {}
                    result = await asyncio.to_thread(self._executor.execute, fc.name, args)
                    function_responses.append(
                        types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                    )
                    logger.info(f"[ToolResult] {fc.name} → {result}")

                if hasattr(self._session, "send_tool_response"):
                    await self._session.send_tool_response(function_responses=function_responses)
                elif hasattr(self._session, "send"):
                    await self._session.send(input={"function_responses": function_responses})
                else:
                    logger.error("セッションにツール結果を送信するメソッドが見つかりません。")

            except Exception as e:
                logger.error(f"[_handle_tool_call] ツール結果の送信中にエラー: {e}", exc_info=True)

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
        config = self._build_config()
        logger.info(f"Gemini Live API に接続中... (model={MODEL})")
        async with self._client.aio.live.connect(model=MODEL, config=config) as session:
            self._session = session
            self._running = True
            print("\n" + "═" * 55)
            print("  StoryWire M4 - Gemini Live PTT モード")
            print("═" * 55)
            print(f"  記事: {len(self._today_titles)} 件 / 動画: {len(self._content_list)} 件")
            print(f"  静止画アセット: {len(self._image_asset_mgr.list_all())} 件")
            print(f"  {PTT_KEY.upper()} を押しながら話しかけてください")
            print("  F5=再生/停止トグル F6=停止 F7=最小化 F8=復元")
            print("  Ctrl+C で終了")
            print("═" * 55)
            if self._content_list:
                print("\n【再生可能なコンテンツ一覧】")
                for v in self._content_list:
                    tags = ", ".join(v["topic_tags"]) if v["topic_tags"] else "-"
                    mt = v.get("media_type", "?")
                    print(
                        f"  [{v['content_id']}] [{v['type']}|{mt}] {v['title']}"
                        f" (score={v['score']:.1f}, tags={tags})"
                    )
            else:
                print("\n【再生可能なコンテンツ一覧】\n  （なし）")

            image_assets = self._image_asset_mgr.list_all()
            if image_assets:
                print("\n【静止画アセット】")
                for a in image_assets:
                    tags = ", ".join(a.get("topic_tags", []))
                    print(f"  [{a['id']}] {a['title']} (tags={tags})")

            print()
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._task_ptt_mic())
                    tg.create_task(self._task_receive())
                    tg.create_task(self._task_play_audio())
            except* KeyboardInterrupt:
                pass
            finally:
                self._running = False
                logger.info("セッション終了")

    def close(self):
        self._running = False
        self._pya.terminate()


# ══════════════════════════════════════════════════════════════════
# エントリポイント
# ══════════════════════════════════════════════════════════════════

async def _main():
    # 静止画アセットのダウンロード
    image_asset_mgr = ImageAssetManager()
    image_asset_mgr.ensure_downloaded()

    today_titles = load_today_titles()
    content_list = load_content_list()

    if not content_list:
        logger.warning(
            "ContentIndex に再生可能なコンテンツがありません。"
            "M1/M3 で動画を生成してから起動してください。"
        )

    # MediaWindow 起動（別スレッド）
    window = MediaWindow()
    window.start()
    register_hotkeys(window)

    client = GeminiLiveClient(window, content_list, today_titles, image_asset_mgr)
    try:
        await client.run()
    finally:
        window.close()
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())

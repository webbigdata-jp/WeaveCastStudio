"""
compe_M4/gemini_live_client.py

Gemini Live API を使ってジャーナリストの音声指示を受け取り、
Function Calling 経由で VLCPlayer を操作するクライアント。

【PTT モード】
  F9 を押している間だけマイク音声を Gemini に送信する。
  離した瞬間に audio_stream_end を送って Gemini に発話終了を伝える。
  Gemini が応答中でも F9 で割り込み（barge-in）可能。

依存:
    uv add pyaudio google-genai pygetwindow keyboard python-dotenv

使い方:
    python gemini_live_client.py
"""

import asyncio
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

from vlc_player import VLCPlayer          # noqa: E402
from content_index import ContentIndexManager  # noqa: E402

# ── ロギング ────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

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


# ══════════════════════════════════════════════════════════════════
# ツール定義
# ══════════════════════════════════════════════════════════════════

def _make_tools(content_list: list[dict]) -> list[dict]:
    items_desc = "\n".join(
        f"  {i['index']}: [{i['type']}] {i['title']} (score={i['score']})"
        for i in content_list
    ) or "  （コンテンツなし）"

    return [
        {
            "function_declarations": [
                {
                    "name": "play_video",
                    "description": (
                        "指定した番号（index）の動画を再生する。\n"
                        "利用可能なコンテンツ一覧:\n" + items_desc
                    ),
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "index": {
                                "type": "integer",
                                "description": "再生するコンテンツの番号（0始まり）",
                            }
                        },
                        "required": ["index"],
                    },
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "stop_video",
                    "description": "現在再生中の動画を停止する。",
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "pause_video",
                    "description": "再生中の動画を一時停止する。",
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "resume_video",
                    "description": "一時停止中の動画を再開する。",
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "minimize_window",
                    "description": "VLC 動画ウィンドウを最小化する。",
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "restore_window",
                    "description": "最小化された VLC 動画ウィンドウを元に戻す。",
                    "behavior": "NON_BLOCKING",
                },
                {
                    "name": "list_videos",
                    "description": "利用可能なコンテンツの一覧を読み上げる。",
                },
            ]
        }
    ]


# ══════════════════════════════════════════════════════════════════
# ToolExecutor
# ══════════════════════════════════════════════════════════════════

class ToolExecutor:
    def __init__(self, player: VLCPlayer, content_list: list[dict]):
        self._player = player
        self._content_list = content_list

    def execute(self, name: str, args: dict) -> dict:
        logger.info(f"[ToolCall] {name}({args})")
        try:
            if name == "play_video":
                return self._play_video(int(args.get("index", 0)))
            elif name == "stop_video":
                self._player.stop()
                return {"result": "停止しました"}
            elif name == "pause_video":
                self._player.pause()
                return {"result": "一時停止しました"}
            elif name == "resume_video":
                self._player.resume()
                return {"result": "再開しました"}
            elif name == "minimize_window":
                self._player.minimize()
                return {"result": "ウィンドウを最小化しました"}
            elif name == "restore_window":
                self._player.restore()
                return {"result": "ウィンドウを復元しました"}
            elif name == "list_videos":
                return self._list_videos()
            else:
                return {"error": f"未知のツール: {name}"}
        except Exception as e:
            logger.error(f"[ToolCall] {name} 実行エラー: {e}", exc_info=True)
            return {"error": str(e)}

    def _play_video(self, index: int) -> dict:
        if not self._content_list:
            return {"error": "コンテンツがありません"}
        if not (0 <= index < len(self._content_list)):
            return {"error": f"番号 {index} は範囲外です（0〜{len(self._content_list)-1}）"}
        entry_info = self._content_list[index]
        if self._player.is_playing():
            ok = self._player.swap(entry_info["id"])
        else:
            ok = self._player.play_by_index_id(entry_info["id"])
        if ok:
            return {"result": f"「{entry_info['title']}」を再生中"}
        return {"error": f"再生に失敗しました: {entry_info['id']}"}

    def _list_videos(self) -> dict:
        if not self._content_list:
            return {"result": "コンテンツがありません"}
        lines = [
            f"{item['index']}: {item['title']} ({item['type']}, score={item['score']})"
            for item in self._content_list
        ]
        return {"result": "\n".join(lines)}


# ══════════════════════════════════════════════════════════════════
# GeminiLiveClient（PTT版）
# ══════════════════════════════════════════════════════════════════

class GeminiLiveClient:
    """
    F9 を押している間だけマイク音声を Gemini Live API に送信する PTT クライアント。

    タスク構成:
      _task_ptt_mic   : F9 状態を監視しマイク PCM を送信 / stream_end を通知
      _task_receive   : Gemini レスポンスを受信・処理（音声 / テキスト / ToolCall）
      _task_play_audio: Gemini 音声をスピーカーに出力
    """

    def __init__(self, player: VLCPlayer, content_list: list[dict]):
        self._player = player
        self._content_list = content_list
        self._executor = ToolExecutor(player, content_list)
        self._tools = _make_tools(content_list)

        self._client = genai.Client(api_key=API_KEY)
        self._pya = pyaudio.PyAudio()

        # Gemini 音声出力バッファ（None = ターン終端マーカー）
        self._audio_out_queue: asyncio.Queue[bytes | None] = asyncio.Queue()

        self._session = None
        self._running = False

    # ──────────────────────────────────────────────────────────────
    # セッション設定
    # ──────────────────────────────────────────────────────────────

    def _build_config(self) -> dict:
        items_desc = "\n".join(
            f"  {i['index']}: [{i['type']}] {i['title']} (重要度={i['score']})"
            for i in self._content_list
        ) or "  （コンテンツなし）"

        system_instruction = (
            "あなたは StoryWire のニュース放送ディレクターです。\n"
            "ジャーナリストの音声指示を聞いて、動画の再生・停止・ウィンドウ操作を行います。\n"
            "指示に応じて適切なツールを呼び出してください。\n\n"
            "利用可能なコンテンツ:\n" + items_desc + "\n\n"
            "応答は日本語で、簡潔に行ってください。"
        )

        return {
            "response_modalities": ["AUDIO"],
            "tools": self._tools,
            "system_instruction": system_instruction,
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

    # ──────────────────────────────────────────────────────────────
    # タスク: PTT マイク送信
    # ──────────────────────────────────────────────────────────────

    async def _task_ptt_mic(self):
        """
        F9 の押下状態を監視する。
        - 押している間: マイク PCM を send_realtime_input で送信
        - 離した瞬間 : audio_stream_end を送って Gemini に発話終了を通知
        """
        mic_info = self._pya.get_default_input_device_info()
        logger.info(f"マイク: {mic_info['name']}")

        stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=SEND_RATE,
            input=True,
            input_device_index=int(mic_info["index"]),
            frames_per_buffer=CHUNK_SIZE,
        )

        was_pressed = False
        try:
            while self._running:
                is_pressed = kb.is_pressed(PTT_KEY)

                if is_pressed and not was_pressed:
                    # ── F9 押下：送信開始 ──
                    logger.info(f"[PTT] 開始 ({PTT_KEY.upper()} 押下)")
                    print(f"\n🎙  話しかけてください... ({PTT_KEY.upper()} を押している間)")

                if is_pressed:
                    # ── 送信中：PCM チャンクを Gemini へ ──
                    data = await asyncio.to_thread(stream.read, CHUNK_SIZE, False)
                    if self._session:
                        await self._session.send_realtime_input(
                            audio=types.Blob(
                                data=data,
                                mime_type=f"audio/pcm;rate={SEND_RATE}",
                            )
                        )

                elif was_pressed and not is_pressed:
                    # ── F9 リリース：発話終了を通知 ──
                    logger.info("[PTT] 終了（音声送信停止）")
                    print("⏹  送信停止。Gemini が処理中...\n")
                    if self._session:
                        await self._session.send_realtime_input(
                            audio_stream_end=True
                        )

                else:
                    # ── 待機中：CPU を占有しないよう短いスリープ ──
                    await asyncio.sleep(0.01)

                was_pressed = is_pressed

        finally:
            stream.stop_stream()
            stream.close()

    # ──────────────────────────────────────────────────────────────
    # タスク: Gemini レスポンス受信
    # ──────────────────────────────────────────────────────────────

    async def _task_receive(self):
        async for response in self._session.receive():

            # 音声データ
            if response.data is not None:
                await self._audio_out_queue.put(response.data)

            # テキスト（デバッグ用）
            if response.text:
                logger.debug(f"[Gemini TEXT] {response.text}")

            # 入力音声の文字起こし
            if (
                response.server_content
                and response.server_content.input_transcription
            ):
                t = response.server_content.input_transcription.text
                if t:
                    print(f"📝 [あなた]  {t}")

            # 出力音声の文字起こし
            if (
                response.server_content
                and response.server_content.output_transcription
            ):
                t = response.server_content.output_transcription.text
                if t:
                    print(f"🤖 [Gemini] {t}")

            # ターン完了
            if (
                response.server_content
                and response.server_content.turn_complete
            ):
                await self._audio_out_queue.put(None)
                print(f"─── (F9 で話しかけてください) ───\n")

            # Function Call
            if response.tool_call:
                await self._handle_tool_call(response.tool_call)

    async def _handle_tool_call(self, tool_call):
        function_responses = []
        for fc in tool_call.function_calls:
            args = dict(fc.args) if fc.args else {}
            result = self._executor.execute(fc.name, args)
            function_responses.append(
                types.FunctionResponse(
                    id=fc.id,
                    name=fc.name,
                    response=result,
                )
            )
            logger.info(f"[ToolResult] {fc.name} → {result}")

        await self._session.send_tool_response(
            function_responses=function_responses
        )

    # ──────────────────────────────────────────────────────────────
    # タスク: 音声再生
    # ──────────────────────────────────────────────────────────────

    async def _task_play_audio(self):
        stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT,
            channels=CHANNELS,
            rate=RECEIVE_RATE,
            output=True,
            frames_per_buffer=CHUNK_SIZE,
        )
        try:
            while self._running:
                chunk = await self._audio_out_queue.get()
                if chunk is None:
                    continue  # ターン終端マーカー
                await asyncio.to_thread(stream.write, chunk)
        finally:
            stream.stop_stream()
            stream.close()

    # ──────────────────────────────────────────────────────────────
    # 公開インターフェース
    # ──────────────────────────────────────────────────────────────

    async def run(self):
        config = self._build_config()
        logger.info(f"Gemini Live API に接続中... (model={MODEL})")

        async with self._client.aio.live.connect(
            model=MODEL, config=config
        ) as session:
            self._session = session
            self._running = True

            print("\n" + "═" * 50)
            print("  StoryWire M4 - Gemini Live PTT モード")
            print("═" * 50)
            print(f"  {PTT_KEY.upper()} を押しながら話しかけてください")
            print("  Ctrl+C で終了")
            print("═" * 50 + "\n")

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
    mgr = ContentIndexManager()
    all_entries = mgr.get_all(sort_by_importance=True)

    content_list = []
    for entry in all_entries:
        abs_path = entry.get("video_path_abs") or ""
        rel_path = entry.get("video_path") or ""
        resolved = (
            abs_path if Path(abs_path).exists()
            else str(_PROJECT_ROOT / rel_path)
            if rel_path and (_PROJECT_ROOT / rel_path).exists()
            else None
        )
        if resolved:
            content_list.append({
                "index": len(content_list),
                "id": entry["id"],
                "title": entry.get("title", "(no title)"),
                "type": entry.get("type", "?"),
                "score": entry.get("importance_score", 0),
                "path": resolved,
            })

    if not content_list:
        logger.warning(
            "ContentIndex に再生可能なコンテンツがありません。"
            "M1/M3 で動画を生成してから起動してください。"
        )

    logger.info(f"コンテンツ {len(content_list)} 件をロード")
    for item in content_list:
        logger.info(f"  [{item['index']}] {item['title']} ({item['type']})")

    player = VLCPlayer()
    client = GeminiLiveClient(player, content_list)

    try:
        await client.run()
    finally:
        player.release()
        client.close()


if __name__ == "__main__":
    asyncio.run(_main())

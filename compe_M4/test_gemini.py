import asyncio
import os
import sys
import keyboard as kb
import pyaudio
from google import genai
from google.genai import types
from dotenv import load_dotenv

# 環境変数の読み込み
load_dotenv(os.path.join(os.path.dirname(__file__), "..", "compe_M1", "config", ".env"))
API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("API_KEYが設定されていません。")
    sys.exit(1)

# モデルと音声設定
MODEL = "gemini-2.5-flash-native-audio-preview-12-2025"
FORMAT = pyaudio.paInt16
CHANNELS = 1
SEND_RATE = 16000
RECEIVE_RATE = 24000
CHUNK_SIZE = 1024
PTT_KEY = "f9"

# ============================================================
# 検証フラグ
# ============================================================
# True  : PTTを離した時に audio_stream_end を送信する（元の動作）
# False : PTTを離しても audio_stream_end を送らず、単に音声送信を停止する
USE_STREAM_END = True
# ============================================================

def _make_tools():
    return [
        {
            "function_declarations":[
                {
                    "name": "dummy_play_video",
                    "description": "動画を再生するダミーツール。「動画を再生して」と言われたら呼び出すこと。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "video_name": {
                                "type": "string",
                                "description": "再生する動画のタイトル"
                            }
                        },
                        "required": ["video_name"]
                    }
                }
            ]
        }
    ]

class MinimalLiveClient:
    def __init__(self):
        self._client = genai.Client(api_key=API_KEY)
        self._pya = pyaudio.PyAudio()
        self._audio_out_queue = asyncio.Queue()
        self._session = None
        self._running = False
        self._turn_count = 0  # ターン数カウント

    async def _task_ptt_mic(self):
        mic_info = self._pya.get_default_input_device_info()
        print(f"マイク: {mic_info['name']}")
        stream = await asyncio.to_thread(
            self._pya.open,
            format=FORMAT, channels=CHANNELS, rate=SEND_RATE,
            input=True, input_device_index=int(mic_info["index"]),
            frames_per_buffer=CHUNK_SIZE,
        )
        was_pressed = False
        chunk_count = 0
        try:
            while self._running:
                is_pressed = kb.is_pressed(PTT_KEY)
                if is_pressed and not was_pressed:
                    chunk_count = 0
                    print(f"\n[PTT] 開始 ({PTT_KEY.upper()} 押下)")
                if is_pressed:
                    data = await asyncio.to_thread(stream.read, CHUNK_SIZE, False)
                    chunk_count += 1
                    if self._session:
                        await self._session.send_realtime_input(
                            audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={SEND_RATE}")
                        )
                elif was_pressed and not is_pressed:
                    print(f"[PTT] 終了（送信チャンク数: {chunk_count}）")
                    if USE_STREAM_END:
                        print("[PTT] audio_stream_end=True を送信します")
                        if self._session:
                            await self._session.send_realtime_input(audio_stream_end=True)
                    else:
                        print("[PTT] audio_stream_end は送信しません（VADに任せる）")
                else:
                    await asyncio.sleep(0.01)
                was_pressed = is_pressed
        except Exception as e:
            print(f"❌ [MIC タスク例外] {type(e).__name__}: {e}")
            raise
        finally:
            print("[MIC] タスク終了")
            stream.stop_stream()
            stream.close()

    async def _task_receive(self):
        """
        修正ポイント: receive() を while ループで囲む。
        receive() は turn_complete でイテレーション終了する可能性があるため、
        ループで再度 receive() を呼び直す。
        （GitHub Issue #1224 の解決策）
        """
        loop_count = 0
        while self._running:
            loop_count += 1
            print(f"🔄 [RECV] receive() ループ開始 (#{loop_count})")
            try:
                async for response in self._session.receive():
                    if response.data is not None:
                        await self._audio_out_queue.put(response.data)

                    # テキストの書き起こし出力
                    if response.server_content:
                        if response.server_content.input_transcription and response.server_content.input_transcription.text:
                            print(f"📝 [あなた] {response.server_content.input_transcription.text.strip()}")
                        if response.server_content.output_transcription and response.server_content.output_transcription.text:
                            print(f"🤖 [Gemini] {response.server_content.output_transcription.text.strip()}")
                        if response.server_content.turn_complete:
                            self._turn_count += 1
                            await self._audio_out_queue.put(None)
                            print(f"─── ターン {self._turn_count} 完了 (F9 で話しかけてください) ───\n")

                    # ツールコールの処理
                    if response.tool_call:
                        print(f"\n🛠 [ToolCall 受信] ツール呼び出し要求を受け取りました")
                        asyncio.create_task(self._handle_tool_call(response.tool_call))

                # ここに来た = async for が正常終了した
                print(f"⚠️ [RECV] receive() のイテレーションが終了しました (ループ #{loop_count})")

            except Exception as e:
                print(f"❌ [RECV タスク例外] {type(e).__name__}: {e}")
                if not self._running:
                    break
                # 少し待ってからリトライ
                await asyncio.sleep(0.1)

        print("[RECV] タスク終了")

    async def _handle_tool_call(self, tool_call):
        try:
            function_responses = []
            for fc in tool_call.function_calls:
                args = dict(fc.args) if fc.args else {}
                print(f"⚙️ 実行中: {fc.name}({args})")

                # ダミーの処理時間 (1秒)
                await asyncio.sleep(1)
                result = {"result": f"ダミーの {fc.name} 実行完了。問題なし。"}

                function_responses.append(
                    types.FunctionResponse(id=fc.id, name=fc.name, response=result)
                )
                print(f"✅ 実行完了: {result}")

            print("📤 ツール結果をGeminiに送信中...")
            if hasattr(self._session, "send_tool_response"):
                await self._session.send_tool_response(function_responses=function_responses)
            else:
                await self._session.send(input={"function_responses": function_responses})
            print("📤 送信完了\n")

        except Exception as e:
            print(f"❌ ツール処理/送信エラー: {type(e).__name__}: {e}")

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
        except Exception as e:
            print(f"❌ [PLAY タスク例外] {type(e).__name__}: {e}")
            raise
        finally:
            print("[PLAY] タスク終了")
            stream.stop_stream()
            stream.close()

    async def run(self):
        config = {
            "response_modalities": ["AUDIO"],
            "tools": _make_tools(),
            "system_instruction": "あなたはテストアシスタントです。ユーザーが「動画を再生して」などと言ったら dummy_play_video ツールを呼んでください。ツールを呼んだ後には「再生しました」と返事してください。",
            "input_audio_transcription": {},
            "output_audio_transcription": {},
        }

        print("Gemini Live API に接続中...")
        print(f"[設定] USE_STREAM_END = {USE_STREAM_END}")
        async with self._client.aio.live.connect(model=MODEL, config=config) as session:
            self._session = session
            self._running = True
            print("=========================================")
            print("接続完了。F9を押しながら話してください。")
            print("=========================================")

            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._task_ptt_mic())
                    tg.create_task(self._task_receive())
                    tg.create_task(self._task_play_audio())
            except* KeyboardInterrupt:
                pass
            except* Exception as eg:
                for e in eg.exceptions:
                    print(f"❌ [TaskGroup例外] {type(e).__name__}: {e}")
            finally:
                self._running = False
                print("終了しました。")

def main():
    client = MinimalLiveClient()
    try:
        asyncio.run(client.run())
    finally:
        client._pya.terminate()

if __name__ == "__main__":
    main()
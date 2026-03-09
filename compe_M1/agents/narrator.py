"""
STEP 6: ナレーション音声生成
原稿テキストをGemini TTSで音声化する。

使用モデル: gemini-2.5-flash-preview-tts
出力フォーマット: PCM 16-bit, 24000 Hz, mono → WAVファイルとして保存
"""

import re
import time
import wave
import logging
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

TTS_MODEL = "gemini-2.5-flash-preview-tts"
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2   # 16-bit = 2 bytes
CHANNELS = 1       # mono

# 1回のTTSリクエストの最大文字数（安全マージンを持たせた値）
MAX_CHARS_PER_REQUEST = 4000

# リトライ設定
MAX_RETRIES = 4
RETRY_BASE_WAIT = 5   # 秒（指数バックオフの基底）


def _save_wave(path: Path, pcm_data: bytes) -> None:
    """PCMバイトデータをWAVファイルとして保存する"""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


def _save_silence(path: Path, duration_seconds: float = 3.0) -> None:
    """指定秒数の無音WAVファイルを生成する（TTS失敗時のプレースホルダー）"""
    num_frames = int(SAMPLE_RATE * duration_seconds)
    pcm_silence = b'\x00' * num_frames * SAMPLE_WIDTH * CHANNELS
    _save_wave(path, pcm_silence)


def _split_into_paragraphs(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """
    テキストをパラグラフ単位で分割する。
    1パラグラフがmax_charsを超える場合はさらに文単位で分割する。
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    result = []

    for para in paragraphs:
        if len(para) <= max_chars:
            result.append(para)
        else:
            # 文単位で分割
            sentences = re.split(r'(?<=[.!?])\s+', para)
            chunk = ""
            for sentence in sentences:
                if len(chunk) + len(sentence) + 1 <= max_chars:
                    chunk = (chunk + " " + sentence).strip()
                else:
                    if chunk:
                        result.append(chunk)
                    chunk = sentence
            if chunk:
                result.append(chunk)

    return result


def generate_narration(
    client: genai.Client,
    script_text: str,
    output_dir: Path,
    voice_name: str = "Charon",
) -> list[Path]:
    """
    原稿テキストをTTSで音声化し、パラグラフごとにWAVファイルを保存する。

    Args:
        client: 初期化済みの genai.Client
        script_text: [IMAGE: ...] マーカーを含む原稿テキスト
        output_dir: 音声ファイルの保存先ディレクトリ
        voice_name: 使用するボイス名 ("Charon", "Kore", "Puck" など)

    Returns:
        生成したWAVファイルパスのリスト
    """
    # [IMAGE: ...] マーカーを除去した純粋なテキストを取得
    clean_script = re.sub(r'\[IMAGE:\s*.*?\]', '', script_text).strip()

    paragraphs = _split_into_paragraphs(clean_script)
    logger.info(f"TTS: {len(paragraphs)} paragraphs to synthesize (voice: {voice_name})")

    audio_paths = []
    for i, paragraph in enumerate(paragraphs):
        output_path = output_dir / f"segment_{i:03d}.wav"
        logger.info(f"  Synthesizing paragraph {i+1}/{len(paragraphs)} ({len(paragraph)} chars)...")

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=TTS_MODEL,
                    contents=(
                        f"Read the following in a calm, authoritative, professional "
                        f"news anchor voice. Speak clearly and at a measured pace:\n\n"
                        f"{paragraph}"
                    ),
                    config=types.GenerateContentConfig(
                        response_modalities=["AUDIO"],
                        speech_config=types.SpeechConfig(
                            voice_config=types.VoiceConfig(
                                prebuilt_voice_config=types.PrebuiltVoiceConfig(
                                    voice_name=voice_name,
                                )
                            )
                        ),
                    ),
                )

                audio_data = response.candidates[0].content.parts[0].inline_data.data
                _save_wave(output_path, audio_data)
                audio_paths.append(output_path)
                logger.info(f"  -> Saved: {output_path}")
                success = True
                break  # 成功したのでリトライループを抜ける

            except Exception as e:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))  # 5s, 10s, 20s, 40s
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"  -> TTS attempt {attempt}/{MAX_RETRIES} failed: {e}. "
                        f"Retrying in {wait}s..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"  -> TTS failed after {MAX_RETRIES} attempts for paragraph {i}. "
                        f"Skipping segment."
                    )

        if not success:
            # 全リトライ失敗 → 無音WAVをプレースホルダーとして挿入して音声の欠落を防ぐ
            _save_silence(output_path, duration_seconds=3)
            audio_paths.append(output_path)
            logger.warning(f"  -> Inserted 3s silence placeholder for paragraph {i}.")

    logger.info(f"TTS complete: {len(audio_paths)}/{len(paragraphs)} segments synthesized")
    return audio_paths


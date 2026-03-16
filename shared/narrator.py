"""
Phase 3: Narration audio generation.
Converts a script text to speech using Gemini TTS.

Model: gemini-2.5-flash-preview-tts
Output format: PCM 16-bit, 24000 Hz, mono -> saved as WAV files.

The output language is controlled by LANGUAGE in .env (BCP-47 code).
Gemini TTS auto-detects language from the text, so prompting in the
target language is sufficient.
"""

import re
import time
import wave
import logging
from pathlib import Path
from google import genai
from google.genai import types

from .language_utils import get_language_config

logger = logging.getLogger(__name__)

TTS_MODEL = "gemini-2.5-flash-preview-tts"
SAMPLE_RATE = 24000
SAMPLE_WIDTH = 2   # 16-bit = 2 bytes
CHANNELS = 1       # mono

# Maximum characters per TTS request (conservative safe margin)
MAX_CHARS_PER_REQUEST = 4000

# Retry settings
MAX_RETRIES = 4
RETRY_BASE_WAIT = 5   # seconds (exponential backoff base)


def _save_wave(path: Path, pcm_data: bytes) -> None:
    """Save raw PCM bytes as a WAV file."""
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(CHANNELS)
        wf.setsampwidth(SAMPLE_WIDTH)
        wf.setframerate(SAMPLE_RATE)
        wf.writeframes(pcm_data)


def _save_silence(path: Path, duration_seconds: float = 3.0) -> None:
    """Generate a silent WAV file of the given duration (placeholder on TTS failure)."""
    num_frames = int(SAMPLE_RATE * duration_seconds)
    pcm_silence = b'\x00' * num_frames * SAMPLE_WIDTH * CHANNELS
    _save_wave(path, pcm_silence)


def _split_into_paragraphs(text: str, max_chars: int = MAX_CHARS_PER_REQUEST) -> list[str]:
    """
    Split text into paragraph-sized chunks.
    Paragraphs exceeding max_chars are further split at sentence boundaries.
    Handles both CJK full-stop '。' and Latin period '.'.
    """
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    result = []

    for para in paragraphs:
        if len(para) <= max_chars:
            result.append(para)
        else:
            # Split on CJK and Latin sentence endings
            sentences = re.split(r'(?<=[。.!?])\s*', para)
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
    Convert a narration script to speech and save one WAV file per paragraph.

    Args:
        client: Initialised genai.Client.
        script_text: Narration text (may contain [IMAGE: ...] markers).
        output_dir: Directory to write WAV files into.
        voice_name: TTS voice name (default: "Charon" — calm, broadcast-suitable).

    Returns:
        List of paths to the generated WAV files.
    """
    lang = get_language_config()

    # Strip [IMAGE: ...] markers before sending to TTS
    clean_script = re.sub(r'\[IMAGE:\s*.*?\]', '', script_text).strip()

    paragraphs = _split_into_paragraphs(clean_script)
    logger.info(
        f"TTS: converting {len(paragraphs)} paragraph(s) to audio "
        f"(voice: {voice_name}, language: {lang.prompt_lang})"
    )

    audio_paths = []
    for i, paragraph in enumerate(paragraphs):
        output_path = output_dir / f"segment_{i:03d}.wav"
        logger.info(
            f"  Paragraph {i+1}/{len(paragraphs)}: {len(paragraph)} chars ..."
        )

        success = False
        for attempt in range(1, MAX_RETRIES + 1):
            try:
                response = client.models.generate_content(
                    model=TTS_MODEL,
                    contents=(
                        f"Read the following text aloud in {lang.prompt_lang} "
                        f"as a calm, authoritative professional news anchor. "
                        f"Speak clearly, at a measured pace:\n\n"
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
                logger.info(f"  -> saved: {output_path}")
                success = True
                break

            except Exception as e:
                wait = RETRY_BASE_WAIT * (2 ** (attempt - 1))  # 5s, 10s, 20s, 40s
                if attempt < MAX_RETRIES:
                    logger.warning(
                        f"  -> TTS attempt {attempt}/{MAX_RETRIES} failed: {e}. "
                        f"Retrying in {wait}s ..."
                    )
                    time.sleep(wait)
                else:
                    logger.error(
                        f"  -> All {MAX_RETRIES} TTS attempts failed for paragraph {i}. "
                        f"Skipping segment."
                    )

        if not success:
            # All retries exhausted — insert silent placeholder to keep audio intact
            _save_silence(output_path, duration_seconds=3)
            audio_paths.append(output_path)
            logger.warning(
                f"  -> Inserted 3s silent placeholder for paragraph {i}."
            )

    logger.info(
        f"TTS complete: {len(audio_paths)}/{len(paragraphs)} segment(s) generated."
    )
    return audio_paths

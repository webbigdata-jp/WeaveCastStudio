"""
STEP 7: 動画合成
画像スライドショー + ナレーション音声 → MP4動画 (1920x1080, H.264, AAC)

依存: ffmpeg (システムインストール必須), pydub
"""

import subprocess
import logging
import tempfile
from pathlib import Path
from pydub import AudioSegment

logger = logging.getLogger(__name__)

# 音声セグメント間の無音挿入時間（ミリ秒）
GAP_BETWEEN_SEGMENTS_MS = 500

# 動画エンコード設定
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_CRF = 23         # 品質 (低いほど高品質・大容量)
VIDEO_PRESET = "medium"
AUDIO_BITRATE = "192k"


def merge_audio_segments(
    audio_paths: list[Path],
    output_path: Path,
    gap_ms: int = GAP_BETWEEN_SEGMENTS_MS,
) -> float:
    """
    複数のWAVファイルを結合してひとつのWAVファイルにする。
    セグメント間には gap_ms ミリ秒の無音を挿入する。

    Returns:
        結合後の音声の総秒数
    """
    logger.info(f"Merging {len(audio_paths)} audio segments...")
    silence = AudioSegment.silent(duration=gap_ms)
    combined = AudioSegment.empty()

    for path in audio_paths:
        segment = AudioSegment.from_wav(str(path))
        combined += segment + silence

    combined.export(str(output_path), format="wav")
    total_seconds = len(combined) / 1000.0
    logger.info(f"Merged audio: {total_seconds:.1f}s -> {output_path}")
    return total_seconds


def create_video(
    image_paths: list[Path],
    audio_path: Path,
    output_path: Path,
) -> None:
    """
    画像スライドショー + 音声 → MP4動画を ffmpeg で生成する。

    各画像の表示時間は音声の総長 / 画像枚数で均等割り当て。

    Args:
        image_paths: スライド画像のパスリスト（表示順）
        audio_path: 結合済みナレーション音声WAVのパス
        output_path: 出力MP4ファイルのパス
    """
    if not image_paths:
        raise ValueError("No images provided for video composition.")

    # 音声の長さを取得
    audio = AudioSegment.from_wav(str(audio_path))
    total_duration = len(audio) / 1000.0
    duration_per_image = total_duration / len(image_paths)

    logger.info(
        f"Composing video: {len(image_paths)} slides × {duration_per_image:.1f}s "
        f"= {total_duration:.1f}s total"
    )

    # ffmpeg concat用のテキストファイルを一時ファイルに書き出す
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".txt", delete=False
    ) as concat_file:
        concat_path = concat_file.name
        for img_path in image_paths:
            concat_file.write(f"file '{img_path.resolve()}'\n")
            concat_file.write(f"duration {duration_per_image:.4f}\n")
        # ffmpegのconcat demuxerは最後のファイルを2回書く必要がある
        concat_file.write(f"file '{image_paths[-1].resolve()}'\n")

    # ffmpegコマンド構築
    vf_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"format=yuv420p"
    )

    cmd = [
        "ffmpeg", "-y",
        "-f", "concat", "-safe", "0",
        "-i", concat_path,
        "-i", str(audio_path),
        "-vf", vf_filter,
        "-c:v", "libx264",
        "-preset", VIDEO_PRESET,
        "-crf", str(VIDEO_CRF),
        "-c:a", "aac",
        "-b:a", AUDIO_BITRATE,
        "-shortest",
        "-movflags", "+faststart",
        str(output_path),
    ]

    logger.info(f"Running ffmpeg: {' '.join(cmd)}")
    try:
        result = subprocess.run(
            cmd,
            check=True,
            capture_output=True,
            text=True,
        )
        logger.info(f"Video created: {output_path}")
    except subprocess.CalledProcessError as e:
        logger.error(f"ffmpeg failed:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
        raise
    finally:
        # 一時ファイルを削除
        Path(concat_path).unlink(missing_ok=True)


def compose_video(
    title_slide: Path,
    content_slides: list[Path],
    audio_segments: list[Path],
    output_dir: Path,
) -> Path:
    """
    STEP 7のメインエントリポイント。
    音声結合 → 動画合成 を実行して完成MP4のパスを返す。
    """
    merged_audio_path = output_dir / "audio" / "full_narration.wav"
    output_video_path = output_dir / "video" / "briefing.mp4"

    # 音声結合
    merge_audio_segments(audio_segments, merged_audio_path)

    # タイトルスライドを先頭に追加
    all_images = [title_slide] + content_slides

    # 動画合成
    create_video(all_images, merged_audio_path, output_video_path)

    return output_video_path

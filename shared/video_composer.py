"""
Phase 7: Video composition.
Combines image slideshow + narration audio -> MP4 (1920x1080, H.264, AAC).

Dependencies: ffmpeg (system install required), pydub.
"""

import subprocess
import logging
import tempfile
from pathlib import Path
from pydub import AudioSegment

logger = logging.getLogger(__name__)

# Silent gap inserted between audio segments (milliseconds)
GAP_BETWEEN_SEGMENTS_MS = 500

# Video encoding settings
VIDEO_WIDTH = 1920
VIDEO_HEIGHT = 1080
VIDEO_CRF = 23          # Quality (lower = higher quality / larger file)
VIDEO_PRESET = "medium"
AUDIO_BITRATE = "192k"


def merge_audio_segments(
    audio_paths: list[Path],
    output_path: Path,
    gap_ms: int = GAP_BETWEEN_SEGMENTS_MS,
) -> float:
    """
    Concatenate multiple WAV files into a single WAV.
    A silent gap of gap_ms milliseconds is inserted between segments.

    Returns:
        Total duration of the combined audio in seconds.
    """
    logger.info(f"Merging {len(audio_paths)} audio segment(s) ...")
    silence = AudioSegment.silent(duration=gap_ms)
    combined = AudioSegment.empty()

    for path in audio_paths:
        segment = AudioSegment.from_wav(str(path))
        combined += segment + silence

    combined.export(str(output_path), format="wav")
    total_seconds = len(combined) / 1000.0
    logger.info(f"Audio merge complete: {total_seconds:.1f}s -> {output_path}")
    return total_seconds


def create_video(
    image_paths: list[Path],
    audio_path: Path,
    output_path: Path,
) -> None:
    """
    Generate an MP4 video from an image slideshow + audio using ffmpeg.

    Single image:   -loop 1 method (avoids timestamp issues with long stills).
    Multiple images: concat demuxer method (equal duration per image).

    Args:
        image_paths: Ordered list of slide image paths.
        audio_path:  Combined narration WAV path.
        output_path: Output MP4 path.
    """
    if not image_paths:
        raise ValueError("No images provided for video composition.")

    audio = AudioSegment.from_wav(str(audio_path))
    total_duration = len(audio) / 1000.0

    logger.info(
        f"Composing video: {len(image_paths)} slide(s), {total_duration:.1f}s total."
    )

    vf_filter = (
        f"scale={VIDEO_WIDTH}:{VIDEO_HEIGHT}:force_original_aspect_ratio=decrease,"
        f"pad={VIDEO_WIDTH}:{VIDEO_HEIGHT}:(ow-iw)/2:(oh-ih)/2:color=black,"
        f"format=yuv420p"
    )

    if len(image_paths) == 1:
        # Single still: use -loop 1 to avoid concat demuxer timestamp conversion errors
        cmd = [
            "ffmpeg", "-y",
            "-loop", "1",
            "-i", str(image_paths[0].resolve()),
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
        logger.info(f"ffmpeg (single image / -loop 1): {' '.join(cmd)}")
        try:
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            logger.info(f"Video created: {output_path}")
        except subprocess.CalledProcessError as e:
            logger.error(f"ffmpeg failed:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
            raise

    else:
        # Multiple images: concat demuxer with equal time per image
        duration_per_image = total_duration / len(image_paths)
        concat_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".txt", delete=False
            ) as concat_file:
                concat_path = concat_file.name
                for img_path in image_paths:
                    concat_file.write(f"file '{img_path.resolve()}'\n")
                    concat_file.write(f"duration {duration_per_image:.4f}\n")
                # ffmpeg concat demuxer requires the last file to be listed twice
                concat_file.write(f"file '{image_paths[-1].resolve()}'\n")

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
            logger.info(f"ffmpeg (concat demuxer): {' '.join(cmd)}")
            try:
                subprocess.run(cmd, check=True, capture_output=True, text=True)
                logger.info(f"Video created: {output_path}")
            except subprocess.CalledProcessError as e:
                logger.error(f"ffmpeg failed:\nSTDOUT: {e.stdout}\nSTDERR: {e.stderr}")
                raise
        finally:
            if concat_path:
                Path(concat_path).unlink(missing_ok=True)


def compose_video(
    title_slide: Path,
    content_slides: list[Path],
    audio_segments: list[Path],
    output_dir: Path,
    output_video_path: Path | None = None,
    merged_audio_path: Path | None = None,
) -> Path:
    """
    Phase 7 main entry point.
    Merges audio then composes the final MP4; returns the output path.

    Args:
        output_video_path: Override output path. Defaults to output_dir/video/briefing.mp4.
        merged_audio_path: Override merged audio path. Defaults to output_dir/audio/full_narration.wav.
    """
    if merged_audio_path is None:
        merged_audio_path = output_dir / "audio" / "full_narration.wav"
    if output_video_path is None:
        output_video_path = output_dir / "video" / "briefing.mp4"

    merged_audio_path.parent.mkdir(parents=True, exist_ok=True)
    output_video_path.parent.mkdir(parents=True, exist_ok=True)

    merge_audio_segments(audio_segments, merged_audio_path)

    all_images = [title_slide] + content_slides
    create_video(all_images, merged_audio_path, output_video_path)

    return output_video_path

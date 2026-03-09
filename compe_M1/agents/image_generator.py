"""
STEP 5: インターリーブド画像生成
原稿の [IMAGE: ...] マーカーに対応した画像を生成する。

使用モデル: gemini-3.1-flash-image-preview
  ※ 仕様書の "gemini-2.5-flash-image" は存在しない。
     2026-03時点の正式モデル名は gemini-3.1-flash-image-preview。
"""

import re
import logging
from io import BytesIO
from pathlib import Path
from PIL import Image
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

IMAGE_MODEL = "gemini-3.1-flash-image-preview"

# 画像生成失敗時のプレースホルダー生成用
PLACEHOLDER_BG_COLOR = (15, 30, 60)   # dark navy
PLACEHOLDER_TEXT_COLOR = (200, 200, 200)


def _make_placeholder_image(description: str, output_path: Path) -> None:
    """画像生成失敗時に単色+テキストのプレースホルダー画像を生成する"""
    img = Image.new("RGB", (1920, 1080), color=PLACEHOLDER_BG_COLOR)
    img.save(str(output_path))
    logger.info(f"Placeholder image saved: {output_path}")


def _save_image_from_response(response, output_path: Path) -> bool:
    """レスポンスから画像パーツを探してファイルに保存する。成功したらTrueを返す"""
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            img = Image.open(BytesIO(part.inline_data.data))
            # 1920x1080 にリサイズ（アスペクト比を維持してパディング）
            img = _fit_to_1920x1080(img)
            img.save(str(output_path))
            logger.info(f"Image saved: {output_path} ({img.size})")
            return True
    return False


def _fit_to_1920x1080(img: Image.Image) -> Image.Image:
    """画像を 1920x1080 にフィットさせる（letterbox/pillarbox）"""
    target_w, target_h = 1920, 1080
    img.thumbnail((target_w, target_h), Image.LANCZOS)
    background = Image.new("RGB", (target_w, target_h), PLACEHOLDER_BG_COLOR)
    offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
    background.paste(img, offset)
    return background


def generate_title_slide(
    client: genai.Client,
    topic: dict,
    output_dir: Path,
) -> Path:
    """タイトルスライド画像を生成する"""
    output_path = output_dir / "slide_title.png"
    date_str = topic["timestamp"][:10]

    prompt = (
        f'Generate a professional breaking-news title card for a news broadcast.\n'
        f'Title: "GLOBAL RESPONSE: {topic["title"]}"\n'
        f'Subtitle: "Official Government Positions — {date_str}"\n'
        f'Style: Dark navy (#0F1E3C) background. Bold white title text with a red accent bar. '
        f'"STORYWIRE" branding watermark bottom-right. Clean, modern broadcast graphics. '
        f'16:9 aspect ratio. No people, no flags, purely typographic/graphic design.'
    )

    logger.info("Generating title slide...")
    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE"],
            ),
        )
        if not _save_image_from_response(response, output_path):
            logger.warning("No image part in title response, using placeholder.")
            _make_placeholder_image("Title Slide", output_path)
    except Exception as e:
        logger.error(f"Title slide generation failed: {e}")
        _make_placeholder_image("Title Slide", output_path)

    return output_path


def generate_content_images(
    client: genai.Client,
    script_text: str,
    output_dir: Path,
) -> list[Path]:
    """
    原稿内の [IMAGE: ...] マーカーをすべて抽出して画像を生成する。

    Returns:
        生成された画像ファイルパスのリスト（マーカーの順番通り）
    """
    markers = re.findall(r'\[IMAGE:\s*(.*?)\]', script_text)
    logger.info(f"Found {len(markers)} image markers in script.")

    generated_paths = []
    for i, description in enumerate(markers):
        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = (
            f"Generate a professional news broadcast-style infographic or visualization:\n"
            f"{description}\n\n"
            f"Style requirements:\n"
            f"- Dark navy (#0F1E3C) background\n"
            f"- White and light-colored text, clean typography\n"
            f"- Modern news graphics aesthetic (think BBC World, CNN International)\n"
            f"- Conceptual/infographic style — do NOT attempt realistic maps with precise borders\n"
            f"- 16:9 aspect ratio\n"
            f"- No photorealistic faces or people\n"
        )

        logger.info(f"Generating image {i+1}/{len(markers)}: {description[:60]}...")
        try:
            response = client.models.generate_content(
                model=IMAGE_MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["IMAGE"],
                ),
            )
            if not _save_image_from_response(response, output_path):
                logger.warning(f"No image part for slide {i}, using placeholder.")
                _make_placeholder_image(description, output_path)
        except Exception as e:
            logger.error(f"Image generation failed for slide {i}: {e}. Using placeholder.")
            _make_placeholder_image(description, output_path)

        generated_paths.append(output_path)

    return generated_paths

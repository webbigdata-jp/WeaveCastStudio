"""
STEP 5: 画像生成
- タイトルスライド
- コンテンツ画像（[IMAGE: ...] マーカー対応）
- 本日のニュース一覧画像
- クリップ用個別画像

使用モデル: gemini-3.1-flash-image-preview
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
PLACEHOLDER_BG_COLOR = (15, 30, 60)


def _make_placeholder_image(description: str, output_path: Path) -> None:
    img = Image.new("RGB", (1920, 1080), color=PLACEHOLDER_BG_COLOR)
    img.save(str(output_path))
    logger.info(f"プレースホルダー画像を保存: {output_path}")


def _save_image_from_response(response, output_path: Path) -> bool:
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            img = Image.open(BytesIO(part.inline_data.data))
            img = _fit_to_1920x1080(img)
            img.save(str(output_path))
            logger.info(f"画像を保存: {output_path}（{img.size}）")
            return True
    return False


def _fit_to_1920x1080(img: Image.Image) -> Image.Image:
    target_w, target_h = 1920, 1080
    img.thumbnail((target_w, target_h), Image.LANCZOS)
    background = Image.new("RGB", (target_w, target_h), PLACEHOLDER_BG_COLOR)
    offset = ((target_w - img.width) // 2, (target_h - img.height) // 2)
    background.paste(img, offset)
    return background


def _generate_image(client: genai.Client, prompt: str, output_path: Path, label: str) -> Path:
    """共通の画像生成処理。失敗時はプレースホルダーを生成。"""
    logger.info(f"{label} を生成中...")
    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        if not _save_image_from_response(response, output_path):
            logger.warning(f"{label}: 画像パーツなし。プレースホルダーを使用。")
            _make_placeholder_image(label, output_path)
    except Exception as e:
        logger.error(f"{label} 生成失敗: {e}")
        _make_placeholder_image(label, output_path)
    return output_path


def generate_title_slide(
    client: genai.Client, topics: list[dict], output_dir: Path, date_str: str,
) -> Path:
    """タイトルスライド画像を生成する。"""
    output_path = output_dir / "slide_title.png"
    prompt = (
        f'プロフェッショナルなニュース速報のタイトルカード画像を生成してください。\n'
        f'メインタイトル: 「国際情勢ブリーフィング」\n'
        f'日付: {date_str}\n'
        f'スタイル要件:\n'
        f'- ダークネイビー (#0F1E3C) の背景\n'
        f'- 白い太字のタイトルテキストと赤いアクセントバー\n'
        f'- 右下に "WeaveCast" ブランディングウォーターマーク\n'
        f'- クリーンでモダンな報道番組風グラフィック\n'
        f'- 16:9 アスペクト比\n'
        f'- 人物や国旗は描かない\n'
        f'- テキストはすべて日本語（"WeaveCast" のみ英語可）'
    )
    return _generate_image(client, prompt, output_path, "タイトルスライド")


def generate_news_lineup_image(
    client: genai.Client, topics: list[dict], output_dir: Path, date_str: str,
) -> Path:
    """
    「本日のニュース一覧」画像を生成する。
    M4で最初に表示する用途。
    """
    output_path = output_dir / "news_lineup.png"

    topic_lines = "\n".join(
        f"  {i+1}. {t['title']}" for i, t in enumerate(topics)
    )

    prompt = (
        f'プロフェッショナルなニュース番組の「本日のニュース一覧」画像を生成してください。\n\n'
        f'タイトル: 「本日のニュース」\n'
        f'日付: {date_str}\n\n'
        f'ニュース一覧:\n{topic_lines}\n\n'
        f'スタイル要件:\n'
        f'- ダークネイビー (#0F1E3C) の背景\n'
        f'- 各ニュース項目を番号付きで縦に並べる\n'
        f'- 白い太字テキスト、各項目の左に番号\n'
        f'- 上部に赤いアクセントバーと「本日のニュース」タイトル\n'
        f'- 右下に "WeaveCast" ウォーターマーク\n'
        f'- クリーンでモダンな報道番組風グラフィック\n'
        f'- 16:9 アスペクト比\n'
        f'- テキストはすべて日本語（"WeaveCast" のみ英語可）'
    )
    return _generate_image(client, prompt, output_path, "ニュース一覧画像")


def generate_content_images(
    client: genai.Client, script_text: str, output_dir: Path,
) -> list[Path]:
    """原稿内の [IMAGE: ...] マーカーから画像を生成する。"""
    markers = re.findall(r'\[IMAGE:\s*(.*?)\]', script_text)
    logger.info(f"原稿中に{len(markers)}個の画像マーカーを検出")

    generated = []
    for i, desc in enumerate(markers):
        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = (
            f"以下の内容を表現するニュース報道風インフォグラフィックを生成:\n{desc}\n\n"
            f"スタイル: ダークネイビー背景、白テキスト、NHK/BBC風グラフィック、16:9。\n"
            f"画像内のテキスト・ラベル・凡例はすべて日本語。英語は使わないこと。\n"
            f"右下に小さく WeaveCast ウォーターマーク。"
        )
        _generate_image(client, prompt, output_path, f"コンテンツ画像 {i+1}/{len(markers)}")
        generated.append(output_path)
    return generated


def generate_clip_image(
    client: genai.Client, clip: dict, output_path: Path,
) -> Path:
    """ショートクリップ用の画像を1枚生成する。"""
    topic_title = clip.get("topic_title", "ニュース")
    image_prompt = clip.get("image_prompt", "")

    prompt = (
        f"以下の内容を表現するニュース報道風インフォグラフィックを生成:\n"
        f"トピック: {topic_title}\n"
        f"{image_prompt}\n\n"
        f"スタイル: ダークネイビー (#0F1E3C) 背景、白テキスト、16:9。\n"
        f"画像内のテキスト・ラベルはすべて日本語。\n"
        f"右下に小さく WeaveCast ウォーターマーク。"
    )
    return _generate_image(client, prompt, output_path, f"クリップ画像「{topic_title}」")

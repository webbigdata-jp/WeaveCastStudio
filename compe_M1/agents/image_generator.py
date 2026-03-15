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
        f'メインタイトル: 「Middle East Crisis Monitor |」\n'
        f'日付: {date_str}\n'
        f'スタイル要件:\n'
        f'- ダークネイビー (#0F1E3C) の背景\n'
        f'- 白い太字のタイトルテキストと赤いアクセントバー\n'
        f'- 右下に "WeaveCast" ブランディングウォーターマーク\n'
        f'- クリーンでモダンな報道番組風グラフィック\n'
        f'- 16:9 アスペクト比\n'
        f'- 人物や国旗は描かない\n'
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


def _build_content_image_prompt(desc: str) -> str:
    """
    [IMAGE: ...] マーカーの description からカテゴリを判定し、
    幻覚を防ぐ制約付きプロンプトを生成する。
    """
    desc_lower = desc.lower()

    common_style = (
        "\n\n【共通スタイル要件】\n"
        "- ダークネイビー (#0F1E3C) 背景、白テキスト\n"
        "- NHK/BBC風の報道番組グラフィック\n"
        "- 16:9 アスペクト比\n"
        "- 画像内のテキスト・ラベル・凡例はすべて日本語（固有名詞・WeaveCast除く）\n"
        "- 右下に小さく WeaveCast ウォーターマーク\n"
        "- フラットデザイン・ダイアグラムスタイルのみ\n"
        "\n【絶対禁止】\n"
        "- 写実的な人物・建物・風景・兵器の描写\n"
        "- 画像生成AIが勝手に作った架空の数値・統計データ\n"
        "- フォトリアリスティックなスタイル\n"
        "- 感情的・扇情的なイメージ\n"
    )

    if "map" in desc_lower:
        prompt = (
            f"以下の内容を表現する報道番組風の地図グラフィックを生成してください。\n"
            f"内容: {desc}\n"
            f"\n地図の要件:\n"
            f"- シンプルな地図（国境線、海岸線、主要都市名のみ）\n"
            f"- 関連地域をハイライト（赤またはオレンジ）\n"
            f"- 矢印やマーカーで重要なポイントを示す\n"
            f"- 地名ラベルは日本語\n"
        )
    elif "relationship" in desc_lower or "position" in desc_lower:
        prompt = (
            f"以下の内容を表現する報道番組風の関係図・立場対比グラフィックを生成してください。\n"
            f"内容: {desc}\n"
            f"\n関係図の要件:\n"
            f"- 各国・組織を丸または四角のノードで表現\n"
            f"- 賛成=青系、反対=赤系、中立=灰色系 で色分け\n"
            f"- ノード間の関係を矢印や線で示す\n"
            f"- 各ノードに国名と立場の一言要約を日本語で表示\n"
            f"- 数値は使わない（原文に数値がない限り）\n"
        )
    elif "timeline" in desc_lower:
        prompt = (
            f"以下の内容を表現する報道番組風のタイムラインを生成してください。\n"
            f"内容: {desc}\n"
            f"\nタイムラインの要件:\n"
            f"- 横方向または縦方向の時系列レイアウト\n"
            f"- 各イベントをドット＋ラベルで表現\n"
            f"- 日付とイベント名を日本語で表示\n"
            f"- 重要イベントを赤アクセントで強調\n"
        )
    elif "comparison" in desc_lower or "table" in desc_lower:
        prompt = (
            f"以下の内容を表現する報道番組風の対比表グラフィックを生成してください。\n"
            f"内容: {desc}\n"
            f"\n対比表の要件:\n"
            f"- 左右または上下で2者を対比するレイアウト\n"
            f"- 各項目を箇条書きで簡潔に表示\n"
            f"- 対立する立場は赤と青で色分け\n"
            f"- テキストは日本語\n"
        )
    elif "key points" in desc_lower or "summary" in desc_lower:
        prompt = (
            f"以下の内容を表現する報道番組風のキーポイント要約グラフィックを生成してください。\n"
            f"内容: {desc}\n"
            f"\n要約グラフィックの要件:\n"
            f"- 箇条書きまたはアイコン付きリストで各ポイントを表示\n"
            f"- 各ポイントに番号またはアイコン\n"
            f"- テキストは日本語で簡潔に\n"
        )
    else:
        # デフォルト: 抽象的なインフォグラフィック
        prompt = (
            f"以下の内容を表現する報道番組風のインフォグラフィックを生成してください。\n"
            f"内容: {desc}\n"
            f"\nインフォグラフィックの要件:\n"
            f"- アイコン、矢印、テキストボックスを使った図解\n"
            f"- テキスト中心のレイアウト（写実的な描写は禁止）\n"
            f"- テキストは日本語\n"
        )

    return prompt + common_style


def generate_content_images(
    client: genai.Client, script_text: str, output_dir: Path,
) -> list[Path]:
    """原稿内の [IMAGE: ...] マーカーから画像を生成する。"""
    markers = re.findall(r'\[IMAGE:\s*(.*?)\]', script_text)
    logger.info(f"原稿中に{len(markers)}個の画像マーカーを検出")

    generated = []
    for i, desc in enumerate(markers):
        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = _build_content_image_prompt(desc)
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
        f"以下のニューストピックのタイトルカード画像を生成してください。\n"
        f"トピック: {topic_title}\n"
        f"{image_prompt}\n\n"
        f"【スタイル要件】\n"
        f"- ダークネイビー (#0F1E3C) 背景、白テキスト、16:9\n"
        f"- トピック名を大きく日本語で表示\n"
        f"- トピックに関連する抽象的なアイコンやシンボル（地図アイコン、国旗アイコン等）を配置\n"
        f"- フラットデザイン・ダイアグラムスタイルのみ\n"
        f"- 右下に小さく WeaveCast ウォーターマーク\n"
        f"\n【絶対禁止】\n"
        f"- 写実的な人物・建物・風景・兵器の描写\n"
        f"- 架空の数値・統計データ\n"
        f"- フォトリアリスティックなスタイル\n"
        f"- 感情的・扇情的なイメージ\n"
    )
    return _generate_image(client, prompt, output_path, f"クリップ画像「{topic_title}」")

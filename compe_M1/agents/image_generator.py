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
IMAGE_MODEL = "gemini-3-pro-image-preview"

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
        f'Generate a professional news broadcast title card image.\n'
        f'Main title text: "Middle East Crisis Monitor"\n'
        f'Date text: {date_str}\n'
        f'Style requirements:\n'
        f'- Dark navy (#0F1E3C) background\n'
        f'- White bold title text with red accent bar\n'
        f'- "WeaveCast" branding watermark in bottom-right\n'
        f'- Clean, modern broadcast news graphic\n'
        f'- 16:9 aspect ratio\n'
        f'- Flat design only: NO people, NO flags, NO photographs\n'
        f'- Use only geometric shapes and text\n'
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
        f'Generate a professional news broadcast "Today\'s Headlines" lineup image.\n\n'
        f'Title text: "Today\'s News"\n'
        f'Date: {date_str}\n\n'
        f'Headlines:\n{topic_lines}\n\n'
        f'Style requirements:\n'
        f'- Dark navy (#0F1E3C) background\n'
        f'- Each headline numbered vertically\n'
        f'- White bold text with number on the left of each item\n'
        f'- Red accent bar at top with title\n'
        f'- "WeaveCast" watermark in bottom-right\n'
        f'- Clean, modern broadcast news graphic\n'
        f'- 16:9 aspect ratio\n'
        f'- Flat design only: NO people, NO photographs\n'
        f'- Headline text can be in Japanese (as provided above)\n'
    )
    return _generate_image(client, prompt, output_path, "ニュース一覧画像")


_BANNED_KEYWORDS = [
    "portrait", "photo", "image of", "picture of",
    "soldiers", "children", "person", "people", "face", "body",
    "building", "buildings", "school", "schools",
    "aircraft", "missile", "missiles", "weapon", "weapons", "gun", "tank",
    "destroyed", "damaged", "ruins", "rubble", "wreckage",
    "crying", "caution tape", "warning", "explosion", "fire", "blood",
    "corpse", "dead", "injury", "wound", "victim",
    "firing", "bombing", "attack",
]


def _sanitize_description(desc: str) -> str:
    """
    写実的・扇情的なキーワードを除去し、安全な description に変換する。
    除去後に内容が崩壊した場合は、固有名詞を抽出してフォールバックする。
    """
    sanitized = desc
    for keyword in _BANNED_KEYWORDS:
        # ワード境界でマッチ（"building" が "rebuilding" にヒットしないように）
        pattern = re.compile(r'\b' + re.escape(keyword) + r'(?:s|ing|ed)?\b', re.IGNORECASE)
        sanitized = pattern.sub("", sanitized)
    # 余分な空白・記号を整理
    sanitized = re.sub(r"['\"]s\b", "", sanitized)  # 所有格の残骸 "'s" を除去
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    # フォールバック判定: 意味のある英単語（2文字以上、冠詞・前置詞除外）が3語未満なら崩壊
    stopwords = {"a", "an", "the", "of", "on", "in", "at", "to", "and", "or",
                 "with", "for", "by", "from", "near", "about", "into", "over"}
    meaningful_words = [w for w in sanitized.split() if len(w) > 1 and w.lower() not in stopwords]
    if len(meaningful_words) < 3:
        # 元のdescから固有名詞（大文字始まりで2文字以上）を抽出
        proper_nouns = re.findall(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)*\b', desc)
        # "Image", "Portrait" など禁止語由来の固有名詞を除外
        banned_proper = {"Image", "Portrait", "Photo", "Picture", "Soldiers", "Children",
                         "Building", "School", "Aircraft", "Damaged", "Destroyed"}
        proper_nouns = [n for n in proper_nouns if n not in banned_proper]
        if proper_nouns:
            sanitized = "Key topics: " + ", ".join(dict.fromkeys(proper_nouns))  # 重複排除
        else:
            sanitized = "General news summary"
        logger.info(f"サニタイズ後にフォールバック: '{desc}' → '{sanitized}'")

    return sanitized


def _parse_image_type(desc: str) -> tuple[str, str]:
    """
    [IMAGE: TYPE: description] 形式からTYPEとdescriptionを分離する。
    TYPE が認識できない場合は ("KEYPOINTS", 元のdesc) を返す。
    """
    valid_types = {"MAP", "STANCE", "TIMELINE", "VERSUS", "KEYPOINTS"}
    match = re.match(r'^(MAP|STANCE|TIMELINE|VERSUS|KEYPOINTS)\s*:\s*(.+)$', desc.strip(), re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    # 旧形式の互換: "Map showing ..." → MAP
    desc_lower = desc.lower()
    if desc_lower.startswith("map ") or "map showing" in desc_lower:
        return "MAP", desc
    if "timeline" in desc_lower:
        return "TIMELINE", desc
    if "comparison" in desc_lower or " vs " in desc_lower or "versus" in desc_lower:
        return "VERSUS", desc
    if "key points" in desc_lower or "summary" in desc_lower or "keypoints" in desc_lower:
        return "KEYPOINTS", desc
    if "relationship" in desc_lower or "position" in desc_lower or "stance" in desc_lower:
        return "STANCE", desc
    # デフォルト: KEYPOINTS（最も安全）
    return "KEYPOINTS", desc


def _build_content_image_prompt(desc: str) -> str:
    """
    [IMAGE: ...] マーカーの description からTYPEを解析し、
    幻覚を防ぐ制約付きプロンプトを生成する。
    """
    image_type, raw_desc = _parse_image_type(desc)
    clean_desc = _sanitize_description(raw_desc)

    common_style = (
        "\n\n=== STYLE RULES (MUST FOLLOW) ===\n"
        "- Background: dark navy (#0F1E3C)\n"
        "- Text color: white\n"
        "- Aspect ratio: 16:9\n"
        "- All text labels in English (except the WeaveCast watermark)\n"
        "- Small 'WeaveCast' watermark in bottom-right corner\n"
        "- Flat design, diagram/infographic style ONLY\n"
        "- Clean, modern broadcast news aesthetic (NHK/BBC style)\n"
        "\n=== ABSOLUTE PROHIBITIONS ===\n"
        "- NO photorealistic imagery of any kind\n"
        "- NO depictions of people, faces, bodies, or human figures\n"
        "- NO buildings, vehicles, weapons, or physical objects rendered realistically\n"
        "- NO invented numbers, statistics, percentages, or data that are not in the description\n"
        "- NO emotional or sensational imagery\n"
        "- NO photographs or photo-like renderings\n"
        "- Use ONLY geometric shapes, icons, arrows, text boxes, and abstract symbols\n"
    )

    if image_type == "MAP":
        prompt = (
            f"Generate a broadcast-style schematic MAP graphic.\n"
            f"Content: {clean_desc}\n\n"
            f"MAP requirements:\n"
            f"- Simple schematic map with country outlines and coastlines\n"
            f"- Highlight relevant regions in red or orange\n"
            f"- Use arrows or markers to indicate key points\n"
            f"- Country and city labels in ENGLISH\n"
            f"- Do NOT draw any people, vehicles, or buildings on the map\n"
            f"- Do NOT add any numbers or statistics\n"
        )
    elif image_type == "STANCE":
        prompt = (
            f"Generate a broadcast-style STANCE DIAGRAM showing different countries' positions.\n"
            f"Content: {clean_desc}\n\n"
            f"Stance diagram requirements:\n"
            f"- Each country/organization as a labeled box or circle node\n"
            f"- Color code: supportive=blue, opposed=red, neutral=gray, cautious=yellow\n"
            f"- Arrows or lines showing relationships between positions\n"
            f"- Each node shows: country name + one-line stance summary in ENGLISH\n"
            f"- Do NOT invent any quotes or numbers\n"
            f"- Do NOT draw any people or realistic objects\n"
        )
    elif image_type == "TIMELINE":
        prompt = (
            f"Generate a broadcast-style TIMELINE graphic.\n"
            f"Content: {clean_desc}\n\n"
            f"Timeline requirements:\n"
            f"- Horizontal or vertical timeline layout\n"
            f"- Each event as a dot + label\n"
            f"- Dates in format: 'Mar 1' or '2026-03-01' (Western calendar, NO Japanese era)\n"
            f"- Event descriptions in ENGLISH\n"
            f"- Highlight critical events with red accent\n"
            f"- Do NOT add events that are not in the description\n"
            f"- Do NOT draw any people or realistic objects\n"
        )
    elif image_type == "VERSUS":
        prompt = (
            f"Generate a broadcast-style VERSUS comparison graphic.\n"
            f"Content: {clean_desc}\n\n"
            f"Comparison requirements:\n"
            f"- Left vs Right layout with clear divider\n"
            f"- Each side: country/entity name at top, bullet points below\n"
            f"- Opposing positions in red and blue\n"
            f"- Text in ENGLISH\n"
            f"- Do NOT invent any quotes, numbers, or facts\n"
            f"- Do NOT draw any people or realistic objects\n"
        )
    elif image_type == "KEYPOINTS":
        prompt = (
            f"Generate a broadcast-style KEY POINTS summary graphic.\n"
            f"Content: {clean_desc}\n\n"
            f"Key points requirements:\n"
            f"- Numbered list or icon-based layout\n"
            f"- Each point with a simple geometric icon and text\n"
            f"- Text in ENGLISH\n"
            f"- Do NOT invent any numbers or data\n"
            f"- Do NOT draw any people or realistic objects\n"
        )
    else:
        prompt = (
            f"Generate a broadcast-style KEY POINTS summary graphic.\n"
            f"Content: {clean_desc}\n\n"
            f"Requirements:\n"
            f"- Abstract icons, arrows, and text boxes only\n"
            f"- Text in ENGLISH\n"
            f"- Do NOT draw any people, buildings, or realistic objects\n"
            f"- Do NOT invent any numbers or data\n"
        )

    return prompt + common_style


def generate_content_images(
    client: genai.Client, script_text: str, output_dir: Path,
) -> list[Path]:
    """[後方互換] 原稿内の [IMAGE: ...] マーカーから画像を生成する。"""
    markers = re.findall(r'\[IMAGE:\s*(.*?)\]', script_text)
    if not markers:
        logger.info("原稿中に画像マーカーなし（generate_briefing_images を使用してください）")
        return []
    logger.info(f"原稿中に{len(markers)}個の画像マーカーを検出")

    generated = []
    for i, desc in enumerate(markers):
        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = _build_content_image_prompt(desc)
        _generate_image(client, prompt, output_path, f"コンテンツ画像 {i+1}/{len(markers)}")
        generated.append(output_path)
    return generated


# ── 共通スタイル（日本語プロンプト版） ──

_COMMON_STYLE_JA = (
    "\n\n【共通スタイル要件】\n"
    "- 背景: ダークネイビー (#0F1E3C)\n"
    "- テキスト色: 白\n"
    "- アスペクト比: 16:9\n"
    "- 右下に小さく「WeaveCast」ウォーターマーク\n"
    "- フラットデザイン・ダイアグラムスタイルのみ\n"
    "- クリーンでモダンな報道番組風（NHK/BBC風）\n"
    "\n【絶対禁止】\n"
    "- 写実的な人物・顔・体・建物・車両・兵器の描写\n"
    "- プロンプトに記載されていない数値・統計・パーセンテージの捏造\n"
    "- 感情的・扇情的なイメージ\n"
    "- 写真やフォトリアリスティックな描写\n"
    "- 幾何学的図形・アイコン・矢印・テキストボックス・抽象シンボルのみ使用すること\n"
)


def _choose_image_type(topic: dict, section: dict) -> str:
    """
    トピックとセクションの内容から最適な画像タイプを選択する。
    """
    title = (section.get("topic", "") + " " + topic.get("title", "")).lower()
    tags = [t.lower() for t in topic.get("tags", [])]
    countries = section.get("countries", [])

    # 地理的キーワード → MAP
    geo_keywords = ["海峡", "hormuz", "封鎖", "blockade", "shipping", "地域", "region"]
    if any(kw in title for kw in geo_keywords) or "shipping" in tags:
        return "MAP"

    # 軍事・損失系 → KEYPOINTS（STANCEより先に判定）
    if any(kw in tags for kw in ["military", "damage", "war"]):
        return "KEYPOINTS"

    # 人道系 → KEYPOINTS（STANCEより先に判定）
    if any(kw in tags for kw in ["humanitarian", "civilian"]):
        return "KEYPOINTS"

    # 対立構造が明確 → STANCE
    if len(countries) >= 3:
        stances = [c.get("stance", "") for c in countries]
        if "opposed" in stances and "supportive" in stances:
            return "STANCE"

    # 政治・指導者系 → VERSUS or KEYPOINTS
    if any(kw in tags for kw in ["politics", "leadership"]):
        if len(countries) >= 2:
            return "VERSUS"
        return "KEYPOINTS"

    # デフォルト: 国が多ければSTANCE、少なければKEYPOINTS
    if len(countries) >= 3:
        return "STANCE"
    return "KEYPOINTS"


def _build_briefing_image_prompt(
    topic: dict, section: dict, image_type: str, date_str: str,
) -> str:
    """
    briefing_dataのセクション情報から画像生成プロンプトを組み立てる。
    """
    topic_title = section.get("topic", topic.get("title", "ニュース"))
    summary = section.get("summary", "")
    countries = section.get("countries", [])
    analysis = section.get("analysis", {})

    if image_type == "MAP":
        # 関連する国名をラベルとして抽出
        country_names = [c.get("country", "") for c in countries if c.get("country")]
        labels_list = "\n".join(f"  - {name}" for name in country_names[:6])
        prompt = (
            f"報道番組風の地図グラフィックを生成してください。\n"
            f"タイトル: 「{topic_title}」（画像上部に日本語で大きく表示）\n\n"
            f"【描画する要素（これだけを描くこと）】\n"
            f"1. シンプルな模式地図（国境線と海岸線のみ）\n"
            f"2. 関連地域（トピックの中心地）を赤またはオレンジでハイライト\n"
            f"3. 以下の国名ラベルのみ表示（日本語）:\n{labels_list}\n"
            f"4. トピックの中心地点に赤い丸マーカーを1つ配置\n\n"
            f"【絶対に描かないこと】\n"
            f"- 矢印（国と国を結ぶ矢印、攻撃を示す矢印などすべて禁止）\n"
            f"- 「STRANDED SHIPS」「OIL PRICES」など追加テキスト\n"
            f"- 船舶、軍艦、飛行機などのアイコン\n"
            f"- プロンプトに書かれていないラベルや情報\n"
            f"- 数値、統計、価格情報\n"
        )

    elif image_type == "STANCE":
        # 各国の立場を整理（矢印なしのグリッドレイアウト用）
        stance_entries = []
        for c in countries[:6]:
            name = c.get("country", "?")
            position = c.get("position", "")
            stance = c.get("stance", "neutral")
            # stance → 色指定
            color_map = {
                "supportive": "青", "opposed": "赤",
                "neutral": "灰色", "cautious": "黄色",
            }
            color = color_map.get(stance, "灰色")
            stance_entries.append(f"  - {name}（{color}の丸）: 「{position}」")
        stances_text = "\n".join(stance_entries) if stance_entries else "  情報なし"

        prompt = (
            f"報道番組風の各国立場対比図を生成してください。\n"
            f"タイトル: 「{topic_title}」（画像上部に日本語で大きく表示）\n\n"
            f"【描画する要素（これだけを描くこと）】\n"
            f"以下の各国を丸いノードで表現し、それぞれの立場を日本語テキストで表示:\n"
            f"{stances_text}\n\n"
            f"【レイアウト】\n"
            f"- 各国のノードを横並びまたは格子状に配置\n"
            f"- 各ノードの下にその国の立場テキストを表示\n"
            f"- トピック名を中央上部にタイトルとして配置\n\n"
            f"【絶対に描かないこと】\n"
            f"- 国と国を結ぶ矢印（矢印は一切使わない）\n"
            f"- 国旗の描写（シンプルな丸ノードのみ）\n"
            f"- プロンプトに書かれていない国や情報\n"
            f"- 数値、統計、引用文\n"
        )

    elif image_type == "VERSUS":
        # 2者対比（最初の2国）
        c1 = countries[0] if len(countries) > 0 else {"country": "A国", "position": ""}
        c2 = countries[1] if len(countries) > 1 else {"country": "B国", "position": ""}
        prompt = (
            f"報道番組風の二者対比グラフィックを生成してください。\n"
            f"タイトル: 「{topic_title}」（画像上部に日本語で大きく表示）\n\n"
            f"【描画する要素（これだけを描くこと）】\n"
            f"左側（赤）: {c1.get('country', 'A国')}\n"
            f"  立場: 「{c1.get('position', '')}」\n"
            f"右側（青）: {c2.get('country', 'B国')}\n"
            f"  立場: 「{c2.get('position', '')}」\n\n"
            f"【レイアウト】\n"
            f"- 左右に分けたレイアウト（中央に VS の文字と区切り線）\n"
            f"- 各側に国名と立場テキストを日本語で表示\n\n"
            f"【絶対に描かないこと】\n"
            f"- 国と国を結ぶ矢印\n"
            f"- 国旗の写実的描写\n"
            f"- プロンプトに書かれていない情報\n"
        )

    elif image_type == "KEYPOINTS":
        # 要点まとめ
        points = []
        # analysis から合意点・相違点を取得
        consensus = analysis.get("consensus_points", [])
        divergence = analysis.get("divergence_points", [])
        for p in consensus[:2]:
            points.append(f"  {len(points)+1}. {p}")
        for p in divergence[:2]:
            points.append(f"  {len(points)+1}. {p}")
        # 不足なら各国の position から補完
        if len(points) < 3:
            for c in countries[:3]:
                points.append(f"  {len(points)+1}. {c.get('country', '')}: {c.get('position', '')}")
        points_text = "\n".join(points[:5]) if points else "  情報なし"

        prompt = (
            f"報道番組風のキーポイント要約グラフィックを生成してください。\n"
            f"タイトル: 「{topic_title}」（画像上部に日本語で大きく表示）\n\n"
            f"【描画する要素（これだけを描くこと）】\n"
            f"以下の要点を番号付きリストで表示:\n{points_text}\n\n"
            f"【レイアウト】\n"
            f"- 各ポイントにシンプルな幾何学アイコン（丸、四角など）とテキスト\n"
            f"- テキストは日本語\n\n"
            f"【絶対に描かないこと】\n"
            f"- プロンプトに書かれていない要点やデータの追加\n"
            f"- 矢印や関係線\n"
            f"- 写実的なイラスト\n"
            f"- 数値・統計の捏造\n"
        )

    else:
        prompt = (
            f"報道番組風の要約グラフィックを生成してください。\n"
            f"タイトル: 「{topic_title}」（画像上部に日本語で大きく表示）\n"
            f"抽象的なアイコンと日本語テキストのみで構成すること。\n"
            f"プロンプトに書かれていない情報は追加しないこと。\n"
        )

    return prompt + _COMMON_STYLE_JA


def generate_briefing_images(
    client: genai.Client,
    briefing_data: dict,
    topics: list[dict],
    output_dir: Path,
    date_str: str,
) -> list[Path]:
    """
    briefing_data（構造化JSON）からトピックごとに画像を生成する。

    [IMAGE:] マーカーに依存せず、briefing_data の各セクションの
    構造化情報（国名、立場、分析など）を直接使って画像プロンプトを組み立てる。

    Args:
        client: 初期化済みの genai.Client
        briefing_data: STEP 3で生成した構造化データ
        topics: topics.yaml のトピック定義リスト
        output_dir: 画像保存先ディレクトリ
        date_str: 日付文字列（例: "March 15, 2026"）
    Returns:
        生成した画像パスのリスト
    """
    sections = briefing_data.get("briefing_sections", [])
    logger.info(f"briefing_dataから{len(sections)}セクションの画像を生成します")

    generated = []
    for i, section in enumerate(sections):
        # 対応するtopics.yamlのトピック情報を取得
        topic = topics[i] if i < len(topics) else {}

        image_type = _choose_image_type(topic, section)
        topic_title = section.get("topic", topic.get("title", f"トピック{i+1}"))
        logger.info(f"  セクション{i+1}/{len(sections)} 「{topic_title}」 → {image_type}")

        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = _build_briefing_image_prompt(topic, section, image_type, date_str)
        _generate_image(client, prompt, output_path, f"ブリーフィング画像 {i+1}/{len(sections)} ({image_type})")
        generated.append(output_path)

    logger.info(f"ブリーフィング画像生成完了: {len(generated)}枚")
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

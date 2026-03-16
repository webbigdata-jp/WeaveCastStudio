"""
Phase 5: Image generation.
- Title slide
- Content images (responds to [IMAGE: ...] markers)
- Today's news lineup image
- Per-clip images

Model: gemini-3-pro-image-preview
Output language for captions is controlled by LANGUAGE in .env.
"""

import logging
import re
from io import BytesIO
from pathlib import Path

from google import genai
from google.genai import types
from PIL import Image

from .language_utils import get_language_config

logger = logging.getLogger(__name__)

IMAGE_MODEL = "gemini-3.1-flash-image-preview"
IMAGE_MODEL = "gemini-3-pro-image-preview"

PLACEHOLDER_BG_COLOR = (15, 30, 60)


def _make_placeholder_image(description: str, output_path: Path) -> None:
    img = Image.new("RGB", (1920, 1080), color=PLACEHOLDER_BG_COLOR)
    img.save(str(output_path))
    logger.info(f"Placeholder image saved: {output_path}")


def _save_image_from_response(response, output_path: Path) -> bool:
    for part in response.candidates[0].content.parts:
        if part.inline_data is not None:
            img = Image.open(BytesIO(part.inline_data.data))
            img = _fit_to_1920x1080(img)
            img.save(str(output_path))
            logger.info(f"Image saved: {output_path} ({img.size})")
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
    """Shared image generation logic. Falls back to a placeholder on failure."""
    logger.info(f"Generating {label} ...")
    try:
        response = client.models.generate_content(
            model=IMAGE_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(response_modalities=["IMAGE"]),
        )
        if not _save_image_from_response(response, output_path):
            logger.warning(f"{label}: no image part in response. Using placeholder.")
            _make_placeholder_image(label, output_path)
    except Exception as e:
        logger.error(f"{label} generation failed: {e}")
        _make_placeholder_image(label, output_path)
    return output_path


def generate_title_slide(
    client: genai.Client, topics: list[dict], output_dir: Path, date_str: str,
) -> Path:
    """Generate the title slide image."""
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
    return _generate_image(client, prompt, output_path, "title slide")


def generate_news_lineup_image(
    client: genai.Client, topics: list[dict], output_dir: Path, date_str: str,
) -> Path:
    """
    Generate the "Today's Headlines" lineup image.
    Used as the first display item in M4.
    """
    lang = get_language_config()
    output_path = output_dir / "news_lineup.png"

    topic_lines = "\n".join(
        f"  {i+1}. {t.get('title_target_lang', t.get('title_en', ''))}"
        for i, t in enumerate(topics)
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
        f'- Headline text is in {lang.prompt_lang} (as provided above)\n'
    )
    return _generate_image(client, prompt, output_path, "news lineup image")


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
    Remove realistic / sensational keywords from a description.
    If the result becomes too short, fall back to extracting proper nouns.
    """
    sanitized = desc
    for keyword in _BANNED_KEYWORDS:
        # Match on word boundary so "building" doesn't hit "rebuilding"
        pattern = re.compile(r'\b' + re.escape(keyword) + r'(?:s|ing|ed)?\b', re.IGNORECASE)
        sanitized = pattern.sub("", sanitized)
    sanitized = re.sub(r"['\"]s\b", "", sanitized)   # remove orphaned possessives "'s"
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

    # Fallback: fewer than 3 meaningful words -> extract proper nouns from original
    stopwords = {"a", "an", "the", "of", "on", "in", "at", "to", "and", "or",
                 "with", "for", "by", "from", "near", "about", "into", "over"}
    meaningful_words = [w for w in sanitized.split() if len(w) > 1 and w.lower() not in stopwords]
    if len(meaningful_words) < 3:
        proper_nouns = re.findall(r'\b[A-Z][a-z]{2,}(?:\s+[A-Z][a-z]+)*\b', desc)
        banned_proper = {"Image", "Portrait", "Photo", "Picture", "Soldiers", "Children",
                         "Building", "School", "Aircraft", "Damaged", "Destroyed"}
        proper_nouns = [n for n in proper_nouns if n not in banned_proper]
        if proper_nouns:
            sanitized = "Key topics: " + ", ".join(dict.fromkeys(proper_nouns))
        else:
            sanitized = "General news summary"
        logger.info(f"Sanitize fallback: '{desc}' -> '{sanitized}'")

    return sanitized


def _parse_image_type(desc: str) -> tuple[str, str]:
    """
    Parse image type and description from '[IMAGE: TYPE: description]' format.
    Returns ("KEYPOINTS", original_desc) if the type is not recognised.
    valid_types = {"MAP", "STANCE", "TIMELINE", "VERSUS", "KEYPOINTS"}
    """
    
    match = re.match(r'^(MAP|STANCE|TIMELINE|VERSUS|KEYPOINTS)\s*:\s*(.+)$', desc.strip(), re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2).strip()
    # Legacy compatibility: "Map showing ..." -> MAP
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
    # Default: KEYPOINTS (safest option)
    return "KEYPOINTS", desc


def _build_content_image_prompt(desc: str) -> str:
    """
    Build a hallucination-safe image prompt from an [IMAGE: ...] marker description.
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
    """[Backward-compat] Generate images from [IMAGE: ...] markers in the script."""
    markers = re.findall(r'\[IMAGE:\s*(.*?)\]', script_text)
    if not markers:
        logger.info("No image markers found in script (use generate_briefing_images instead).")
        return []
    logger.info(f"Found {len(markers)} image marker(s) in script.")

    generated = []
    for i, desc in enumerate(markers):
        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = _build_content_image_prompt(desc)
        _generate_image(client, prompt, output_path, f"content image {i+1}/{len(markers)}")
        generated.append(output_path)
    return generated


# ── Common style block (used with target-language prompts) ──────────────────

_COMMON_STYLE = (
    "\n\n[Common style requirements]\n"
    "- Background: dark navy (#0F1E3C)\n"
    "- Text color: white\n"
    "- Aspect ratio: 16:9\n"
    "- Small 'WeaveCast' watermark in bottom-right corner\n"
    "- Flat design, diagram style only\n"
    "- Clean, modern broadcast news aesthetic (NHK/BBC style)\n"
    "\n[Absolute prohibitions]\n"
    "- NO photorealistic people, faces, bodies, buildings, vehicles, or weapons\n"
    "- NO invented numbers, statistics, or percentages not present in the prompt\n"
    "- NO emotional or sensational imagery\n"
    "- NO photographs or photo-like renderings\n"
    "- Use ONLY geometric shapes, icons, arrows, text boxes, and abstract symbols\n"
)


def _choose_image_type(topic: dict, section: dict) -> str:
    """Select the most appropriate image type for a topic/section pair."""
    title = (section.get("topic", "") + " " + topic.get("title_en", "")).lower()
    tags = [t.lower() for t in topic.get("tags", [])]
    countries = section.get("countries", [])

    # Geographic keywords -> MAP
    geo_keywords = ["hormuz", "blockade", "shipping", "strait", "region", "sea"]
    if any(kw in title for kw in geo_keywords) or "shipping" in tags:
        return "MAP"

    # Military / damage -> KEYPOINTS (checked before STANCE)
    if any(kw in tags for kw in ["military", "damage", "war"]):
        return "KEYPOINTS"

    # Humanitarian / civilian -> KEYPOINTS (checked before STANCE)
    if any(kw in tags for kw in ["humanitarian", "civilian"]):
        return "KEYPOINTS"

    # Clear opposing sides -> STANCE
    if len(countries) >= 3:
        stances = [c.get("stance", "") for c in countries]
        if "opposed" in stances and "supportive" in stances:
            return "STANCE"

    # Political / leadership -> VERSUS or KEYPOINTS
    if any(kw in tags for kw in ["politics", "leadership"]):
        if len(countries) >= 2:
            return "VERSUS"
        return "KEYPOINTS"

    # Default: STANCE for many countries, KEYPOINTS otherwise
    if len(countries) >= 3:
        return "STANCE"
    return "KEYPOINTS"


def _build_briefing_image_prompt(
    topic: dict, section: dict, image_type: str, date_str: str,
) -> str:
    """Build an image generation prompt from structured briefing_data section."""
    lang = get_language_config()

    topic_title = section.get("topic", topic.get("title_en", "News"))
    #summary = section.get("summary", "")
    countries = section.get("countries", [])
    analysis = section.get("analysis", {})

    if image_type == "MAP":
        country_names = [c.get("country", "") for c in countries if c.get("country")]
        labels_list = "\n".join(f"  - {name}" for name in country_names[:6])
        prompt = (
            f"Generate a broadcast-style schematic map graphic.\n"
            f"Title: '{topic_title}' (displayed prominently at the top in {lang.prompt_lang})\n\n"
            f"[Elements to draw — ONLY these]\n"
            f"1. Simple schematic map (country borders and coastlines only)\n"
            f"2. Highlight the relevant region in red or orange\n"
            f"3. Show ONLY the following country labels (in {lang.prompt_lang}):\n{labels_list}\n"
            f"4. Place one red circle marker at the focal point of the topic\n\n"
            f"[NEVER draw]\n"
            f"- Arrows connecting countries (no attack arrows, no directional arrows)\n"
            f"- Extra text labels not listed above\n"
            f"- Ship, aircraft, or military icons\n"
            f"- Numbers, statistics, or price data\n"
        )

    elif image_type == "STANCE":
        stance_entries = []
        for c in countries[:6]:
            name = c.get("country", "?")
            position = c.get("position", "")
            stance = c.get("stance", "neutral")
            color_map = {
                "supportive": "blue", "opposed": "red",
                "neutral": "gray",    "cautious": "yellow",
            }
            color = color_map.get(stance, "gray")
            stance_entries.append(f"  - {name} ({color} node): '{position}'")
        stances_text = "\n".join(stance_entries) if stance_entries else "  No data"

        prompt = (
            f"Generate a broadcast-style country-stance diagram.\n"
            f"Title: '{topic_title}' (displayed prominently at the top in {lang.prompt_lang})\n\n"
            f"[Elements to draw — ONLY these]\n"
            f"Represent each country below as a circular node and show its stance as text in {lang.prompt_lang}:\n"
            f"{stances_text}\n\n"
            f"[Layout]\n"
            f"- Arrange nodes side by side or in a grid\n"
            f"- Display each country's stance text below its node\n"
            f"- Place the topic title as a heading at the top\n\n"
            f"[NEVER draw]\n"
            f"- Arrows connecting countries\n"
            f"- Realistic flag artwork (simple circle nodes only)\n"
            f"- Countries or information not listed above\n"
            f"- Numbers, statistics, or quoted text\n"
        )

    elif image_type == "VERSUS":
        c1 = countries[0] if len(countries) > 0 else {"country": "Side A", "position": ""}
        c2 = countries[1] if len(countries) > 1 else {"country": "Side B", "position": ""}
        prompt = (
            f"Generate a broadcast-style two-sided comparison graphic.\n"
            f"Title: '{topic_title}' (displayed prominently at the top in {lang.prompt_lang})\n\n"
            f"[Elements to draw — ONLY these]\n"
            f"Left side (red): {c1.get('country', 'Side A')}\n"
            f"  Stance: '{c1.get('position', '')}'\n"
            f"Right side (blue): {c2.get('country', 'Side B')}\n"
            f"  Stance: '{c2.get('position', '')}'\n\n"
            f"[Layout]\n"
            f"- Split left/right layout with 'VS' and a divider line in the centre\n"
            f"- Display country names and stance text in {lang.prompt_lang}\n\n"
            f"[NEVER draw]\n"
            f"- Arrows connecting the two sides\n"
            f"- Realistic flag artwork\n"
            f"- Information not listed above\n"
        )

    elif image_type == "KEYPOINTS":
        points = []
        consensus = analysis.get("consensus_points", [])
        divergence = analysis.get("divergence_points", [])
        for p in consensus[:2]:
            points.append(f"  {len(points)+1}. {p}")
        for p in divergence[:2]:
            points.append(f"  {len(points)+1}. {p}")
        if len(points) < 3:
            for c in countries[:3]:
                points.append(f"  {len(points)+1}. {c.get('country', '')}: {c.get('position', '')}")
        points_text = "\n".join(points[:5]) if points else "  No data"

        prompt = (
            f"Generate a broadcast-style key-points summary graphic.\n"
            f"Title: '{topic_title}' (displayed prominently at the top in {lang.prompt_lang})\n\n"
            f"[Elements to draw — ONLY these]\n"
            f"Display the following points as a numbered list in {lang.prompt_lang}:\n{points_text}\n\n"
            f"[Layout]\n"
            f"- Each point paired with a simple geometric icon (circle, square, etc.) and text\n\n"
            f"[NEVER draw]\n"
            f"- Points or data not listed above\n"
            f"- Arrows or relationship lines\n"
            f"- Realistic illustrations\n"
            f"- Invented numbers or statistics\n"
        )

    else:
        prompt = (
            f"Generate a broadcast-style summary graphic.\n"
            f"Title: '{topic_title}' (displayed prominently at the top in {lang.prompt_lang})\n"
            f"Use only abstract icons and text in {lang.prompt_lang}.\n"
            f"Do NOT add information not present in this prompt.\n"
        )

    return prompt + _COMMON_STYLE


def generate_briefing_images(
    client: genai.Client,
    briefing_data: dict,
    topics: list[dict],
    output_dir: Path,
    date_str: str,
) -> list[Path]:
    """
    Generate one image per topic section from structured briefing_data.

    Does not rely on [IMAGE:] markers; instead builds prompts directly from
    the structured data (country names, stances, analysis, etc.).

    Args:
        client: Initialised genai.Client.
        briefing_data: Structured data produced in Phase 3.
        topics: Topic definitions from topics.yaml.
        output_dir: Directory to save images.
        date_str: Date string (e.g. "March 15, 2026").

    Returns:
        List of generated image paths.
    """
    sections = briefing_data.get("briefing_sections", [])
    logger.info(f"Generating images for {len(sections)} section(s) from briefing_data.")

    generated = []
    for i, section in enumerate(sections):
        topic = topics[i] if i < len(topics) else {}

        image_type = _choose_image_type(topic, section)
        topic_title = section.get("topic", topic.get("title_en", f"Topic {i+1}"))
        logger.info(f"  Section {i+1}/{len(sections)} '{topic_title}' -> {image_type}")

        output_path = output_dir / f"slide_{i:03d}.png"
        prompt = _build_briefing_image_prompt(topic, section, image_type, date_str)
        _generate_image(
            client, prompt, output_path,
            f"briefing image {i+1}/{len(sections)} ({image_type})"
        )
        generated.append(output_path)

    logger.info(f"Briefing image generation complete: {len(generated)} image(s).")
    return generated


def generate_clip_image(
    client: genai.Client, clip: dict, output_path: Path,
) -> Path:
    """Generate a single title-card image for a short clip."""
    lang = get_language_config()
    topic_title = clip.get("topic_title", "News")
    image_prompt = clip.get("image_prompt", "")

    prompt = (
        f"Generate a title card image for the following news topic.\n"
        f"Topic: {topic_title}\n"
        f"{image_prompt}\n\n"
        f"[Style requirements]\n"
        f"- Dark navy (#0F1E3C) background, white text, 16:9 aspect ratio\n"
        f"- Display the topic name prominently in {lang.prompt_lang}\n"
        f"- Use abstract icons or symbols related to the topic\n"
        f"- Flat design, diagram style only\n"
        f"- Small 'WeaveCast' watermark in bottom-right\n"
        f"\n[Absolute prohibitions]\n"
        f"- NO photorealistic people, buildings, scenery, or weapons\n"
        f"- NO invented numbers or statistics\n"
        f"- NO photo-realistic rendering style\n"
        f"- NO emotional or sensational imagery\n"
    )
    return _generate_image(client, prompt, output_path, f"clip image '{topic_title}'")

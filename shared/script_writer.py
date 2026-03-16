"""
Phase 4: News briefing script generation.
Produces from structured JSON data:
  - A full briefing script (3–5 min)
  - Per-topic short-clip scripts (~30 sec each)

Output language is controlled by LANGUAGE in .env.
"""

import json
import logging
import re

from google import genai
from google.genai import types

from .language_utils import get_language_config

logger = logging.getLogger(__name__)


def _strip_preamble(text: str) -> str:
    """Remove preamble / postamble messages from a Gemini response."""
    if "\n---\n" in text:
        parts = text.split("\n---\n", 1)
        if len(parts) > 1 and len(parts[1].strip()) > 200:
            text = parts[1].strip()

    lines = text.split("\n")
    start_idx = 0
    for i, line in enumerate(lines):
        stripped = line.strip()
        if (not stripped
            or stripped.startswith("---")
            or re.match(r'^(はい|承知|了解|かしこまり)', stripped)
            or re.match(r'^(Sure|Certainly|Of course|Here is|Here\'s)', stripped, re.IGNORECASE)
            or re.match(r'^\*\*.*script.*\*\*', stripped, re.IGNORECASE)
            or re.match(r'^#+\s', stripped)
            or re.match(r'^(以下|下記|次の|それでは)', stripped) and i < 5):
            start_idx = i + 1
        else:
            break

    if start_idx > 0 and start_idx < len(lines):
        text = "\n".join(lines[start_idx:]).strip()
    return text


def generate_briefing_script(client: genai.Client, briefing_data: dict) -> str:
    """
    Generate a full briefing narration script (3–5 min, ~1200–1800 chars).

    Args:
        client: Initialised genai.Client.
        briefing_data: Structured data produced in Phase 3.

    Returns:
        Narration script text in the configured output language.
    """
    lang = get_language_config()

    prompt = f"""Based on the structured data below, write a 3–5 minute news briefing narration script in {lang.prompt_lang}.

[CRITICAL CONSTRAINTS]
- Output the script body ONLY. No preamble, greeting, confirmation, or postamble.
- Do NOT include phrases such as "Sure, here is..." or "Certainly!".
- Do NOT use Markdown headings (#, **, --- etc.).
- The very first character must be the start of the narration.
- Do NOT include [IMAGE: ...] markers or any directives. Pure narration text only.

Data:
{json.dumps(briefing_data, indent=2, ensure_ascii=False)}

Script structure:
1. Opening: 2–3 sentences summarising today's key topics.
2. Body: Report on each topic.
   - 2–3 sentences per topic covering major countries' reactions.
   - Highlight divergences in international opinion.
   - Do NOT add information not present in the data.
3. Closing: Brief analysis of likely impact.

Tone: Calm, authoritative broadcast news (NHK World / BBC style).
Length: 1200–1800 characters (or equivalent in {lang.prompt_lang}).
Language: {lang.prompt_lang}"""

    logger.info("Generating full briefing script ...")
    response = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["TEXT"]),
    )
    script_text = _strip_preamble(response.text.strip())
    logger.info(f"Full script generated: ~{len(script_text)} chars.")
    return script_text


def generate_clip_scripts(client: genai.Client, briefing_data: dict) -> list[dict]:
    """
    Generate per-topic short-clip narration scripts (~30 sec, 200–300 chars).

    Args:
        client: Initialised genai.Client.
        briefing_data: Structured data produced in Phase 3.

    Returns:
        List of dicts:
        [
            {
                "topic_title": str,
                "script": str,        # narration in the configured language
                "image_prompt": str,  # English image-generation prompt for the clip
            },
            ...
        ]
    """
    lang = get_language_config()

    sections = briefing_data.get("briefing_sections", [])
    if not sections:
        logger.warning("briefing_sections is empty — cannot generate clip scripts.")
        return []

    clip_scripts = []
    for i, section in enumerate(sections):
        topic_title = section.get("topic", f"Topic {i+1}")
        logger.info(f"Clip script {i+1}/{len(sections)}: {topic_title}")

        section_json = json.dumps(section, indent=2, ensure_ascii=False)

        prompt = f"""Based on the topic data below, write a ~30-second short news clip narration script in {lang.prompt_lang}.

[CRITICAL CONSTRAINTS]
- Output the script body ONLY. No preamble or response phrases.
- The very first character must be the start of the narration.
- Do NOT use Markdown.

Topic data:
{section_json}

Script structure:
1. State the key point of the topic in 1–2 sentences.
2. Briefly report major countries' reactions (2–3 sentences).
3. One closing sentence.

Length: 200–300 characters (or equivalent in {lang.prompt_lang}).
Tone: Calm, authoritative broadcast news style.
Language: {lang.prompt_lang}"""

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT"]),
            )
            script = _strip_preamble(response.text.strip())
        except Exception as e:
            logger.error(f"  Clip script generation failed: {e}")
            script = (
                f"Here is the latest on {topic_title}. "
                f"Please refer to the full briefing for details."
            )

        # Image prompt for the clip (abstract / non-realistic only)
        image_prompt = (
            f"Topic title card for news broadcast. "
            f"Show the topic name '{topic_title}' prominently. "
            f"Use abstract icons and symbols related to the topic (map icons, flag icons, arrows). "
            f"Flat design, diagram style only. "
            f"Do NOT include any photorealistic imagery, people, buildings, or weapons. "
            f"Do NOT invent any numbers, statistics, or data."
        )

        clip_scripts.append({
            "topic_title": topic_title,
            "script": script,
            "image_prompt": image_prompt,
        })
        logger.info(f"  -> {len(script)} chars.")

    logger.info(f"Clip scripts complete: {len(clip_scripts)} item(s).")
    return clip_scripts

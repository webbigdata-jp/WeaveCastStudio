"""
Phase 3: Structured summary generation.
Converts collected raw text into a unified JSON format.
Structures per-topic information as "topic × per-country reactions".
"""

import json
import logging
from datetime import datetime, timezone

from google import genai
from google.genai import types

from .language_utils import get_language_config

logger = logging.getLogger(__name__)


def _normalize_raw_statements(raw_statements: dict) -> tuple[str, dict]:
    """
    Normalise raw_statements into (statements_text, url_map).

    New format (topic keys):
        {"topic_title_en": {"text": "...", "urls": [...], ...}}
    Legacy format (country keys / backward-compat):
        {"country": {"text": "...", "urls": [...]}}
        {"country": "raw text string"}

    __backup__ keys are also processed.

    Returns:
        (statements_text, url_map)
    """
    statements_text_parts = []
    url_map = {}

    for key, value in raw_statements.items():
        if isinstance(value, dict):
            text = value.get("text", "")
            urls = value.get("urls", [])
        else:
            text = str(value)
            urls = []

        statements_text_parts.append(f"=== {key} ===\n{text}")
        url_map[key] = urls

    return "\n\n".join(statements_text_parts), url_map


def generate_structured_summary(
    client: genai.Client,
    topics: list[dict],
    raw_statements: dict,
    max_retries: int = 3,
) -> dict:
    """
    Convert raw per-topic text into structured JSON.

    Args:
        client: Initialised genai.Client.
        topics: Topic definitions [{"title_en": str, "title_target_lang": str, ...}, ...]
        raw_statements: Dict returned by source_collector.
        max_retries: Maximum JSON parse retry attempts.

    Returns:
        briefing_data dict.
    """
    lang = get_language_config()
    statements_text, url_map = _normalize_raw_statements(raw_statements)

    topic_titles_en = [t["title_en"] for t in topics]
    topic_list_str = ", ".join(f'"{t}"' for t in topic_titles_en)

    prompt = f"""You are a diplomatic analyst. Analyse the collected data below and produce
a structured JSON summary of each topic's international reactions.

Target topics: {topic_list_str}

Collected data:
{statements_text}

Output ONLY valid JSON strictly following this schema (no markdown fences, no preamble):
{{
  "generated_at": "{datetime.now(timezone.utc).isoformat()}",
  "briefing_sections": [
    {{
      "topic": "<topic title in {lang.prompt_lang}>",
      "summary": "<2–3 sentence topic overview in {lang.prompt_lang}>",
      "countries": [
        {{
          "country": "<country name in {lang.prompt_lang}>",
          "position": "<one-line stance summary in {lang.prompt_lang}, max 30 chars>",
          "key_quotes": ["<key quote in original language>"],
          "stance": "supportive|opposed|neutral|cautious",
          "speaker": "<name and title of speaker, or null>",
          "source_date": "<YYYY-MM-DD or null>"
        }}
      ],
      "analysis": {{
        "consensus_points": ["<point of consensus in {lang.prompt_lang}>"],
        "divergence_points": ["<point of divergence in {lang.prompt_lang}>"]
      }}
    }}
  ],
  "overall_analysis": {{
    "summary": "<3–4 sentence overall international situation summary in {lang.prompt_lang}>",
    "notable_absences": ["<notable country that has not issued a statement, in {lang.prompt_lang}>"]
  }}
}}

Important:
- Create one entry in briefing_sections per topic.
- Store each country's reaction in the countries array within its topic section.
- Use __backup_osint__ and __backup_headlines__ data to supplement information.
- All analysis text must be written in {lang.prompt_lang}.
"""

    for attempt in range(1, max_retries + 1):
        logger.info(f"Generating structured summary (attempt {attempt}/{max_retries}) ...")
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT"],
                    response_mime_type="application/json",
                ),
            )

            text = response.text.strip()
            if text.startswith("```"):
                text = text.split("\n", 1)[1]
                text = text.rsplit("```", 1)[0]

            briefing_data = json.loads(text)
            _merge_urls(briefing_data, url_map)

            logger.info("Structured summary generation complete.")
            return briefing_data

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed (attempt {attempt}): {e}")
            if attempt == max_retries:
                logger.error("Max retries reached. Returning fallback structure.")
                return _fallback_structure(topics, raw_statements, url_map, lang.prompt_lang)

        except Exception as e:
            logger.error(f"Unexpected error (attempt {attempt}): {e}")
            if attempt == max_retries:
                return _fallback_structure(topics, raw_statements, url_map, lang.prompt_lang)


def _merge_urls(briefing_data: dict, url_map: dict) -> None:
    """Merge url_map entries into briefing_sections."""
    for section in briefing_data.get("briefing_sections", []):
        topic = section.get("topic", "")
        urls = url_map.get(topic, [])
        section["source_urls"] = urls


def _fallback_structure(
    topics: list[dict], raw_statements: dict, url_map: dict, prompt_lang: str
) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefing_sections": [
            {
                "topic": t["title_en"],
                "summary": "Structured parse failed.",
                "countries": [],
                "analysis": {"consensus_points": [], "divergence_points": []},
                "source_urls": url_map.get(t["title_en"], []),
            }
            for t in topics
        ],
        "overall_analysis": {
            "summary": "Structured summary generation failed.",
            "notable_absences": [],
        },
    }

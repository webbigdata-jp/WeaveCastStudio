"""
STEP 3: 構造化要約の生成
収集した生テキストを統一JSONフォーマットに変換する
"""

import json
import logging
from datetime import datetime, timezone
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def _normalize_raw_statements(raw_statements: dict) -> tuple[str, dict]:
    """
    raw_statementsを正規化する。

    source_collectorの新形式:
        {"country": {"text": "...", "urls": [...], "screenshot_paths": [...]}}
    旧形式（後方互換）:
        {"country": "raw text string"}

    Returns:
        (statements_text, url_map)
        - statements_text: Geminiに渡す結合テキスト
        - url_map: {"country": ["url1", "url2", ...]}
    """
    statements_text_parts = []
    url_map = {}

    for country, value in raw_statements.items():
        if isinstance(value, dict):
            text = value.get("text", "")
            urls = value.get("urls", [])
        else:
            text = str(value)
            urls = []

        statements_text_parts.append(f"=== {country} ===\n{text}")
        url_map[country] = urls

    return "\n\n".join(statements_text_parts), url_map


def generate_structured_summary(
    client: genai.Client,
    topic: dict,
    raw_statements: dict,
    max_retries: int = 3,
) -> dict:
    """
    生テキストの各国見解を構造化JSONに変換する。

    Args:
        client: 初期化済みの genai.Client
        topic: トピック定義辞書
        raw_statements: source_collectorが返す辞書
            新形式: {"country": {"text": str, "urls": list, "screenshot_paths": list}}
            旧形式: {"country": "raw text"}  <- 後方互換で対応
        max_retries: JSON parse失敗時の最大リトライ回数

    Returns:
        briefing_data 辞書（source_urlsフィールドにURL一覧を含む）
    """
    statements_text, url_map = _normalize_raw_statements(raw_statements)

    prompt = f"""You are a diplomatic analyst. Analyze the following government statements
about "{topic['title']}" and produce a structured JSON summary.

Raw statements:
{statements_text}

Output ONLY valid JSON (no markdown fences, no preamble) with this exact schema:
{{
  "topic": "{topic['title']}",
  "generated_at": "{datetime.now(timezone.utc).isoformat()}",
  "briefing_sections": [
    {{
      "country": "country name",
      "position": "one-line summary of position (max 20 words)",
      "key_quotes": ["direct quote 1", "direct quote 2"],
      "stance": "supportive|opposed|neutral|cautious",
      "source_url": "primary URL or null",
      "source_date": "YYYY-MM-DD or null",
      "speaker": "Name and Title or null",
      "credibility_score": 5
    }}
  ],
  "analysis": {{
    "consensus_points": ["point 1"],
    "divergence_points": ["point 1"],
    "notable_absences": ["country that has not commented"],
    "summary": "2-3 sentence overall summary of global response"
  }}
}}
"""

    for attempt in range(1, max_retries + 1):
        logger.info(f"Generating structured summary (attempt {attempt}/{max_retries})...")
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
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

            logger.info("Structured summary generated successfully.")
            return briefing_data

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse failed (attempt {attempt}): {e}")
            if attempt == max_retries:
                logger.error("Max retries reached. Returning minimal fallback structure.")
                return _fallback_structure(topic, raw_statements, url_map)

        except Exception as e:
            logger.error(f"Unexpected error on attempt {attempt}: {e}")
            if attempt == max_retries:
                return _fallback_structure(topic, raw_statements, url_map)


def _merge_urls(briefing_data: dict, url_map: dict) -> None:
    """
    url_mapの情報をbriefing_sectionsの各エントリにマージする。
    source_url が null の場合に url_map の最初のURLで補完する。
    また source_urls（複数）フィールドを追加する。
    """
    for section in briefing_data.get("briefing_sections", []):
        country = section.get("country", "")
        urls = url_map.get(country, [])

        if not section.get("source_url") and urls:
            section["source_url"] = urls[0]

        section["source_urls"] = urls


def _fallback_structure(topic: dict, raw_statements: dict, url_map: dict) -> dict:
    """JSON生成が完全に失敗した場合のフォールバック構造"""
    return {
        "topic": topic["title"],
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefing_sections": [
            {
                "country": country,
                "position": "Statement collected but structured parsing failed.",
                "key_quotes": [],
                "stance": "neutral",
                "source_url": url_map.get(country, [None])[0],
                "source_urls": url_map.get(country, []),
                "source_date": None,
                "speaker": None,
                "credibility_score": 3,
            }
            for country in raw_statements.keys()
        ],
        "analysis": {
            "consensus_points": [],
            "divergence_points": [],
            "notable_absences": [],
            "summary": "Structured summary generation failed.",
        },
    }

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
    分析テキスト部分は日本語で出力される。

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

    prompt = f"""あなたは外交アナリストです。以下の各国政府声明を分析し、
「{topic['title']}」に関する構造化JSON要約を作成してください。

各国声明（原文）:
{statements_text}

以下のスキーマに厳密に従った有効なJSONのみを出力してください（マークダウンのフェンスや前置きテキストは不要）:
{{
  "topic": "{topic['title']}",
  "generated_at": "{datetime.now(timezone.utc).isoformat()}",
  "briefing_sections": [
    {{
      "country": "国名（日本語）",
      "position": "立場の一行要約（日本語、最大30文字）",
      "key_quotes": ["主要な引用1（原文の言語のまま）", "主要な引用2"],
      "stance": "supportive|opposed|neutral|cautious",
      "source_url": "主要ソースURLまたはnull",
      "source_date": "YYYY-MM-DD または null",
      "speaker": "発言者の氏名と肩書き または null",
      "credibility_score": 5
    }}
  ],
  "analysis": {{
    "consensus_points": ["合意点1（日本語）"],
    "divergence_points": ["相違点1（日本語）"],
    "notable_absences": ["声明を出していない注目国（日本語）"],
    "summary": "国際社会の反応に関する全体的な要約（日本語、2〜3文）"
  }}
}}
"""

    for attempt in range(1, max_retries + 1):
        logger.info(f"構造化要約を生成中（試行 {attempt}/{max_retries}）...")
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

            logger.info("構造化要約の生成が完了しました。")
            return briefing_data

        except json.JSONDecodeError as e:
            logger.warning(f"JSONパース失敗（試行{attempt}）: {e}")
            if attempt == max_retries:
                logger.error("最大リトライ回数に達しました。最小限のフォールバック構造を返します。")
                return _fallback_structure(topic, raw_statements, url_map)

        except Exception as e:
            logger.error(f"予期しないエラー（試行{attempt}）: {e}")
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
                "position": "声明は収集されましたが、構造化パースに失敗しました。",
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
            "summary": "構造化要約の生成に失敗しました。",
        },
    }

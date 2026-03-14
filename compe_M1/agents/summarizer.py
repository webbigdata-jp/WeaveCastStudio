"""
STEP 3: 構造化要約の生成
収集した生テキストを統一JSONフォーマットに変換する。
トピック単位の情報を「トピック×各国の反応」として構造化する。
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

    新形式（トピックキー）:
        {"topic_title": {"text": "...", "urls": [...], ...}}
    旧形式（国キー / 後方互換）:
        {"country": {"text": "...", "urls": [...]}}
        {"country": "raw text string"}

    __backup__ 系キーも含めて処理する。

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
    生テキストの各トピック情報を構造化JSONに変換する。

    Args:
        client: 初期化済みの genai.Client
        topics: トピック定義のリスト [{"title": str, "title_en": str, ...}, ...]
        raw_statements: source_collectorが返す辞書
        max_retries: JSON parse失敗時の最大リトライ回数

    Returns:
        briefing_data 辞書
    """
    statements_text, url_map = _normalize_raw_statements(raw_statements)

    # トピック一覧を明示
    topic_titles = [t["title"] for t in topics]
    topic_list_str = ", ".join(f"「{t}」" for t in topic_titles)

    prompt = f"""あなたは外交アナリストです。以下の収集データを分析し、
各トピックに関する構造化JSON要約を作成してください。

対象トピック: {topic_list_str}

収集データ:
{statements_text}

以下のスキーマに厳密に従った有効なJSONのみを出力してください（マークダウンのフェンスや前置きテキストは不要）:
{{
  "generated_at": "{datetime.now(timezone.utc).isoformat()}",
  "briefing_sections": [
    {{
      "topic": "トピック名（日本語）",
      "summary": "トピックの概要（日本語、2〜3文）",
      "countries": [
        {{
          "country": "国名（日本語）",
          "position": "立場の一行要約（日本語、最大30文字）",
          "key_quotes": ["主要な引用（原文の言語のまま）"],
          "stance": "supportive|opposed|neutral|cautious",
          "speaker": "発言者の氏名と肩書き または null",
          "source_date": "YYYY-MM-DD または null"
        }}
      ],
      "analysis": {{
        "consensus_points": ["合意点（日本語）"],
        "divergence_points": ["相違点（日本語）"]
      }}
    }}
  ],
  "overall_analysis": {{
    "summary": "全トピックを通した国際情勢の全体的な要約（日本語、3〜4文）",
    "notable_absences": ["声明を出していない注目国（日本語）"]
  }}
}}

重要:
- briefing_sections はトピックごとに1つのエントリを作成すること
- 各トピック内に複数国の反応を countries 配列で格納すること
- __backup_osint__ や __backup_headlines__ のデータも参考にして情報を補完すること
- 分析テキストはすべて日本語で記述すること
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
                logger.error("最大リトライ回数に達しました。フォールバック構造を返します。")
                return _fallback_structure(topics, raw_statements, url_map)

        except Exception as e:
            logger.error(f"予期しないエラー（試行{attempt}）: {e}")
            if attempt == max_retries:
                return _fallback_structure(topics, raw_statements, url_map)


def _merge_urls(briefing_data: dict, url_map: dict) -> None:
    """url_mapの情報をbriefing_sectionsにマージする。"""
    for section in briefing_data.get("briefing_sections", []):
        topic = section.get("topic", "")
        urls = url_map.get(topic, [])
        section["source_urls"] = urls


def _fallback_structure(topics: list[dict], raw_statements: dict, url_map: dict) -> dict:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "briefing_sections": [
            {
                "topic": t["title"],
                "summary": "構造化パースに失敗しました。",
                "countries": [],
                "analysis": {"consensus_points": [], "divergence_points": []},
                "source_urls": url_map.get(t["title"], []),
            }
            for t in topics
        ],
        "overall_analysis": {
            "summary": "構造化要約の生成に失敗しました。",
            "notable_absences": [],
        },
    }

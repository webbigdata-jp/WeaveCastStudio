"""
STEP 4: ニュースブリーフィング原稿生成
構造化JSONデータからナレーション用原稿を生成する
"""

import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def generate_briefing_script(client: genai.Client, briefing_data: dict) -> str:
    """
    briefing_data JSONからナレーション原稿を生成する。
    原稿中には [IMAGE: description] マーカーが含まれる。

    Args:
        client: 初期化済みの genai.Client
        briefing_data: STEP 3で生成した構造化データ

    Returns:
        [IMAGE: ...] マーカー付きの原稿テキスト
    """
    prompt = f"""You are a professional news anchor writing a briefing script.
Based on the following structured diplomatic data, write a 3-5 minute news briefing script.

Data:
{json.dumps(briefing_data, indent=2, ensure_ascii=False)}

Requirements:
- Professional BBC World Service tone
- Open with a brief situation summary (2-3 sentences)
- Cover each country's position in 2-3 sentences
- Group countries by stance (supportive / opposed / neutral / cautious)
- Highlight key divergences between major powers
- Close with a concise analysis of implications
- Include [IMAGE: description] markers between paragraphs (NOT mid-sentence) where visuals would help.
  Suggested placements:
    * After the opening: [IMAGE: World map with countries color-coded by stance - red=opposed, blue=supportive, grey=neutral, yellow=cautious]
    * After covering 2-3 countries: [IMAGE: Side-by-side comparison infographic of major power positions]
    * Near the close: [IMAGE: Diplomatic timeline showing key statements and escalation points]
- Total word count: 600-900 words
- Write in English only
"""

    logger.info("Generating briefing script...")
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT"],
        ),
    )

    script_text = response.text.strip()
    logger.info(f"Script generated: {len(script_text.split())} words")
    return script_text

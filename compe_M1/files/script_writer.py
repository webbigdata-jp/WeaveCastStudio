"""
STEP 4: ニュースブリーフィング原稿生成
構造化JSONデータから日本語ナレーション用原稿を生成する
"""

import json
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def generate_briefing_script(client: genai.Client, briefing_data: dict) -> str:
    """
    briefing_data JSONから日本語ナレーション原稿を生成する。
    原稿中には [IMAGE: description] マーカーが含まれる。

    Args:
        client: 初期化済みの genai.Client
        briefing_data: STEP 3で生成した構造化データ

    Returns:
        [IMAGE: ...] マーカー付きの日本語原稿テキスト
    """
    prompt = f"""あなたはプロのニュースキャスターです。以下の構造化された外交データに基づいて、
3〜5分の日本語ニュースブリーフィング原稿を作成してください。

データ:
{json.dumps(briefing_data, indent=2, ensure_ascii=False)}

要件:
- NHKワールドニュースのような、落ち着いた報道トーンで記述すること
- 冒頭に状況の概要を2〜3文で記述する
- 各国の立場を2〜3文で報告する
- 国を立場ごとにグループ分けする（支持 / 反対 / 中立 / 慎重）
- 主要国間の見解の相違を際立たせる
- 最後に今後の影響に関する簡潔な分析で締めくくる
- 段落と段落の間（文の途中ではない）に [IMAGE: description] マーカーを配置する。
  マーカー内の description は画像生成AI向けの英語で記述すること。
  推奨される配置:
    * 冒頭の概要の後: [IMAGE: World map with countries color-coded by stance - red=opposed, blue=supportive, grey=neutral, yellow=cautious]
    * 2〜3カ国を報告した後: [IMAGE: Side-by-side comparison infographic of major power positions]
    * 締めくくりの近く: [IMAGE: Diplomatic timeline showing key statements and escalation points]
- 合計文字数: 1200〜1800文字程度（日本語）
- 原稿本文は必ず日本語で記述すること（[IMAGE: ...] マーカー内のみ英語）
"""

    logger.info("日本語ブリーフィング原稿を生成中...")
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["TEXT"],
        ),
    )

    script_text = response.text.strip()
    logger.info(f"原稿生成完了: 約{len(script_text)}文字")
    return script_text

"""
STEP 4: ニュースブリーフィング原稿生成
構造化JSONデータから:
  - 全体ブリーフィング原稿（3-5分）
  - トピック別ショートクリップ原稿（各30秒程度）
を生成する。
"""

import json
import re
import logging
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)


def _strip_preamble(text: str) -> str:
    """Geminiの応答から前置き・後書きメッセージを除去する。"""
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
            or re.match(r'^\*\*.*原稿.*\*\*', stripped)
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
    全体ブリーフィング原稿を生成する（3-5分、1200-1800文字）。

    Args:
        client: 初期化済みの genai.Client
        briefing_data: STEP 3で生成した構造化データ
    Returns:
        日本語ナレーション原稿テキスト
    """
    prompt = f"""以下の構造化データに基づいて、3〜5分の日本語ニュースブリーフィング原稿を出力せよ。

【重要な制約】
- 原稿本文のみを出力すること。前置き、挨拶、確認メッセージ、後書きは一切不要。
- 「はい、承知しました」等の応答文は絶対に含めないこと。
- Markdownの見出し（#, **, --- 等）は使用しないこと。
- 最初の文字からナレーション原稿が始まること。
- [IMAGE: ...] のようなマーカーや指示は一切含めないこと。純粋なナレーション原稿のみ。

データ:
{json.dumps(briefing_data, indent=2, ensure_ascii=False)}

原稿の構成:
1. 冒頭: 本日の主要トピックの概要を2〜3文で述べる
2. 本文: 各トピックについて報告する
   - トピックごとに主要国の反応を2〜3文ずつ
   - 国際社会の見解の相違を際立たせる
   - データに含まれていない情報は絶対に追加しないこと
3. 締め: 今後の影響に関する簡潔な分析で締めくくる

トーン: NHKワールドニュースのような落ち着いた報道トーン
文字数: 1200〜1800文字（日本語）
言語: 日本語"""

    logger.info("全体ブリーフィング原稿を生成中...")
    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(response_modalities=["TEXT"]),
    )
    script_text = _strip_preamble(response.text.strip())
    logger.info(f"全体原稿生成完了: 約{len(script_text)}文字")
    return script_text


def generate_clip_scripts(client: genai.Client, briefing_data: dict) -> list[dict]:
    """
    トピック別ショートクリップ原稿を生成する（各30秒程度、200-300文字）。

    Args:
        client: 初期化済みの genai.Client
        briefing_data: STEP 3で生成した構造化データ
    Returns:
        [
            {
                "topic_title": str,
                "script": str,       # ナレーション原稿（日本語）
                "image_prompt": str,  # クリップ用画像生成プロンプト（英語）
            },
            ...
        ]
    """
    sections = briefing_data.get("briefing_sections", [])
    if not sections:
        logger.warning("briefing_sections が空です。クリップ原稿を生成できません。")
        return []

    clip_scripts = []
    for i, section in enumerate(sections):
        topic_title = section.get("topic", f"トピック{i+1}")
        logger.info(f"クリップ原稿 {i+1}/{len(sections)} を生成中: {topic_title}")

        section_json = json.dumps(section, indent=2, ensure_ascii=False)

        prompt = f"""以下のトピックデータに基づいて、約30秒のショートニュースクリップ用の日本語ナレーション原稿を出力せよ。

【重要な制約】
- 原稿本文のみを出力すること。前置きや応答文は一切不要。
- 最初の文字からナレーション原稿が始まること。
- Markdownは使わないこと。

トピックデータ:
{section_json}

原稿の構成:
1. トピックの要点を1〜2文で述べる
2. 主要国の反応を簡潔に報告（2〜3文）
3. 一言で締めくくる

文字数: 200〜300文字（日本語）
トーン: NHKワールドニュースのような落ち着いた報道トーン
言語: 日本語のみ"""

        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash-lite",
                contents=prompt,
                config=types.GenerateContentConfig(response_modalities=["TEXT"]),
            )
            script = _strip_preamble(response.text.strip())
        except Exception as e:
            logger.error(f"  クリップ原稿生成失敗: {e}")
            script = f"{topic_title}に関する最新情報をお伝えします。詳細は全体ブリーフィングをご覧ください。"

        # クリップ用画像プロンプト（抽象的・非写実的な指示のみ）
        image_prompt = (
            f"Topic title card for news broadcast. "
            f"Show the topic name '{topic_title}' prominently in Japanese. "
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
        logger.info(f"  -> {len(script)}文字")

    logger.info(f"クリップ原稿生成完了: {len(clip_scripts)}件")
    return clip_scripts

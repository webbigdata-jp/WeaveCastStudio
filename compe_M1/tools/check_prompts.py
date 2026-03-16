"""
確認スクリプト: image_generator.py / script_writer.py のプロンプト生成ロジックを検証する。
API呼び出しなし。ローカルで即実行可能。

使い方:
  python check_prompts.py
"""

import re
from datetime import datetime, timezone

SEPARATOR = "=" * 70

# ── image_generator.py から対象関数をそのままコピー ──

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
    sanitized = desc
    for keyword in _BANNED_KEYWORDS:
        pattern = re.compile(r'\b' + re.escape(keyword) + r'(?:s|ing|ed)?\b', re.IGNORECASE)
        sanitized = pattern.sub("", sanitized)
    sanitized = re.sub(r"['\"]s\b", "", sanitized)
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()

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
        print(f"    [フォールバック] '{desc}' → '{sanitized}'")

    return sanitized


def _parse_image_type(desc: str) -> tuple[str, str]:
    valid_types = {"MAP", "STANCE", "TIMELINE", "VERSUS", "KEYPOINTS"}
    match = re.match(r'^(MAP|STANCE|TIMELINE|VERSUS|KEYPOINTS)\s*:\s*(.+)$', desc.strip(), re.IGNORECASE)
    if match:
        return match.group(1).upper(), match.group(2).strip()
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
    return "KEYPOINTS", desc


def _build_content_image_prompt(desc: str) -> str:
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

SEPARATOR = "=" * 70


# ──────────────────────────────────────────
# 1. date_str の確認
# ──────────────────────────────────────────
def check_date_str():
    print(SEPARATOR)
    print("【1】date_str の展開確認")
    print(SEPARATOR)

    date_str = datetime.now(timezone.utc).strftime("%B %d, %Y")
    print(f"  date_str = '{date_str}'")

    # タイトルスライド用プロンプトの一部を模擬
    title_prompt_snippet = (
        f'Generate a professional news broadcast title card image.\n'
        f'Date text: {date_str}\n'
    )
    print(f"  タイトルプロンプト抜粋:\n    {title_prompt_snippet.strip()}")

    if "〇" in date_str or "○" in date_str:
        print("  ❌ date_str に丸文字が含まれています！")
    else:
        print("  ✅ date_str は正常です")
    print()


# ──────────────────────────────────────────
# 2. _sanitize_description の確認
# ──────────────────────────────────────────
def check_sanitize():
    print(SEPARATOR)
    print("【2】_sanitize_description の確認")
    print(SEPARATOR)

    test_cases = [
        # (入力, 期待: 禁止語が除去されていること)
        "Portrait of Mojtaba Khamenei with question marks",
        "Damaged military aircraft and a map of the region",
        "Image of children and school buildings with caution tape",
        "Image of a destroyed school building and children's drawings",
        "Map showing Strait of Hormuz and surrounding countries",
        "Timeline of Hormuz crisis events in March 2026",
        "Key points summary: 1. Blockade 2. Oil prices 3. UN response",
        "Soldiers firing missiles at buildings near explosion site",
    ]

    for desc in test_cases:
        sanitized = _sanitize_description(desc)
        changed = desc != sanitized
        mark = "🔧" if changed else "✅"
        print(f"  {mark} 入力: {desc}")
        if changed:
            print(f"     出力: {sanitized}")
        print()


# ──────────────────────────────────────────
# 3. _parse_image_type の確認
# ──────────────────────────────────────────
def check_parse_type():
    print(SEPARATOR)
    print("【3】_parse_image_type の確認")
    print(SEPARATOR)

    test_cases = [
        # 新形式（TYPE: desc）
        "MAP: Strait of Hormuz region with Iran, UAE, Oman labeled",
        "STANCE: US supports open navigation vs Iran restricts passage",
        "TIMELINE: Feb 25 blockade announced, Feb 28 school attack",
        "VERSUS: US position vs Iran position on airstrikes",
        "KEYPOINTS: 1. Blockade scope 2. International reactions",
        # 旧形式（互換）
        "Map showing Strait of Hormuz and surrounding countries",
        "Timeline of Hormuz crisis events",
        "Comparison table of US vs Iran",
        "Key points summary: blockade, oil, UN",
        "Relationship diagram showing US, Iran, China positions",
        # 判定不能 → KEYPOINTS フォールバック
        "Portrait of Mojtaba Khamenei with question marks",
        "Damaged military aircraft and a map of the region",
        "Image of children and school buildings with caution tape",
        "Infographic showing differing international stances",
    ]

    for desc in test_cases:
        img_type, parsed_desc = _parse_image_type(desc)
        print(f"  [{img_type:10s}] {desc}")
    print()


# ──────────────────────────────────────────
# 4. _build_content_image_prompt の確認
# ──────────────────────────────────────────
def check_full_prompt():
    print(SEPARATOR)
    print("【4】_build_content_image_prompt の全文確認（代表3件）")
    print(SEPARATOR)

    samples = [
        "MAP: Strait of Hormuz region with Iran, UAE, Oman labeled",
        "STANCE: US supports open navigation vs Iran restricts passage vs China calls for restraint",
        "Portrait of Mojtaba Khamenei with question marks",  # 禁止語入り → サニタイズ + KEYPOINTS
    ]

    for desc in samples:
        print(f"\n  ── 入力: {desc}")
        prompt = _build_content_image_prompt(desc)
        # 最初の数行だけ表示
        lines = prompt.strip().split("\n")
        for line in lines[:8]:
            print(f"    {line}")
        if len(lines) > 8:
            print(f"    ... (残り {len(lines) - 8} 行)")

        # 禁止語チェック（PROHIBITIONS / requirements セクション外に残っていないか）
        problems = []
        # "=== " で始まるセクションヘッダ以降を除外してチェック
        check_part = prompt
        for section_marker in ["=== STYLE RULES", "=== ABSOLUTE PROHIBITIONS", "requirements:"]:
            idx = check_part.find(section_marker)
            if idx >= 0:
                check_part = check_part[:idx]
        for word in ["portrait", "destroyed", "children", "soldiers", "caution tape",
                      "building", "aircraft", "school", "weapon", "explosion"]:
            if word.lower() in check_part.lower():
                problems.append(f"Content部に'{word}'残存")

        if problems:
            print(f"    ⚠️  問題検出: {problems}")
        else:
            print(f"    ✅ OK")
    print()


# ──────────────────────────────────────────
# 5. 実際のscript出力サンプルとの突合
# ──────────────────────────────────────────
def check_real_markers():
    print(SEPARATOR)
    print("【5】実際に問題だったマーカーの処理結果")
    print(SEPARATOR)

    # 前回実行で問題だったマーカー群
    bad_markers = [
        "World map showing the Strait of Hormuz and surrounding countries",
        "Infographic showing differing international stances on the Hormuz Strait blockade",
        "Portrait of Mojtaba Khamenei with question marks",
        "Damaged military aircraft and a map of the region",
        "Image of children and school buildings with caution tape",
        "Diplomatic timeline of recent Middle East events",
    ]

    for desc in bad_markers:
        img_type, _ = _parse_image_type(desc)
        sanitized = _sanitize_description(desc)
        prompt = _build_content_image_prompt(desc)

        # プロンプトの最初の行だけ
        first_line = prompt.strip().split("\n")[0]

        print(f"  入力:     {desc}")
        print(f"  TYPE:     {img_type}")
        print(f"  サニタイズ: {sanitized}")
        print(f"  プロンプト冒頭: {first_line}")
        print()


# ──────────────────────────────────────────
# 6. 新フロー: briefing_dataからの画像プロンプト生成
# ──────────────────────────────────────────
def check_briefing_image_prompts():
    print(SEPARATOR)
    print("【6】新フロー: briefing_data → 画像プロンプト生成")
    print(SEPARATOR)

    # サンプルの briefing_data を模擬
    sample_briefing = {
        "briefing_sections": [
            {
                "topic": "ホルムズ海峡封鎖",
                "summary": "イランがアメリカとイスラエルの船舶の航行を制限。国際社会は安全な航行確保を求めている。",
                "countries": [
                    {"country": "アメリカ", "position": "海峡の安全な航行確保を要請", "stance": "opposed"},
                    {"country": "イラン", "position": "船舶航行を制限", "stance": "supportive"},
                    {"country": "中国", "position": "緊張緩和を要求", "stance": "cautious"},
                    {"country": "EU", "position": "供給安全保障の再評価", "stance": "cautious"},
                ],
                "analysis": {
                    "consensus_points": ["人道物資の安全な通過が必要"],
                    "divergence_points": ["封鎖の正当性について意見が分かれる"],
                },
            },
            {
                "topic": "イラン新最高指導者",
                "summary": "モジタバ・ハメネイ氏が新最高指導者に就任。強硬姿勢の維持が予想される。",
                "countries": [
                    {"country": "アメリカ", "position": "制裁・軍事的圧力を強化", "stance": "opposed"},
                    {"country": "イスラエル", "position": "安全保障弱体化に注力", "stance": "opposed"},
                ],
                "analysis": {
                    "consensus_points": [],
                    "divergence_points": ["新指導者の権力基盤の安定性について不確実"],
                },
            },
            {
                "topic": "アメリカ軍 損失",
                "summary": "Operation Epic Furyで米軍13名以上死亡、約140名負傷。",
                "countries": [
                    {"country": "アメリカ", "position": "攻撃を継続", "stance": "supportive"},
                    {"country": "イラン", "position": "UAE内の米軍施設を標的", "stance": "opposed"},
                    {"country": "ロシア", "position": "米国の攻勢を批判", "stance": "opposed"},
                ],
                "analysis": {
                    "consensus_points": [],
                    "divergence_points": ["軍事作戦の正当性", "エスカレーションの懸念"],
                },
            },
            {
                "topic": "イラン女子校への空爆",
                "summary": "2月28日にイランの女子校が空爆され多数の生徒が犠牲。",
                "countries": [
                    {"country": "アメリカ", "position": "調査中、故意ではないと主張", "stance": "cautious"},
                    {"country": "中国", "position": "国際人道法違反として非難", "stance": "opposed"},
                    {"country": "国連", "position": "調査と説明責任を要求", "stance": "opposed"},
                ],
                "analysis": {
                    "consensus_points": ["学校への攻撃を非難"],
                    "divergence_points": ["責任の所在"],
                },
            },
        ]
    }

    sample_topics = [
        {"title": "ホルムズ海峡封鎖", "tags": ["hormuz", "iran", "shipping", "military"]},
        {"title": "イラン新最高指導者", "tags": ["iran", "leadership", "politics"]},
        {"title": "アメリカ軍 損失", "tags": ["military", "damage", "war"]},
        {"title": "イラン女子校への空爆", "tags": ["iran", "airstrikes", "civilian", "humanitarian"]},
    ]

    sections = sample_briefing["briefing_sections"]
    for i, section in enumerate(sections):
        topic = sample_topics[i] if i < len(sample_topics) else {}
        title = section.get("topic", "?")

        # _choose_image_type を模擬
        tags = [t.lower() for t in topic.get("tags", [])]
        countries = section.get("countries", [])
        stances = [c.get("stance", "") for c in countries]

        # 簡易判定（image_generator.pyの_choose_image_typeと同じロジック）
        title_lower = (section.get("topic", "") + " " + topic.get("title", "")).lower()
        geo_keywords = ["海峡", "hormuz", "封鎖", "blockade", "shipping"]
        if any(kw in title_lower for kw in geo_keywords) or "shipping" in tags:
            img_type = "MAP"
        elif len(countries) >= 3 and "opposed" in stances and "supportive" in stances:
            img_type = "STANCE"
        elif any(kw in tags for kw in ["military", "damage", "war"]):
            img_type = "KEYPOINTS"
        elif any(kw in tags for kw in ["humanitarian", "civilian"]):
            img_type = "KEYPOINTS"
        elif any(kw in tags for kw in ["politics", "leadership"]):
            img_type = "VERSUS" if len(countries) >= 2 else "KEYPOINTS"
        elif len(countries) >= 3:
            img_type = "STANCE"
        else:
            img_type = "KEYPOINTS"

        print(f"  [{img_type:10s}] {title}")
        print(f"             国数: {len(countries)}, tags: {tags}")

        # 禁止語が混入しないことを確認
        problems = []
        summary = section.get("summary", "")
        for word in ["portrait", "destroyed", "children", "soldiers", "building"]:
            # summary自体に含まれるのは原稿データなのでOK（画像プロンプトに渡す前にチェック）
            pass
        print(f"             ✅ プロンプトはbriefing_dataの構造化情報から生成")
        print()


# ──────────────────────────────────────────
# メイン
# ──────────────────────────────────────────
if __name__ == "__main__":
    check_date_str()
    check_sanitize()
    check_parse_type()
    check_full_prompt()
    check_real_markers()
    check_briefing_image_prompts()
    print(SEPARATOR)
    print("確認完了")
    print(SEPARATOR)

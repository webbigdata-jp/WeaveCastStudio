"""
STEP 2: 各国政府公式見解の収集

処理フロー:
  1. Gemini + Google Search Tool で各国の公式見解と参照URLを検索
  2. 取得したURLをnodriverで実際にブラウザで開き、ページ本文を取得
  3. スクリーンショットを output/screenshots/ に保存（動画スライドの素材として使用可能）
  4. ページ本文をGeminiに渡して要点を再抽出

nodriverはasyncライブラリのため、このモジュールは非同期関数を提供する。
呼び出し元（main.py）では asyncio で実行すること。
"""

import asyncio
import logging
import re
import time
from pathlib import Path
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# スクリーンショット保存先（output/screenshots/）
SCREENSHOT_DIR = Path(__file__).parent.parent / "output" / "screenshots"

# nodriverでページ読み込み後に待機する秒数（JS描画待ち）
PAGE_LOAD_WAIT = 3.0


def _extract_urls_from_text(text: str) -> list[str]:
    """テキスト中のURLをすべて抽出する"""
    pattern = r'https?://[^\s\)\]\,\"\'<>]+'
    return list(dict.fromkeys(re.findall(pattern, text)))  # 重複除去・順序保持


async def _fetch_page_with_nodriver(
    browser,
    url: str,
    screenshot_path: Path,
) -> str:
    """
    nodriverで指定URLをブラウザで開き、ページ本文とスクリーンショットを取得する。

    Returns:
        ページのテキスト本文（取得失敗時は空文字列）
    """
    try:
        logger.info(f"    [nodriver] Opening: {url}")
        page = await browser.get(url)
        await asyncio.sleep(PAGE_LOAD_WAIT)  # JS描画待ち

        # スクリーンショット保存
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        await page.save_screenshot(str(screenshot_path))
        logger.info(f"    [nodriver] Screenshot saved: {screenshot_path.name}")

        # ページ本文HTML取得
        content = await page.get_content()
        await page.close()
        return content or ""

    except Exception as e:
        logger.warning(f"    [nodriver] Failed to fetch {url}: {e}")
        return ""


async def _collect_one_country_async(
    client: genai.Client,
    browser,
    country: str,
    topic: dict,
    screenshot_dir: Path,
) -> tuple[str, dict]:
    """
    1カ国分の情報収集を行う（非同期）。

    Returns:
        (country, {"text": str, "urls": list[str], "screenshot_paths": list[str]})
    """
    logger.info(f"Collecting statement for: {country}")

    # --- Step A: Gemini + Google Search でURL付き要約を取得 ---
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                f"Search for the latest official government statement from {country} "
                f"regarding: {topic['title']}. "
                f"Focus on: foreign ministry statements, presidential/PM remarks, "
                f"UN ambassador statements issued in 2026. "
                f"Return: the source URL, date of statement, name and title of speaker, "
                f"and the key points of their position in 3-5 bullet points. "
                f"If no official statement can be found, say 'NO_STATEMENT_FOUND'."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"],
            ),
        )
        gemini_text = response.text.strip()
    except Exception as e:
        logger.error(f"  -> Gemini search failed for {country}: {e}")
        return country, {}

    if "NO_STATEMENT_FOUND" in gemini_text:
        logger.warning(f"  -> No statement found for {country}, skipping.")
        return country, {}

    # --- Step B: URLを抽出してnodriverでページを取得 ---
    urls = _extract_urls_from_text(gemini_text)
    logger.info(f"  -> Found {len(urls)} URL(s): {urls[:3]}")  # 最初の3件をログ表示

    page_texts = []
    screenshot_paths = []

    for i, url in enumerate(urls[:2]):  # 上位2URLまで取得（コスト・時間を抑制）
        safe_country = re.sub(r'[^a-zA-Z0-9]', '_', country)
        screenshot_path = screenshot_dir / f"{safe_country}_{i:02d}.png"

        page_html = await _fetch_page_with_nodriver(browser, url, screenshot_path)

        if page_html:
            # HTMLタグを除去して本文テキストを抽出（簡易）
            plain = re.sub(r'<[^>]+>', ' ', page_html)
            plain = re.sub(r'\s+', ' ', plain).strip()
            # 先頭5000文字のみ使用（長すぎるとトークンを消費しすぎる）
            page_texts.append(plain[:5000])
            screenshot_paths.append(str(screenshot_path))

    # --- Step C: ページ本文があればGeminiで再要約 ---
    if page_texts:
        combined_page_text = "\n\n---\n\n".join(page_texts)
        try:
            refine_response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=(
                    f"Based on the following official web page content from {country}'s government "
                    f"regarding {topic['title']}, extract:\n"
                    f"- Speaker name and title\n"
                    f"- Date of statement\n"
                    f"- Key position in 3-5 bullet points\n"
                    f"- Any direct quotes\n\n"
                    f"Page content:\n{combined_page_text}\n\n"
                    f"Also include the source URLs: {', '.join(urls[:2])}"
                ),
                config=types.GenerateContentConfig(
                    response_modalities=["TEXT"],
                ),
            )
            final_text = refine_response.text.strip()
        except Exception as e:
            logger.warning(f"  -> Refinement failed for {country}, using Gemini search result: {e}")
            final_text = gemini_text
    else:
        # ページ取得できなかった場合はGemini検索結果をそのまま使用
        final_text = gemini_text

    result = {
        "text": final_text,
        "urls": urls,
        "screenshot_paths": screenshot_paths,
    }
    logger.info(f"  -> Collected ({len(final_text)} chars, {len(screenshot_paths)} screenshots)")
    return country, result


async def collect_government_statements_async(
    client: genai.Client,
    topic: dict,
    screenshot_dir: Path = SCREENSHOT_DIR,
) -> dict:
    """
    各国の政府公式見解をGoogle Search + nodriverで収集する（非同期版）。

    Returns:
        {
            "country": {
                "text": "要約テキスト",
                "urls": ["https://...", ...],
                "screenshot_paths": ["output/screenshots/...png", ...]
            },
            ...
        }
    """
    import nodriver as uc

    screenshot_dir.mkdir(parents=True, exist_ok=True)

    # nodriverブラウザを起動（headless=Trueでバックグラウンド実行）
    browser = await uc.start(headless=True)
    logger.info("nodriver browser started.")

    raw_statements = {}
    try:
        for country in topic["countries_of_interest"]:
            country_key, result = await _collect_one_country_async(
                client, browser, country, topic, screenshot_dir
            )
            if result:
                raw_statements[country_key] = result

            # Google Search Toolのレート制限に配慮
            await asyncio.sleep(2)

    finally:
        browser.stop()
        logger.info("nodriver browser stopped.")

    logger.info(
        f"Collection complete: {len(raw_statements)}/{len(topic['countries_of_interest'])} countries"
    )
    return raw_statements


def collect_government_statements(client: genai.Client, topic: dict) -> dict:
    """
    同期ラッパー。main.pyから呼び出す際はこちらを使う。

    Returns:
        collect_government_statements_async() と同じ構造の辞書
    """
    import nodriver as uc
    return uc.loop().run_until_complete(
        collect_government_statements_async(client, topic)
    )

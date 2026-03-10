"""
STEP 2: 各国政府公式見解の収集

処理フロー:
  1. Gemini + Google Search Tool で各国の公式見解と参照URLを検索
  2. 取得したURLをDrissionPageで実際にブラウザで開き、ページ本文を取得
  3. スクリーンショットを output/screenshots/ に保存（動画スライドの素材として使用可能）
  4. ページ本文をGeminiに渡して要点を再抽出

DrissionPageは同期ライブラリのため、このモジュールは通常の同期関数を提供する。
呼び出し元（main.py）では asyncio.run() は不要。
"""

import logging
import re
import time
from pathlib import Path
from google import genai
from google.genai import types
from DrissionPage import ChromiumPage, ChromiumOptions

logger = logging.getLogger(__name__)

# スクリーンショット保存先（output/screenshots/）
SCREENSHOT_DIR = Path(__file__).parent.parent / "output" / "screenshots"

# ページ読み込み後に待機する秒数（JS描画待ち）
PAGE_LOAD_WAIT = 3.0

# ヘッドレスモード設定（True: バックグラウンド実行、False: ブラウザウィンドウを表示）
HEADLESS = True


def _build_chromium_options(headless: bool = HEADLESS) -> ChromiumOptions:
    """
    DrissionPage用のChromiumOptionsを生成する。

    Args:
        headless: Trueでヘッドレス動作（デフォルトはモジュール定数 HEADLESS に従う）
    Returns:
        設定済みの ChromiumOptions インスタンス
    """
    options = ChromiumOptions()
    if headless:
        options.headless(True)
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    return options


def _extract_urls_from_text(text: str) -> list[str]:
    """
    テキスト中のURLをすべて抽出する。
    Googleの内部リダイレクトURL（vertexaisearch.cloud.google.com）は除外する。
    """
    pattern = r'https?://[^\s\)\]\,\"\'<>]+'
    urls = re.findall(pattern, text)
    # Gemini grounding用の内部リダイレクトURLは実際のページではないため除外
    urls = [u for u in urls if "vertexaisearch.cloud.google.com" not in u]
    return list(dict.fromkeys(urls))  # 重複除去・順序保持


def _fetch_page_with_drissionpage(
    page,
    url: str,
    screenshot_path: Path,
) -> str:
    """
    DrissionPageで指定URLをブラウザで開き、ページ本文とスクリーンショットを取得する。

    Args:
        page: ChromiumPage オブジェクト
        url: 取得対象のURL
        screenshot_path: スクリーンショット保存先パス
    Returns:
        ページのHTML本文（取得失敗時は空文字列）
    """
    try:
        logger.info(f"    [DrissionPage] Opening: {url}")
        page.get(url)
        time.sleep(PAGE_LOAD_WAIT)  # JS描画待ち

        # スクリーンショット保存
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(path=str(screenshot_path.parent), name=screenshot_path.name)
        logger.info(f"    [DrissionPage] Screenshot saved: {screenshot_path.name}")

        # ページ本文HTML取得
        content = page.html
        return content or ""

    except Exception as e:
        logger.warning(f"    [DrissionPage] Failed to fetch {url}: {e}")
        return ""


def _collect_one_country(
    client: genai.Client,
    page,
    country: str,
    topic: dict,
    screenshot_dir: Path,
) -> tuple[str, dict]:
    """
    1カ国分の情報収集を行う（同期）。

    Returns:
        (country, {"text": str, "urls": list[str], "screenshot_paths": list[str]})
    """
    logger.info(f"Collecting statement for: {country}")

    # --- Step A: Gemini + Google Search でURL付き要約を取得 ---
    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=(
                f"Search for the latest official government statement from {country} "
                f"regarding: {topic['title']}. "
                f"Focus on: foreign ministry statements, presidential/PM remarks, "
                f"UN ambassador statements issued in 2026. "
                f"Return the following in plain text format:\n"
                f"1. SOURCE_URL: <the full direct URL to the official page, e.g. https://www.state.gov/...>\n"
                f"2. DATE: <date of statement>\n"
                f"3. SPEAKER: <name and title>\n"
                f"4. KEY_POINTS:\n"
                f"   - <bullet point 1>\n"
                f"   - <bullet point 2>\n"
                f"   ...\n"
                f"IMPORTANT: Write the actual direct URL of the source page in the SOURCE_URL field. "
                f"Do not omit or shorten the URL.\n"
                f"If no official statement can be found, say 'NO_STATEMENT_FOUND'."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"],
                thinking_config=types.ThinkingConfig(thinking_budget=2048),
                max_output_tokens=2048,
            ),
        )
        gemini_text = response.text.strip()
    except Exception as e:
        logger.error(f"  -> Gemini search failed for {country}: {e}")
        return country, {}

    if "NO_STATEMENT_FOUND" in gemini_text:
        logger.warning(f"  -> No statement found for {country}, skipping.")
        return country, {}

    # --- Step B: URLを抽出してDrissionPageでページを取得 ---
    urls = _extract_urls_from_text(gemini_text)
    logger.info(f"  -> Found {len(urls)} URL(s): {urls[:3]}")  # 最初の3件をログ表示

    page_texts = []
    screenshot_paths = []

    for i, url in enumerate(urls[:2]):  # 上位2URLまで取得（コスト・時間を抑制）
        safe_country = re.sub(r'[^a-zA-Z0-9]', '_', country)
        screenshot_path = screenshot_dir / f"{safe_country}_{i:02d}.png"

        page_html = _fetch_page_with_drissionpage(page, url, screenshot_path)

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
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                    max_output_tokens=2048,
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


def collect_government_statements(
    client: genai.Client,
    topic: dict,
    screenshot_dir: Path = SCREENSHOT_DIR,
    headless: bool = HEADLESS,
) -> dict:
    """
    各国の政府公式見解をGoogle Search + DrissionPageで収集する。

    Args:
        client: Gemini APIクライアント
        topic: トピック辞書（"title" および "countries_of_interest" キーを含む）
        screenshot_dir: スクリーンショット保存先ディレクトリ
        headless: Trueでヘッドレス動作（デフォルトはモジュール定数 HEADLESS に従う）
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
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    options = _build_chromium_options(headless=headless)
    page = ChromiumPage(addr_or_opts=options)
    logger.info(f"DrissionPage browser started (headless={headless}).")

    raw_statements = {}
    try:
        for country in topic["countries_of_interest"]:
            country_key, result = _collect_one_country(
                client, page, country, topic, screenshot_dir
            )
            if result:
                raw_statements[country_key] = result

            # Google Search Toolのレート制限に配慮
            time.sleep(2)

    finally:
        page.quit()
        logger.info("DrissionPage browser stopped.")

    logger.info(
        f"Collection complete: {len(raw_statements)}/{len(topic['countries_of_interest'])} countries"
    )
    return raw_statements



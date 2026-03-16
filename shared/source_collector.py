"""
Phase 2: Latest information collection for all topics.

Processing flow:
  1. One Gemini + Google Search Tool query per topic.
     - search_countries are included as hints; other countries may also be covered.
     - Source URLs and titles are extracted from grounding_metadata.
  2. Backup sources (iranmonitor, parseek, signalcockpit) are always fetched
     via DrissionPage and summarised by Gemini to supplement the main results.
  3. News from the past 24 hours is weighted as "latest".

DrissionPage is used exclusively for the OSINT backup sources.
"""

import logging
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from DrissionPage import ChromiumOptions, ChromiumPage
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Default screenshot directory (overridden by main.py)
SCREENSHOT_DIR = Path(__file__).parent.parent / "output" / "screenshots"
PAGE_LOAD_WAIT = 3.0
PAGE_LOAD_WAIT_HEAVY_JS = 6.0
HEADLESS = True

BACKUP_SOURCES = [
    {"name": "IranMonitor", "url": "https://www.iranmonitor.org/", "wait": PAGE_LOAD_WAIT_HEAVY_JS},
    {"name": "Parseek",     "url": "https://www.parseek.com/",     "wait": PAGE_LOAD_WAIT},
    {"name": "SignalCockpit","url": "https://signalcockpit.com/",  "wait": PAGE_LOAD_WAIT_HEAVY_JS},
]


def _build_chromium_options(headless: bool = HEADLESS) -> ChromiumOptions:
    options = ChromiumOptions()
    if headless:
        options.headless(True)
    options.set_argument("--no-sandbox")
    options.set_argument("--disable-gpu")
    return options


def _extract_grounding_sources(response) -> list[dict]:
    sources = []
    try:
        candidate = response.candidates[0]
        gm = getattr(candidate, "grounding_metadata", None)
        if not gm:
            return sources
        for chunk in (getattr(gm, "grounding_chunks", None) or []):
            web = getattr(chunk, "web", None)
            if web:
                url = getattr(web, "uri", "")
                title = getattr(web, "title", "")
                if title or url:
                    sources.append({"title": title, "url": url})
    except Exception as e:
        logger.warning(f"  grounding_metadata extraction error: {e}")
    return sources


def _collect_one_topic(
    client: genai.Client, topic: dict, search_countries: list[str],
) -> dict:
    title = topic["title_en"]

    logger.info(f"Collecting information for topic: '{title}' ...")

    now_utc = datetime.now(timezone.utc)
    date_str = now_utc.strftime("%Y-%m-%d")
    since_str = (now_utc - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M UTC")

    if search_countries:
        countries_hint = (
            f"Include perspectives from countries such as {', '.join(search_countries)}, "
            f"but also include any other relevant countries or international organizations "
            f"(e.g., EU, UN, Gulf states, NATO allies, regional neighbors) that have issued statements."
        )
    else:
        countries_hint = (
            "Include perspectives from all relevant countries and international "
            "organizations worldwide that have issued statements or reactions."
        )

    try:
        response = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=(
                f"Today's date is {date_str}. "
                f"Search for the latest developments, official government statements, "
                f"and reliable news reports regarding: {title}.\n\n"
                f"{countries_hint}\n\n"
                f"PRIORITIZE news and statements from the last 24 hours "
                f"(since {since_str}). "
                f"If nothing exists from the last 24 hours, include the most recent ones from 2026.\n\n"
                f"For each statement or development found, provide:\n"
                f"1. DATE: <date of statement or publication>\n"
                f"2. COUNTRY_OR_ORG: <country name or organization>\n"
                f"3. SPEAKER_OR_SOURCE: <name and title, or media outlet name>\n"
                f"4. KEY_POINTS:\n"
                f"   - <bullet point 1>\n"
                f"   - <bullet point 2>\n"
                f"   ...\n\n"
                f"List multiple statements if available, ordered by date (most recent first).\n"
                f"If no statements or reports can be found, say 'NO_STATEMENT_FOUND'."
            ),
            config=types.GenerateContentConfig(
                tools=[types.Tool(google_search=types.GoogleSearch())],
                response_modalities=["TEXT"],
                thinking_config=types.ThinkingConfig(thinking_budget=2048),
                max_output_tokens=4096,
                safety_settings=[
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_DANGEROUS_CONTENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HARASSMENT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_HATE_SPEECH,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                    types.SafetySetting(
                        category=types.HarmCategory.HARM_CATEGORY_SEXUALLY_EXPLICIT,
                        threshold=types.HarmBlockThreshold.BLOCK_NONE,
                    ),
                ]
            )
        )
        if not response.text:
            finish_reason = (
                response.candidates[0].finish_reason
                if response.candidates else "Unknown"
            )
            logger.warning(
                f"Warning: Gemini returned an empty response (reason: {finish_reason})"
            )
            gemini_text = ""
        else:
            gemini_text = response.text.strip()
    except Exception as e:
        logger.error(f"  -> Gemini search failed for '{title}': {e}")
        return {}

    if "NO_STATEMENT_FOUND" in gemini_text:
        logger.warning(f"  -> No information found for '{title}'.")
        return {}

    grounding_sources = _extract_grounding_sources(response)
    source_urls = [s["url"] for s in grounding_sources if s["url"]]

    logger.info(
        f"  -> {len(gemini_text)} chars, {len(grounding_sources)} source(s)."
    )
    return {
        "text": gemini_text,
        "urls": source_urls,
        "screenshot_paths": [],
        "grounding_sources": grounding_sources,
    }


# ─── Backup sources ───────────────────────────────────────────────────────────

def _html_to_plain_text(html: str, max_chars: int = 8000) -> str:
    plain = re.sub(r'<script[^>]*>.*?</script>', ' ', html, flags=re.DOTALL)
    plain = re.sub(r'<style[^>]*>.*?</style>', ' ', plain, flags=re.DOTALL)
    plain = re.sub(r'<[^>]+>', ' ', plain)
    plain = re.sub(r'\s+', ' ', plain).strip()
    return plain[:max_chars]


def _fetch_backup_source(page, source: dict, screenshot_dir: Path) -> dict:
    name, url, wait = source["name"], source["url"], source.get("wait", PAGE_LOAD_WAIT)
    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', name)
    screenshot_path = screenshot_dir / f"backup_{safe_name}.png"
    logger.info(f"  [backup] Fetching {name}: {url}")
    try:
        page.get(url)
        time.sleep(wait)
        screenshot_path.parent.mkdir(parents=True, exist_ok=True)
        page.get_screenshot(path=str(screenshot_path.parent), name=screenshot_path.name)
        raw_text = _html_to_plain_text(page.html or "")
        logger.info(f"  [backup] {name}: {len(raw_text)} chars retrieved.")
        return {"name": name, "url": url, "raw_text": raw_text, "screenshot_path": str(screenshot_path)}
    except Exception as e:
        logger.warning(f"  [backup] {name}: fetch failed - {e}")
        return {"name": name, "url": url, "raw_text": "", "screenshot_path": None}


def _summarize_backup_sources(client: genai.Client, backup_results: list[dict], topics: list[dict]) -> dict:
    valid = [s for s in backup_results if s["raw_text"]]
    if not valid:
        return {}
    combined = "\n\n".join(
        f"=== {s['name']} ({s['url']}) ===\n{s['raw_text']}" for s in valid
    )
    date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    topic_list = "\n".join(
        f"- {t['title_en']}" for t in topics
    )

    topic_related = ""
    try:
        r = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=(
                f"Current date/time: {date_str}\n\nTopics:\n{topic_list}\n\n"
                f"Extract news related to ANY topic above from:\n{combined}\n\n"
                f"PRIORITIZE last 24 hours. For each: headline (English), related topic, "
                f"source, date, brief context. If none, say 'NO_RELEVANT_NEWS'."
            ),
            config=types.GenerateContentConfig(
                response_modalities=["TEXT"],
                thinking_config=types.ThinkingConfig(thinking_budget=2048),
                max_output_tokens=4096,
            ),
        )
        topic_related = r.text.strip()
    except Exception as e:
        logger.warning(f"  [backup] Topic-related extraction failed: {e}")

    all_headlines = ""
    try:
        r = client.models.generate_content(
            model="gemini-2.5-flash-lite",
            contents=(
                f"Current date/time: {date_str}\n\n"
                f"Extract ALL major headlines from:\n{combined}\n\n"
                f"Numbered list, original language + English translation."
            ),
            config=types.GenerateContentConfig(response_modalities=["TEXT"], max_output_tokens=4096),
        )
        all_headlines = r.text.strip()
    except Exception as e:
        logger.warning(f"  [backup] Headline extraction failed: {e}")

    return {
        "topic_related": topic_related,
        "all_headlines": all_headlines,
        "source_urls": [s["url"] for s in valid],
        "screenshot_paths": [s["screenshot_path"] for s in valid if s["screenshot_path"]],
    }


def _merge_backup(raw_statements: dict, backup_summary: dict) -> dict:
    if not backup_summary:
        return raw_statements
    tr = backup_summary.get("topic_related", "")
    if tr and "NO_RELEVANT_NEWS" not in tr:
        raw_statements["__backup_osint__"] = {
            "text": tr,
            "urls": backup_summary.get("source_urls", []),
            "screenshot_paths": backup_summary.get("screenshot_paths", []),
        }
    ah = backup_summary.get("all_headlines", "")
    if ah:
        raw_statements["__backup_headlines__"] = {
            "text": ah,
            "urls": backup_summary.get("source_urls", []),
            "screenshot_paths": [],
        }
    return raw_statements


# ─── Main entry point ─────────────────────────────────────────────────────────

def collect_all_topics(
    client: genai.Client, config: dict,
    screenshot_dir: Path = SCREENSHOT_DIR, headless: bool = HEADLESS,
) -> dict:
    """
    Collect information for all topics in one pass.

    Args:
        config: Full topics.yaml content: {"search_countries": [...], "topics": [...]}

    Returns:
        {"topic_title_en": {"text":..., "urls":..., ...}, "__backup_osint__":..., ...}
    """
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    topics = config["topics"]
    search_countries = config.get("search_countries", [])
    raw_statements = {}

    for topic in topics:
        result = _collect_one_topic(client, topic, search_countries)
        if result:
            raw_statements[topic["title_en"]] = result
        time.sleep(2)

    logger.info("=" * 40)
    logger.info("Starting backup source collection ...")
    options = _build_chromium_options(headless=headless)
    page = ChromiumPage(addr_or_opts=options)
    logger.info(f"DrissionPage browser started (headless={headless}).")
    try:
        backup_results = [_fetch_backup_source(page, s, screenshot_dir) for s in BACKUP_SOURCES]
        for _ in backup_results:
            time.sleep(1)
        raw_statements = _merge_backup(
            raw_statements,
            _summarize_backup_sources(client, backup_results, topics),
        )
    finally:
        page.quit()
        logger.info("DrissionPage browser stopped.")

    tc = len([k for k in raw_statements if not k.startswith("__")])
    logger.info(
        f"Collection complete: {tc}/{len(topics)} topic(s) + "
        f"{len(BACKUP_SOURCES)} backup source(s)."
    )
    return raw_statements


# Backward-compatibility wrapper
def collect_government_statements(
    client: genai.Client, topic: dict,
    screenshot_dir: Path = SCREENSHOT_DIR, headless: bool = HEADLESS,
) -> dict:
    config = {"search_countries": topic.get("countries_of_interest", []), "topics": [topic]}
    return collect_all_topics(client, config, screenshot_dir, headless)

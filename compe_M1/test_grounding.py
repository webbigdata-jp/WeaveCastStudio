"""
Gemini グラウンディング検索（Step A）単体テスト

目的:
  - google_search ツール経由でどれくらいの情報が返るか確認
  - grounding_metadata の生データを確認
  - 返されたURLが実際にアクセス可能か（HTTPステータスコード）を確認

使い方:
  GOOGLE_API_KEY=xxx python test_grounding.py

  環境変数 GOOGLE_API_KEY が必要。
  .env ファイルがあれば自動読み込みする。
"""

import os
import re
import json
import sys
import requests
from pathlib import Path
from datetime import datetime, timezone

# .env があれば読み込む
try:
    from dotenv import load_dotenv
    # 本番と同じ config/.env を探す（なければカレントの .env）
    env_candidates = [
        Path(__file__).parent / "config" / ".env",
        Path(__file__).parent / ".env",
    ]
    for env_path in env_candidates:
        if env_path.exists():
            load_dotenv(env_path)
            print(f"[info] .env 読み込み: {env_path}")
            break
except ImportError:
    pass

from google import genai
from google.genai import types


# ─────────────────────────────────────────
# テスト設定（ハードコード）
# ─────────────────────────────────────────

SAMPLE_TOPIC = {
    "title": "US-Iran military tensions and airstrikes 2026",
    "countries_of_interest": ["United States", "Iran", "Russia"],
}

# HTTPステータスチェック時のタイムアウト（秒）
HTTP_TIMEOUT = 10

# URL抽出用（source_collector.pyと同じロジック）
def extract_urls(text: str) -> list[str]:
    pattern = r'https?://[^\s\)\]\,\"\'<>]+'
    urls = re.findall(pattern, text)
    urls = [u for u in urls if "vertexaisearch.cloud.google.com" not in u]
    return list(dict.fromkeys(urls))


def check_url_status(url: str) -> dict:
    """URLにHEADリクエストを送りステータスコードを返す。"""
    try:
        resp = requests.head(
            url,
            timeout=HTTP_TIMEOUT,
            allow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; StoryWire/1.0)"},
        )
        return {
            "url": url,
            "status": resp.status_code,
            "final_url": resp.url,
            "redirected": resp.url != url,
        }
    except requests.exceptions.Timeout:
        return {"url": url, "status": "TIMEOUT", "final_url": None, "redirected": False}
    except requests.exceptions.ConnectionError:
        return {"url": url, "status": "CONN_ERROR", "final_url": None, "redirected": False}
    except Exception as e:
        return {"url": url, "status": f"ERROR: {e}", "final_url": None, "redirected": False}


def test_one_country(client: genai.Client, country: str, topic: dict) -> dict:
    """1カ国分のグラウンディング検索を実行し、結果を返す。"""

    prompt = (
        f"Search for the latest official government statements OR reliable news reports from major media outlets "
        f"regarding {country}'s stance on: {topic['title']}. "
        f"Focus on: foreign ministry statements, presidential/PM remarks, UN ambassador statements, "
        f"or reputable news articles reporting on these remarks issued in 2026. "
        f"Return the following in plain text format:\n"
        f"1. SOURCE_URL: <the full direct URL to the official page or news article, e.g. https://www.state.gov/... or https://www.reuters.com/...>\n"
        f"2. DATE: <date of statement or publication>\n"
        f"3. SPEAKER_OR_SOURCE: <name and title of the official, or the name of the media outlet>\n"
        f"4. KEY_POINTS:\n"
        f"   - <bullet point 1>\n"
        f"   - <bullet point 2>\n"
        f"   ...\n"
        f"IMPORTANT: Write the actual direct URL of the source page in the SOURCE_URL field. "
        f"Do not omit or shorten the URL.\n"
        f"If neither official statements nor reliable news reports can be found, say 'NO_STATEMENT_FOUND'."
    )

    print(f"\n{'='*60}")
    print(f"  {country}")
    print(f"{'='*60}")

    response = client.models.generate_content(
        model="gemini-2.5-flash-lite",
        contents=prompt,
        config=types.GenerateContentConfig(
            tools=[types.Tool(google_search=types.GoogleSearch())],
            response_modalities=["TEXT"],
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=4096,
        ),
    )

    # --- 応答テキスト ---
    gemini_text = response.text.strip()
    print(f"\n--- Gemini 応答テキスト ({len(gemini_text)}文字) ---")
    print(gemini_text)

    # --- テキスト中のURL抽出 ---
    text_urls = extract_urls(gemini_text)
    print(f"\n--- テキスト中のURL ({len(text_urls)}件) ---")
    for i, url in enumerate(text_urls):
        print(f"  [{i}] {url}")

    # --- grounding_metadata ---
    grounding_metadata = None
    grounding_urls = []
    try:
        # response.candidates[0].grounding_metadata にメタデータが格納される
        candidate = response.candidates[0]
        gm = getattr(candidate, "grounding_metadata", None)

        if gm:
            grounding_metadata = gm
            print(f"\n--- grounding_metadata (生データ) ---")

            # grounding_chunks（検索結果のソース情報）
            chunks = getattr(gm, "grounding_chunks", None) or []
            print(f"\n  grounding_chunks: {len(chunks)}件")
            for j, chunk in enumerate(chunks):
                web = getattr(chunk, "web", None)
                if web:
                    chunk_url = getattr(web, "uri", "N/A")
                    chunk_title = getattr(web, "title", "N/A")
                    print(f"    [{j}] {chunk_title}")
                    print(f"        URL: {chunk_url}")
                    grounding_urls.append(chunk_url)

            # grounding_supports（テキストとソースの紐付け）
            supports = getattr(gm, "grounding_supports", None) or []
            print(f"\n  grounding_supports: {len(supports)}件")
            for k, sup in enumerate(supports[:5]):  # 最初の5件だけ表示
                segment = getattr(sup, "segment", None)
                seg_text = getattr(segment, "text", "N/A")[:80] if segment else "N/A"
                indices = getattr(sup, "grounding_chunk_indices", [])
                scores = getattr(sup, "confidence_scores", [])
                print(f"    [{k}] \"{seg_text}...\"")
                print(f"        chunk_indices={indices}, confidence={scores}")
            if len(supports) > 5:
                print(f"    ... 他 {len(supports)-5}件省略")

            # search_entry_point（検索クエリの情報）
            sep = getattr(gm, "search_entry_point", None)
            if sep:
                rendered = getattr(sep, "rendered_content", "N/A")
                print(f"\n  search_entry_point: {rendered[:200]}..." if len(str(rendered)) > 200 else f"\n  search_entry_point: {rendered}")

            # web_search_queries
            wsq = getattr(gm, "web_search_queries", None)
            if wsq:
                print(f"\n  web_search_queries: {wsq}")

            # その他のフィールドを列挙
            all_attrs = [a for a in dir(gm) if not a.startswith("_")]
            known = {"grounding_chunks", "grounding_supports", "search_entry_point",
                     "web_search_queries", "retrieval_metadata"}
            unknown = [a for a in all_attrs if a not in known and not callable(getattr(gm, a, None))]
            if unknown:
                print(f"\n  その他のフィールド: {unknown}")
                for attr in unknown:
                    val = getattr(gm, attr, None)
                    print(f"    {attr} = {val}")
        else:
            print(f"\n--- grounding_metadata: なし ---")
    except Exception as e:
        print(f"\n--- grounding_metadata 取得エラー: {e} ---")

    # --- 全URL統合（テキスト中 + grounding_chunks） ---
    all_urls = list(dict.fromkeys(text_urls + grounding_urls))
    print(f"\n--- 全ユニークURL ({len(all_urls)}件) ---")
    for i, url in enumerate(all_urls):
        source = []
        if url in text_urls:
            source.append("text")
        if url in grounding_urls:
            source.append("grounding")
        print(f"  [{i}] [{'+'.join(source)}] {url}")

    # --- HTTPステータスチェック ---
    print(f"\n--- HTTPステータスチェック ---")
    url_checks = []
    for url in all_urls:
        result = check_url_status(url)
        url_checks.append(result)
        status = result["status"]
        redirected = " -> " + result["final_url"] if result["redirected"] else ""
        # ステータスに応じて絵文字
        if isinstance(status, int):
            icon = "OK" if 200 <= status < 400 else "NG"
        else:
            icon = "NG"
        print(f"  [{icon}] {status} {url}{redirected}")

    # --- サマリ ---
    ok_count = sum(1 for r in url_checks if isinstance(r["status"], int) and 200 <= r["status"] < 400)
    ng_count = len(url_checks) - ok_count
    print(f"\n--- サマリ ---")
    print(f"  テキスト長: {len(gemini_text)}文字")
    print(f"  テキスト中URL: {len(text_urls)}件")
    print(f"  grounding_chunks URL: {len(grounding_urls)}件")
    print(f"  全ユニークURL: {len(all_urls)}件")
    print(f"  HTTP到達可能: {ok_count}件 / 到達不可: {ng_count}件")

    return {
        "country": country,
        "text_length": len(gemini_text),
        "text": gemini_text,
        "text_urls": text_urls,
        "grounding_urls": grounding_urls,
        "all_urls": all_urls,
        "url_checks": url_checks,
        "ok_count": ok_count,
        "ng_count": ng_count,
        "has_grounding_metadata": grounding_metadata is not None,
    }


def main():
    # APIキー確認
    api_key = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
    if not api_key:
        print("[ERROR] GOOGLE_API_KEY または GEMINI_API_KEY 環境変数を設定してください。")
        sys.exit(1)

    client = genai.Client()

    topic = SAMPLE_TOPIC
    print(f"トピック: {topic['title']}")
    print(f"テスト対象国: {topic['countries_of_interest']}")
    print(f"実行時刻: {datetime.now(timezone.utc).isoformat()}")

    results = []
    for country in topic["countries_of_interest"]:
        try:
            result = test_one_country(client, country, topic)
            results.append(result)
        except Exception as e:
            print(f"\n[ERROR] {country} の処理中にエラー: {e}")
            results.append({"country": country, "error": str(e)})

        # レート制限に配慮
        import time
        time.sleep(2)

    # --- 全体サマリ ---
    print(f"\n{'='*60}")
    print(f"  全体サマリ")
    print(f"{'='*60}")
    for r in results:
        if "error" in r:
            print(f"  {r['country']}: ERROR - {r['error']}")
        else:
            print(
                f"  {r['country']}: "
                f"テキスト{r['text_length']}文字, "
                f"URL {len(r['all_urls'])}件 "
                f"(到達可能{r['ok_count']}/到達不可{r['ng_count']}), "
                f"grounding_metadata={'あり' if r['has_grounding_metadata'] else 'なし'}"
            )

    # 結果をJSONファイルにも保存
    output_path = Path(__file__).parent / "test_grounding_results.json"
    serializable = []
    for r in results:
        s = {k: v for k, v in r.items()}
        s.pop("has_grounding_metadata", None)  # boolはそのままOK
        serializable.append(s)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(serializable, f, indent=2, ensure_ascii=False, default=str)
    print(f"\n結果をJSONに保存: {output_path}")


if __name__ == "__main__":
    main()

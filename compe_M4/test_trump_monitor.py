"""
compe_M4/test_trump_monitor.py

TrumpMonitor の動作確認スクリプト。
「最新投稿1件が取れて、Gemini に判定させるところまで」を確認する。

実行方法（compe_M4/ ディレクトリから）:
    python test_trump_monitor.py

必要なもの:
    - GOOGLE_API_KEY が .env または環境変数に設定されていること
    - pip install requests beautifulsoup4 google-genai python-dotenv
"""

import json
import logging
import os
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# ── パス設定 ──────────────────────────────────────────────────────
_HERE         = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent

# .env 読み込み
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
    logger.info(".env loaded from %s", _PROJECT_ROOT / ".env")
except ImportError:
    logger.warning("python-dotenv not installed, using environment variables only")

# monitor/ を import できるよう sys.path に追加
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

# ── LanguageConfig スタブ（shared が使えない場合のフォールバック）──
try:
    if str(_PROJECT_ROOT) not in sys.path:
        sys.path.insert(0, str(_PROJECT_ROOT))
    from shared.language_utils import get_language_config
    _LANG = get_language_config()
    logger.info("LanguageConfig loaded: %s (%s)", _LANG.bcp47_code, _LANG.prompt_lang)
except Exception:
    # shared が使えない環境用スタブ
    class _LangStub:
        bcp47_code = "ja"
        prompt_lang = "Japanese"
    _LANG = _LangStub()
    logger.info("LanguageConfig: using stub (ja / Japanese)")

# ── API キー確認 ──────────────────────────────────────────────────
API_KEY = os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")
if not API_KEY:
    print("\n❌ GOOGLE_API_KEY が設定されていません。")
    print("   WeaveCastStudio/.env に GOOGLE_API_KEY=xxx を追加してください。")
    sys.exit(1)

# ── TrumpMonitor import ───────────────────────────────────────────
from monitor.trump_monitor import TrumpMonitor


def test_fetch():
    """STEP 1: スクレイピングで最新投稿が取れるか確認する。"""
    print("\n" + "═" * 55)
    print("  STEP 1: スクレイピングテスト")
    print("═" * 55)

    monitor = TrumpMonitor(db_path=":memory:", api_key=API_KEY, lang=_LANG)
    posts = monitor._fetch()

    if not posts:
        print("❌ 投稿が取得できませんでした。")
        print("   - ネットワーク接続を確認してください")
        print("   - trumpstruth.org にアクセスできるか確認してください")
        return None

    print(f"✅ {len(posts)} 件取得")
    latest = posts[0]
    print(f"\n最新投稿:")
    print(f"  status_id : {latest['status_id']}")
    print(f"  posted_at : {latest['posted_at']}")
    print(f"  url       : {latest['url']}")
    print(f"  text      : {latest['text'][:120]}...")
    return latest


def test_judge(post: dict):
    """STEP 2: Gemini による重要度判定を確認する。"""
    print("\n" + "═" * 55)
    print("  STEP 2: Gemini 判定テスト")
    print("═" * 55)
    print(f"判定対象 (id={post['status_id']}): {post['text'][:80]}...")

    monitor = TrumpMonitor(db_path=":memory:", api_key=API_KEY, lang=_LANG)
    result = monitor._judge(post)

    if result is None:
        print("❌ Gemini 判定に失敗しました。")
        print("   - GOOGLE_API_KEY を確認してください")
        return False

    print(f"\n✅ 判定結果:")
    print(f"  is_breaking     : {result.get('is_breaking')}")
    print(f"  importance_score: {result.get('importance_score')}")
    print(f"  summary         : {result.get('summary')}")
    print(f"  topics          : {result.get('topics')}")
    return True


def main():
    print("\n╔═══════════════════════════════════════════════════╗")
    print("║     TrumpMonitor 動作確認テスト                    ║")
    print("╚═══════════════════════════════════════════════════╝")
    print(f"  API_KEY : {API_KEY[:8]}...")
    print(f"  Language: {_LANG.bcp47_code} ({_LANG.prompt_lang})")

    # STEP 1
    latest_post = test_fetch()
    if latest_post is None:
        print("\n⛔ STEP 1 失敗。テスト終了。")
        sys.exit(1)

    # STEP 2
    ok = test_judge(latest_post)
    if not ok:
        print("\n⛔ STEP 2 失敗。テスト終了。")
        sys.exit(1)

    print("\n" + "═" * 55)
    print("  ✅ 全テスト通過")
    print("═" * 55)
    print("\n次のステップ:")
    print("  python gemini_live_client.py でM4を起動すると")
    print("  TrumpMonitor がバックグラウンドで自動起動します。")
    print("  ログに [TrumpMonitor] が表示されれば正常動作です。\n")


if __name__ == "__main__":
    main()

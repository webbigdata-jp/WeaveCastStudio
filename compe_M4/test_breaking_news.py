"""
compe_M4/test_breaking_news.py

BreakingNewsServer の単体テスト用スクリプト。
ArticleStore を使わず、ダミーデータでティッカーの動作確認を行う。

【テストシナリオ】
  1. サーバ起動 + 通常ニュース 8件のティッカー表示
  2. 12秒後  速報①を差し込み → スクロール内に赤背景で混入 + 上部バナー（初回）
  3. 25秒後  同じ速報①の状態を再送信 → バナーは出ない（既読）ことを確認
  4. 35秒後  速報②を追加 → 新しい速報②のバナーだけ出る
  5. 50秒後  速報を解除 → 通常ニュースのみに戻る
  6. 60秒後  POST /breaking で手動速報テスト
  7. 75秒後  ヘッドライン追加テスト

【使い方】
  python test_breaking_news.py

  ブラウザで http://localhost:8765/overlay を開いて動作確認。

【依存】
  uv add aiohttp
"""

import asyncio
import json
import logging
import sys
import time
from pathlib import Path

_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE))

from breaking_news_server import (
    TickerState,
    create_app,
    start_server,
    DEFAULT_PORT,
)
from aiohttp import web

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_breaking_news")


# ══════════════════════════════════════════════════════════════════
# ダミーデータ
# ══════════════════════════════════════════════════════════════════

DUMMY_HEADLINES = [
    {
        "id": 1,
        "title": "国連安保理、中東情勢の緊急会合を開催へ",
        "source_name": "Reuters",
        "importance_score": 8.5,
        "is_breaking": False,
    },
    {
        "id": 2,
        "title": "米中首脳がジュネーブで電話会談、貿易摩擦の緩和を模索",
        "source_name": "AP通信",
        "importance_score": 8.0,
        "is_breaking": False,
    },
    {
        "id": 3,
        "title": "EU、AI規制法の最終案を採択 世界初の包括的ルール",
        "source_name": "BBC",
        "importance_score": 7.5,
        "is_breaking": False,
    },
    {
        "id": 4,
        "title": "日銀総裁、追加利上げの可能性を示唆 円相場が急伸",
        "source_name": "日経新聞",
        "importance_score": 7.0,
        "is_breaking": False,
    },
    {
        "id": 5,
        "title": "ウクライナ東部で新たな攻勢、主要幹線道路が遮断",
        "source_name": "Al Jazeera",
        "importance_score": 7.8,
        "is_breaking": False,
    },
    {
        "id": 6,
        "title": "WHO、新たな感染症の監視体制強化を発表",
        "source_name": "WHO",
        "importance_score": 6.5,
        "is_breaking": False,
    },
    {
        "id": 7,
        "title": "インド太平洋経済枠組み（IPEF）、サプライチェーン協定で合意",
        "source_name": "CNBC",
        "importance_score": 6.8,
        "is_breaking": False,
    },
    {
        "id": 8,
        "title": "気候変動サミット、途上国への資金支援で新合意",
        "source_name": "Guardian",
        "importance_score": 6.2,
        "is_breaking": False,
    },
]

BREAKING_1 = {
    "id": 100,
    "title": "イラン、ホルムズ海峡で外国籍タンカーを拿捕",
    "source_name": "Reuters",
    "importance_score": 9.8,
    "is_breaking": True,
}

BREAKING_2 = {
    "id": 101,
    "title": "米国防総省、中東への追加派兵を発表",
    "source_name": "AP通信",
    "importance_score": 9.5,
    "is_breaking": True,
}


# ══════════════════════════════════════════════════════════════════
# テストシナリオ
# ══════════════════════════════════════════════════════════════════

async def run_test_scenario(state: TickerState):
    print()
    print("═" * 60)
    print("  Breaking News Ticker テスト v2")
    print("═" * 60)
    print(f"  ブラウザで確認: http://127.0.0.1:{DEFAULT_PORT}/overlay")
    print(f"  状態確認 API:   http://127.0.0.1:{DEFAULT_PORT}/status")
    print()
    print("  テストシナリオ:")
    print("    0秒   通常ニュース 8件を表示")
    print("   12秒   速報①差し込み（赤背景でスクロールに混入 + バナー初回）")
    print("   25秒   同じ速報①を再送信（バナーは出ない＝既読）")
    print("   35秒   速報②追加（速報②のバナーだけ出る）")
    print("   50秒   速報を全解除 → 通常モードに復帰")
    print("   60秒   POST /breaking で手動速報テスト")
    print("   75秒   ヘッドライン追加テスト")
    print()
    print("  Ctrl+C で終了")
    print("═" * 60)
    print()

    # ── Step 1: 通常ニュース ──
    logger.info("Step 1: 通常ニュース 8件を表示")
    state.update(DUMMY_HEADLINES.copy(), [])
    print("✅ 通常ニュース表示中（12秒後に速報①）")

    await asyncio.sleep(12)

    # ── Step 2: 速報①差し込み ──
    logger.info("Step 2: 速報①差し込み（スクロールに混入 + バナー初回）")
    headlines = DUMMY_HEADLINES.copy()
    headlines.insert(0, BREAKING_1)
    state.update(headlines, [BREAKING_1])
    print("🚨 速報①差し込み！ スクロール内に赤背景で表示 + 上部バナー")

    await asyncio.sleep(13)

    # ── Step 3: 同じ速報を再送信（バナーは出ない） ──
    logger.info("Step 3: 同じ速報①を再送信 → バナーは出ないはず")
    state.update(headlines, [BREAKING_1])
    print("🔁 同じ速報①を再送信 → バナーは出ない（既読）はず（10秒後に速報②追加）")

    await asyncio.sleep(10)

    # ── Step 4: 速報②追加 ──
    logger.info("Step 4: 速報②を追加差し込み")
    headlines.insert(1, BREAKING_2)
    state.update(headlines, [BREAKING_1, BREAKING_2])
    print("🚨 速報②追加！ 速報②のバナーだけ出るはず（15秒後に解除）")

    await asyncio.sleep(15)

    # ── Step 5: 速報解除 ──
    logger.info("Step 5: 速報解除 → 通常モード")
    state.update(DUMMY_HEADLINES.copy(), [])
    print("✅ 速報解除、通常モードに復帰（10秒後に手動速報テスト）")

    await asyncio.sleep(10)

    # ── Step 6: POST /breaking テスト ──
    logger.info("Step 6: inject_manual_breaking で手動速報テスト")
    state.inject_manual_breaking(
        "POST /breaking 経由の手動速報テスト",
        "TEST"
    )
    # ヘッドラインにも反映
    manual = {
        "id": f"manual_{int(time.time())}",
        "title": "POST /breaking 経由の手動速報テスト",
        "source_name": "TEST",
        "importance_score": 10.0,
        "is_breaking": True,
    }
    headlines_with_manual = DUMMY_HEADLINES.copy()
    headlines_with_manual.insert(0, manual)
    state.update(headlines_with_manual, state.breaking)
    print("🚨 手動速報テスト！（15秒後にヘッドライン追加テスト）")

    await asyncio.sleep(15)

    # ── Step 7: ヘッドライン追加 ──
    logger.info("Step 7: ヘッドライン追加テスト")
    new_headline = {
        "id": 200,
        "title": "NASA、新型ロケットの打ち上げに成功 火星探査計画が前進",
        "source_name": "NASA",
        "importance_score": 7.2,
        "is_breaking": False,
    }
    updated = DUMMY_HEADLINES.copy()
    updated.insert(2, new_headline)
    state.update(updated, [])
    print("✅ ヘッドライン追加完了。テスト完了 — 引き続きブラウザで確認可。")
    print("   Ctrl+C で終了")

    await asyncio.Event().wait()


# ══════════════════════════════════════════════════════════════════
# エントリポイント
# ══════════════════════════════════════════════════════════════════

async def main():
    state = TickerState()
    runner = await start_server(state)

    try:
        await run_test_scenario(state)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        await runner.cleanup()
        print("\n終了")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass

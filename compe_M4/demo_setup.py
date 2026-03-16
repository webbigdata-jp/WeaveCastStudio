"""
compe_M4/demo_setup.py

デモ撮影用のデータ準備スクリプト。

【機能】
  1. ArticleStore にデモ用のニュース記事を投入
  2. ティッカーサーバを起動（ArticleStoreポーリング）
  3. 指定秒数後にブレーキングニュースを自動差し込み

【使い方】
  # データ投入のみ（ArticleStoreにデモ記事を書き込む）
  python demo_setup.py --seed-db

  # ティッカーサーバ起動 + 指定秒後に速報差し込み
  python demo_setup.py --run --breaking-delay 30

  # データ投入 + サーバ起動 + 速報差し込み（撮影用フルセット）
  python demo_setup.py --seed-db --run --breaking-delay 30

  # ブレーキングニュースを即時差し込み（別ターミナルから）
  python demo_setup.py --trigger-breaking

  # ブレーキングニュースを削除してデモ初期状態に戻す（別ターミナルから）
  python demo_setup.py --clear-breaking
"""

import argparse
import asyncio
import json
import logging
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("demo_setup")

# ── パス設定 ──
_HERE = Path(__file__).resolve().parent
_PROJECT_ROOT = _HERE.parent
_M3_ROOT = _PROJECT_ROOT / "compe_M3"

# sys.path 追加
for p in [str(_PROJECT_ROOT), str(_M3_ROOT)]:
    if p not in sys.path:
        sys.path.insert(0, p)

from store.article_store import ArticleStore
from breaking_news_server import (
    TickerState,
    start_server as start_ticker_server,
    poll_article_store,
    DEFAULT_PORT,
)

# ── デフォルト DB パス ──
DB_PATH = str(_M3_ROOT / "data" / "articles.db")


# ══════════════════════════════════════════════════════════════════
# デモ用ニュース記事データ
# ══════════════════════════════════════════════════════════════════

_NOW = datetime.now(timezone.utc).isoformat()

# 通常ニュース（ティッカーにスクロール表示される）
DEMO_ARTICLES = [
    {
        "source_id": "reuters",
        "source_name": "Reuters",
        "url": "https://demo.example.com/kc135-crash",
        "title": "イラン戦争作戦中にイラク西部で米軍のKC-135空中給油機が墜落",
        "text_content": "米中央軍は、イラクのアル・アサド空軍基地付近でKC-135空中給油機が墜落したと発表。乗組員の安否は不明。",
        "credibility": 9,
        "tier": 1,
        "importance_score": 8.5,
        "topics": '["iran_conflict", "us_military", "aircraft"]',
        "summary": "イラク西部で米軍のKC-135が墜落。対イラン作戦支援中の事故。",
        "is_breaking": False,
    },
    {
        "source_id": "aljazeera",
        "source_name": "Al Jazeera",
        "url": "https://demo.example.com/irgc-hormuz",
        "title": "イラン革命防衛隊、ホルムズ海峡の封鎖継続と米イスラエル関連施設への攻撃を宣言",
        "text_content": "IRGCのサラミ司令官は、ホルムズ海峡の封鎖を無期限で継続し、域内の米イスラエル関連施設を標的にし続けると声明。",
        "credibility": 7,
        "tier": 1,
        "importance_score": 8.8,
        "topics": '["iran_conflict", "hormuz_strait", "irgc", "oil"]',
        "summary": "IRGC司令官がホルムズ海峡封鎖の継続を宣言。米イスラエル施設への攻撃も継続。",
        "is_breaking": False,
    },
    {
        "source_id": "ap",
        "source_name": "AP通信",
        "url": "https://demo.example.com/hormuz-mines",
        "title": "イランがホルムズ海峡に機雷を設置か — 複数の情報筋が報告",
        "text_content": "ペルシャ湾を航行する商船の複数の船長が、ホルムズ海峡付近で機雷様の物体を目撃したと報告。",
        "credibility": 8,
        "tier": 1,
        "importance_score": 7.5,
        "topics": '["iran_conflict", "hormuz_strait", "mines", "naval"]',
        "summary": "ホルムズ海峡に機雷設置の情報。商船の船長らが目撃を報告。",
        "is_breaking": False,
    },
    {
        "source_id": "pentagon",
        "source_name": "米国防総省",
        "url": "https://demo.example.com/pentagon-denies-mines",
        "title": "米軍高官、ホルムズ海峡への機雷設置の噂を否定",
        "text_content": "国防総省報道官は記者会見で「ホルムズ海峡における機雷の存在は確認されていない」と述べた。",
        "credibility": 8,
        "tier": 1,
        "importance_score": 6.8,
        "topics": '["iran_conflict", "hormuz_strait", "mines", "pentagon"]',
        "summary": "国防総省がホルムズ海峡の機雷設置を否定。航行の安全性を強調。",
        "is_breaking": False,
    },
    {
        "source_id": "bbc",
        "source_name": "BBC",
        "url": "https://demo.example.com/mojtaba-khamenei",
        "title": "モジタバ・ハメネイ氏、イランの新最高指導者として初のメッセージを発表する準備",
        "text_content": "ハメネイ師の後継者とされるモジタバ氏が、国民向けの初の公式メッセージを準備中。",
        "credibility": 8,
        "tier": 1,
        "importance_score": 7.2,
        "topics": '["iran_conflict", "iran_leadership", "khamenei"]',
        "summary": "新最高指導者モジタバ・ハメネイ氏の初メッセージが準備中。",
        "is_breaking": False,
    },
    {
        "source_id": "nyt",
        "source_name": "New York Times",
        "url": "https://demo.example.com/minab-school",
        "title": "NYT：米軍の調査で、ミナブの女子学校への空爆は米国の責任と結論",
        "text_content": "米軍の内部調査により、イラン南部ミナブの女子学校への空爆は米軍機によるものと確認された。",
        "credibility": 9,
        "tier": 1,
        "importance_score": 8.0,
        "topics": '["iran_conflict", "civilian_casualties", "us_military"]',
        "summary": "ミナブの女子学校空爆は米軍の責任と内部調査が結論。",
        "is_breaking": False,
    },
    {
        "source_id": "cnbc",
        "source_name": "CNBC",
        "url": "https://demo.example.com/oil-price-stable",
        "title": "原油価格 WTI $93.40 — 各国の協調放出合意で一時的に安定",
        "text_content": "IEA加盟国による戦略石油備蓄の協調放出で原油価格が一時安定。ただし地政学リスクは継続。",
        "credibility": 8,
        "tier": 2,
        "importance_score": 6.5,
        "topics": '["oil_price", "energy", "iea"]',
        "summary": "各国の協調放出で原油価格が一時安定、WTI $93.40。",
        "is_breaking": False,
    },
]

# ブレーキングニュース（速報差し込み用）
BREAKING_ARTICLES = [
    {
        "source_id": "truthsocial",
        "source_name": "Truth Social",
        "url": "https://demo.example.com/trump-kharg-island",
        "title": "トランプ大統領、米軍がKharg Island（ハールク島）のイラン軍事施設を攻撃したと発表",
        "text_content": "トランプ大統領がTruth Socialで声明を投稿。米中央軍がKharg Islandの全軍事目標を攻撃したと発表。石油施設は攻撃しなかったと主張。",
        "credibility": 7,
        "tier": 1,
        "importance_score": 9.8,
        "topics": '["iran_conflict", "kharg_island", "trump", "us_military", "oil"]',
        "summary": "トランプ大統領がKharg Island攻撃を発表。石油施設は未攻撃と主張するも市場は動揺。",
        "is_breaking": True,
    },
    {
        "source_id": "cnbc",
        "source_name": "CNBC",
        "url": "https://demo.example.com/oil-spike-100",
        "title": "原油価格WTI $100突破 — Kharg Island攻撃報道を受け急騰",
        "text_content": "トランプ大統領のKharg Island攻撃声明を受け、WTI原油先物が$100を突破。イランの報復による石油施設攻撃への懸念が市場を直撃。",
        "credibility": 8,
        "tier": 1,
        "importance_score": 9.2,
        "topics": '["oil_price", "kharg_island", "energy", "market"]',
        "summary": "Kharg Island攻撃で原油$100突破。イランの石油施設報復攻撃への懸念。",
        "is_breaking": True,
    },
]


# ══════════════════════════════════════════════════════════════════
# DB操作
# ══════════════════════════════════════════════════════════════════

def seed_database(db_path: str, include_breaking: bool = False):
    """デモ用記事をArticleStoreに投入する。"""
    store = ArticleStore(db_path=db_path)

    articles = DEMO_ARTICLES.copy()
    if include_breaking:
        articles += BREAKING_ARTICLES

    count = 0
    for article in articles:
        row_id = store.save_article({
            "source_id": article["source_id"],
            "source_name": article["source_name"],
            "url": article["url"],
            "title": article["title"],
            "text_content": article["text_content"],
            "credibility": article["credibility"],
            "tier": article["tier"],
            "is_top_page": False,
            "crawled_at": _NOW,
        })
        if row_id:
            # 分析結果を書き戻す
            store.update_analysis(row_id, {
                "summary": article.get("summary", ""),
                "importance_score": article["importance_score"],
                "topics": json.loads(article.get("topics", "[]")),
            })
            # ブレーキングフラグ
            if article.get("is_breaking"):
                store.mark_breaking([row_id], True)
            count += 1
            logger.info(f"  投入: [{article['source_name']}] {article['title'][:50]}...")

    logger.info(f"DB投入完了: {count} 件 → {db_path}")
    return count


def trigger_breaking(db_path: str):
    """ブレーキングニュース記事をDBに投入する。"""
    store = ArticleStore(db_path=db_path)
    count = 0
    for article in BREAKING_ARTICLES:
        row_id = store.save_article({
            "source_id": article["source_id"],
            "source_name": article["source_name"],
            "url": article["url"],
            "title": article["title"],
            "text_content": article["text_content"],
            "credibility": article["credibility"],
            "tier": article["tier"],
            "is_top_page": False,
            "crawled_at": datetime.now(timezone.utc).isoformat(),
        })
        if row_id:
            store.update_analysis(row_id, {
                "summary": article.get("summary", ""),
                "importance_score": article["importance_score"],
                "topics": json.loads(article.get("topics", "[]")),
            })
            store.mark_breaking([row_id], True)
            count += 1
            logger.info(f"  🚨 速報投入: {article['title'][:50]}...")
    logger.info(f"速報投入完了: {count} 件")


def clear_breaking(db_path: str):
    """ブレーキングニュース記事をDBから削除する。

    trigger_breaking() で追加した記事を URL をキーにして完全削除する。
    これにより、ティッカーの通常ニュース欄からも消える。
    """
    import sqlite3

    # まずフラグだけ先に解除（ポーリングで即座に速報扱いが消える）
    store = ArticleStore(db_path=db_path)
    breaking = store.get_breaking()
    if breaking:
        ids = [a["id"] for a in breaking]
        store.mark_breaking(ids, False)
        logger.info(f"速報フラグ解除: {len(ids)} 件")

    # BREAKING_ARTICLES の URL をキーにして記事自体を削除
    breaking_urls = [a["url"] for a in BREAKING_ARTICLES]
    if not breaking_urls:
        logger.info("削除対象の速報記事なし")
        return

    conn = sqlite3.connect(db_path)
    try:
        placeholders = ",".join("?" for _ in breaking_urls)
        cursor = conn.execute(
            f"DELETE FROM articles WHERE url IN ({placeholders})",
            breaking_urls,
        )
        deleted = cursor.rowcount
        conn.commit()
        logger.info(f"速報記事を DB から削除: {deleted} 件")
    except Exception as e:
        logger.error(f"速報記事の削除に失敗: {e}")
    finally:
        conn.close()


# ══════════════════════════════════════════════════════════════════
# ティッカーサーバ + 速報タイマー
# ══════════════════════════════════════════════════════════════════

async def run_server_with_breaking_timer(db_path: str, breaking_delay: int):
    """ティッカーサーバを起動し、指定秒後に速報を差し込む。"""
    state = TickerState()
    runner = await start_ticker_server(state)
    poll_task = asyncio.create_task(
        poll_article_store(state, db_path, interval=10)
    )

    print()
    print("═" * 60)
    print("  StoryWire デモ撮影モード")
    print("═" * 60)
    print(f"  ティッカー: http://127.0.0.1:{DEFAULT_PORT}/overlay")
    print(f"  状態確認:   http://127.0.0.1:{DEFAULT_PORT}/status")
    print(f"  DB:         {db_path}")
    print()
    if breaking_delay > 0:
        print(f"  ⏱  {breaking_delay}秒後にブレーキングニュースを自動差し込み")
    else:
        print("  速報は手動差し込み（別ターミナルで --trigger-breaking）")
    print()
    print("  Ctrl+C で終了")
    print("═" * 60)

    try:
        if breaking_delay > 0:
            print(f"\n⏳ {breaking_delay}秒後に速報が入ります...")
            await asyncio.sleep(breaking_delay)
            print("\n🚨 ブレーキングニュース差し込み中...")
            trigger_breaking(db_path)
            print("🚨 速報差し込み完了！ ティッカーが次回ポーリングで更新されます。")
            print("   (最大10秒以内に反映)")

        # 永続待機
        await asyncio.Event().wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        poll_task.cancel()
        try:
            await poll_task
        except asyncio.CancelledError:
            pass
        await runner.cleanup()
        print("\n終了")


# ══════════════════════════════════════════════════════════════════
# エントリポイント
# ══════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="StoryWire デモ撮影用データ準備",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
使用例:
  # 通常記事をDBに投入
  python demo_setup.py --seed-db

  # ティッカーサーバ起動 + 30秒後に速報差し込み
  python demo_setup.py --run --breaking-delay 30

  # フルセット（DB投入 + サーバ起動 + 30秒後速報）
  python demo_setup.py --seed-db --run --breaking-delay 30

  # 別ターミナルから速報を手動差し込み
  python demo_setup.py --trigger-breaking

  # 速報フラグを解除
  python demo_setup.py --clear-breaking
        """,
    )
    parser.add_argument("--seed-db", action="store_true",
                        help="デモ用の通常ニュースをDBに投入")
    parser.add_argument("--run", action="store_true",
                        help="ティッカーサーバを起動")
    parser.add_argument("--breaking-delay", type=int, default=0,
                        help="速報を自動差し込みするまでの秒数（0=手動）")
    parser.add_argument("--trigger-breaking", action="store_true",
                        help="ブレーキングニュースを即時DBに投入")
    parser.add_argument("--clear-breaking", action="store_true",
                        help="全ブレーキングフラグを解除")
    parser.add_argument("--db-path", type=str, default=DB_PATH,
                        help=f"ArticleStore DBパス（デフォルト: {DB_PATH}）")
    args = parser.parse_args()

    if args.seed_db:
        seed_database(args.db_path, include_breaking=False)

    if args.trigger_breaking:
        trigger_breaking(args.db_path)
        return

    if args.clear_breaking:
        clear_breaking(args.db_path)
        return

    if args.run:
        asyncio.run(run_server_with_breaking_timer(
            args.db_path, args.breaking_delay
        ))
    elif not args.seed_db:
        parser.print_help()


if __name__ == "__main__":
    main()

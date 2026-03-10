"""
test_phase2.py

Phase 2 単体テスト: GeminiClient + GeminiAnalyst

テスト内容:
  1. GeminiClient 初期化（APIキー読み込み確認）
  2. generate_json の動作確認（軽量なテストプロンプト）
  3. GeminiAnalyst.analyze_article — DB から取得した記事 1 件を実際に分析
  4. ArticleStore.update_analysis — 分析結果の書き戻し確認
  5. GeminiAnalyst.batch_analyze — 未分析記事の一括処理

使い方:
    uv run test_phase2.py
"""

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("test_phase2")


def main():
    # ── 1. GeminiClient 初期化 ──
    logger.info("=== Phase 2 Test: Gemini Analyst Pipeline ===")

    try:
        from analyst.gemini_client import GeminiClient
        client = GeminiClient()
        logger.info("✅ GeminiClient initialized")
    except EnvironmentError as e:
        logger.error(f"❌ GeminiClient init failed: {e}")
        sys.exit(1)

    # ── 2. generate_json 動作確認（軽量プロンプト）──
    logger.info("\n[TEST 1] generate_json — smoke test")
    try:
        result = client.generate_json(
            prompt=(
                'Return this exact JSON: {"status": "ok", "value": 42}'
            )
        )
        assert result.get("status") == "ok", f"Unexpected result: {result}"
        logger.info(f"✅ generate_json OK: {result}")
    except Exception as e:
        logger.error(f"❌ generate_json failed: {e}")
        sys.exit(1)

    # ── 3. DB から記事を取得 ──
    logger.info("\n[TEST 2] Load unanalyzed articles from DB")
    try:
        from store.article_store import ArticleStore
        store = ArticleStore()
        unanalyzed = store.get_unanalyzed(limit=5)
        logger.info(f"  Unanalyzed articles: {len(unanalyzed)}")
        if not unanalyzed:
            logger.warning(
                "  ⚠️  DB に未分析記事がありません。"
                "先に test_phase1.py を実行してください。"
            )
            sys.exit(0)
        for a in unanalyzed:
            logger.info(
                f"  id={a['id']} | {a['title'][:60]} | "
                f"Text len: {len(a.get('text_content') or '')}"
            )
    except Exception as e:
        logger.error(f"❌ ArticleStore load failed: {e}")
        sys.exit(1)

    # ── 4. 1 件だけ個別分析 ──
    logger.info("\n[TEST 3] analyze_article — single article")
    from analyst.gemini_analyst import GeminiAnalyst
    analyst = GeminiAnalyst()

    sample = unanalyzed[0]
    try:
        analysis = analyst.analyze_article(sample)
        logger.info(f"  summary       : {analysis['summary'][:100]}")
        logger.info(f"  importance    : {analysis['importance_score']:.1f}")
        logger.info(f"  reason        : {analysis.get('importance_reason', '')[:80]}")
        logger.info(f"  topics        : {analysis['topics']}")
        logger.info(f"  key_entities  : {analysis['key_entities']}")
        logger.info(f"  sentiment     : {analysis['sentiment']}")
        logger.info(f"  actionable    : {analysis['has_actionable_intel']}")
        logger.info("✅ analyze_article OK")
    except Exception as e:
        logger.error(f"❌ analyze_article failed: {e}")
        sys.exit(1)

    # ── 5. 分析結果を DB に書き戻し ──
    logger.info("\n[TEST 4] update_analysis — write back to DB")
    try:
        store.update_analysis(sample["id"], analysis)
        # 書き戻し確認
        updated = store.get_by_source(sample["source_id"], limit=1)
        assert updated[0].get("analyzed_at") is not None, "analyzed_at is None"
        logger.info(f"  analyzed_at: {updated[0]['analyzed_at']}")
        logger.info("✅ update_analysis OK")
    except Exception as e:
        logger.error(f"❌ update_analysis failed: {e}")
        sys.exit(1)

    # ── 6. 残りをバッチ分析 ──
    remaining = unanalyzed[1:]
    if remaining:
        logger.info(f"\n[TEST 5] batch_analyze — {len(remaining)} articles")
        try:
            results = analyst.batch_analyze(remaining)
            for r in results:
                store.update_analysis(r["article_id"], r)
                logger.info(
                    f"  id={r['article_id']} | "
                    f"score={r['importance_score']:.1f} | "
                    f"{r['summary'][:60]}"
                )
            logger.info("✅ batch_analyze OK")
        except Exception as e:
            logger.error(f"❌ batch_analyze failed: {e}")
            sys.exit(1)

    # ── 最終 DB 状態確認 ──
    logger.info("\n[DB STATS]")
    stats = store.get_stats()
    logger.info(f"  Total articles : {stats['total']}")
    logger.info(f"  Analyzed       : {stats['analyzed']}")
    logger.info(f"  Unanalyzed     : {stats['unanalyzed']}")

    logger.info("\n✅ Phase 2 test complete!")


if __name__ == "__main__":
    main()



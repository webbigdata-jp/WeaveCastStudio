"""
analyst/gemini_analyst.py

M3 Gemini 分析パイプライン — STEP 5A
収集済み記事テキストを GeminiClient 経由で分析し、
要約・重要度スコア・トピック分類・エンティティ抽出を返す。

5B (Grounding/Search)・5C (Image生成) は将来このモジュールに追加する。
"""

import logging
from typing import Any

from analyst.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

# 分析に使用するモデル
_ANALYSIS_MODEL = "gemini-2.5-flash"

# 記事テキストの最大送信文字数（プロンプトコスト削減）
_MAX_CONTENT_CHARS = 3000

# 重要度スコアが不明な場合のデフォルト値
_DEFAULT_IMPORTANCE = 5.0

_SYSTEM_PROMPT = """You are a professional OSINT intelligence analyst specializing in 
geopolitical and military affairs. You analyze articles from verified sources and 
produce concise, structured intelligence assessments. Be precise, factual, and 
avoid speculation beyond what the source material supports."""

_ANALYSIS_PROMPT_TEMPLATE = """Analyze the following article and produce a structured intelligence assessment.

SOURCE: {source_name} (Credibility: {credibility}/5, Tier: {tier})
URL: {url}
TITLE: {title}
CONTENT (first {max_chars} chars):
{content}

Output ONLY valid JSON with this exact structure:
{{
  "summary": "2-3 sentence summary of the key information",
  "importance_score": <float 0.0-10.0>,
  "importance_reason": "one sentence explaining the score",
  "topics": ["military", "humanitarian", "diplomatic", ...],
  "key_entities": ["Iran", "CENTCOM", "UN", ...],
  "sentiment": "positive|negative|neutral|alarming",
  "has_actionable_intel": <true|false>
}}

Scoring guide for importance_score:
- 9-10: Breaking — immediate military action, leadership changes, mass casualties
- 7-8:  High     — significant developments, new fronts, major diplomatic shifts
- 5-6:  Medium   — ongoing situation updates, minor skirmishes, routine statements
- 3-4:  Low      — background analysis, historical context, opinion pieces
- 0-2:  Minimal  — tangential, outdated, or redundant information"""


class GeminiAnalyst:
    """
    収集済み記事を Gemini で分析する M3 ユースケースクラス。

    Usage:
        analyst = GeminiAnalyst()
        result = analyst.analyze_article(article_dict)
        results = analyst.batch_analyze([article1, article2, ...])
    """

    def __init__(self, env_path: str | None = None):
        self._client = GeminiClient(env_path=env_path)

    # ──────────────────────────────────────────
    # 公開インターフェース
    # ──────────────────────────────────────────

    def analyze_article(self, article: dict) -> dict[str, Any]:
        """
        1 記事を分析し、要約・重要度・トピック等を返す。（STEP 5A）
        Gemini には収集済みテキストのみを渡す（Web 検索はしない）。

        Args:
            article: ArticleStore から取得した記事 dict
                     必須キー: source_name, credibility, tier, url, title, text_content
        Returns:
            dict: 分析結果
                {
                  "summary": str,
                  "importance_score": float,
                  "importance_reason": str,
                  "topics": list[str],
                  "key_entities": list[str],
                  "sentiment": str,
                  "has_actionable_intel": bool
                }
        Raises:
            RuntimeError: Gemini API 呼び出し失敗時（リトライ上限到達）
            ValueError: JSON パース失敗時
        """
        content = (article.get("text_content") or "").strip()
        if not content:
            logger.warning(
                f"[GeminiAnalyst] Empty text_content for article id="
                f"{article.get('id')}, url={article.get('url')}"
            )

        prompt = _ANALYSIS_PROMPT_TEMPLATE.format(
            source_name=article.get("source_name", "Unknown"),
            credibility=article.get("credibility", "?"),
            tier=article.get("tier", "?"),
            url=article.get("url", ""),
            title=article.get("title", ""),
            max_chars=_MAX_CONTENT_CHARS,
            content=content[:_MAX_CONTENT_CHARS],
        )

        logger.info(
            f"[GeminiAnalyst] Analyzing: {article.get('title', '')[:60]} "
            f"(id={article.get('id')})"
        )

        result = self._client.generate_json(
            prompt=prompt,
            model=_ANALYSIS_MODEL,
            system_instruction=_SYSTEM_PROMPT,
        )

        # 必須フィールドの補完（モデルが欠落させた場合のフォールバック）
        result.setdefault("summary", "Analysis unavailable")
        result.setdefault("importance_score", _DEFAULT_IMPORTANCE)
        result.setdefault("importance_reason", "")
        result.setdefault("topics", [])
        result.setdefault("key_entities", [])
        result.setdefault("sentiment", "neutral")
        result.setdefault("has_actionable_intel", False)

        # importance_score を float に正規化
        try:
            result["importance_score"] = float(result["importance_score"])
            result["importance_score"] = max(0.0, min(10.0, result["importance_score"]))
        except (TypeError, ValueError):
            result["importance_score"] = _DEFAULT_IMPORTANCE

        logger.info(
            f"[GeminiAnalyst] Done: score={result['importance_score']:.1f}, "
            f"topics={result['topics']}"
        )
        return result

    def batch_analyze(
        self,
        articles: list[dict],
        stop_on_error: bool = False,
    ) -> list[dict]:
        """
        複数記事をバッチ分析する。
        各結果には article_id キーを付与して返す。

        Args:
            articles: 記事 dict のリスト（analyze_article と同じ形式）
            stop_on_error: True の場合、1 件失敗で即座に例外を送出する
        Returns:
            list[dict]: 各記事の分析結果リスト
                        失敗した記事はフォールバック値で埋めて含める
                        （stop_on_error=False の場合）
        """
        results: list[dict] = []

        for article in articles:
            article_id = article.get("id")
            try:
                analysis = self.analyze_article(article)
                analysis["article_id"] = article_id
                results.append(analysis)

            except Exception as e:
                logger.error(
                    f"[GeminiAnalyst] Failed for article id={article_id}, "
                    f"url={article.get('url')}: {e}",
                    exc_info=True,
                )
                if stop_on_error:
                    raise

                # フォールバック: 分析失敗でも後続パイプラインを止めない
                results.append({
                    "article_id": article_id,
                    "summary": f"Analysis failed: {e}",
                    "importance_score": _DEFAULT_IMPORTANCE,
                    "importance_reason": "Analysis error",
                    "topics": [],
                    "key_entities": [],
                    "sentiment": "neutral",
                    "has_actionable_intel": False,
                })

        logger.info(
            f"[GeminiAnalyst] Batch complete: "
            f"{len(results)}/{len(articles)} processed"
        )
        return results




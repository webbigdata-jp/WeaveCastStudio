"""
analyst/gemini_analyst.py

M3 Gemini analysis pipeline — STEP 5A
Analyzes collected article text via GeminiClient and returns
a summary, newsworthiness score, topic classification, and entity extraction.

Designed for general-purpose news content creators (YouTubers, journalists,
independent reporters) covering any topic — not limited to geopolitical or
military subjects.

5B (Grounding/Search) and 5C (Image generation) will be added here in future.

Language behaviour:
  Text output fields (summary, importance_reason) are generated in the language
  configured via LANGUAGE in .env (e.g. "ja" → Japanese).
  Structured fields (topics, key_entities, sentiment) remain in English for
  consistent downstream processing.
"""

import logging
from typing import Any

from analyst.gemini_client import GeminiClient

logger = logging.getLogger(__name__)

# Model used for analysis
_ANALYSIS_MODEL = "gemini-2.5-flash"

# Maximum characters of article text sent to the model (reduces prompt cost)
_MAX_CONTENT_CHARS = 3000

# Default importance score when the model returns nothing usable
_DEFAULT_IMPORTANCE = 5.0

_SYSTEM_PROMPT = """You are an experienced news editor and content analyst.
You evaluate articles from a range of sources — news outlets, government bodies,
research institutions, and social media — and produce concise, structured assessments
that help content creators decide what to cover and how to frame it.
Be factual, balanced, and focus on what will resonate with a general audience."""

# -------------------------------------------------------------------
# NOTE FOR MAINTAINERS:
# The analysis prompt below is intentionally general-purpose.
# If you are deploying WeaveCastStudio for a specialist audience
# (e.g. finance, sports, local politics), customise the scoring guide
# and the "topics" examples to match your content vertical.
# The {output_lang} placeholder is filled at runtime from .env LANGUAGE.
# -------------------------------------------------------------------
_ANALYSIS_PROMPT_TEMPLATE = """Analyze the following article and produce a structured content assessment.

SOURCE: {source_name} (Credibility: {credibility}/5, Tier: {tier})
URL: {url}
TITLE: {title}
CONTENT (first {max_chars} chars):
{content}

Output ONLY valid JSON with this exact structure:
{{
  "summary": "2-3 sentence summary of the key information written in {output_lang}",
  "importance_score": <float 0.0-10.0>,
  "importance_reason": "one sentence explaining the score, written in {output_lang}",
  "topics": ["politics", "economy", "science", "culture", "conflict", ...],
  "key_entities": ["Person Name", "Organisation", "Location", ...],
  "sentiment": "positive|negative|neutral|alarming",
  "has_actionable_intel": <true|false>
}}

Field notes:
- summary and importance_reason MUST be written in {output_lang}.
- topics and key_entities MUST remain in English regardless of output language.
- has_actionable_intel: true when the article contains a concrete development
  that a content creator should report on immediately (e.g. new law enacted,
  major incident confirmed, official statement with direct quotes).

Scoring guide for importance_score:
- 9-10: Breaking  — major confirmed event, significant policy change, large-scale incident
- 7-8:  High      — notable development, new official statement, trending story
- 5-6:  Medium    — ongoing situation update, background context, expert opinion
- 3-4:  Low       — soft news, editorial, analysis without new facts
- 0-2:  Minimal   — tangential, outdated, promotional, or redundant content"""


class GeminiAnalyst:
    """
    Analyzes collected articles using Gemini (M3 use-case class).

    Text output fields (summary, importance_reason) are returned in the language
    specified by LANGUAGE in .env.  Structured fields stay in English.

    Usage:
        analyst = GeminiAnalyst()
        result = analyst.analyze_article(article_dict)
        results = analyst.batch_analyze([article1, article2, ...])
    """

    def __init__(self, env_path: str | None = None):
        self._client = GeminiClient(env_path=env_path)
        # Resolve output language once; reuse across all calls
        self._output_lang = self._client.language.prompt_lang

    # ──────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────

    def analyze_article(self, article: dict) -> dict[str, Any]:
        """
        Analyze one article and return summary, score, topics, etc. (STEP 5A)
        Only collected text is sent to Gemini — no web search is performed here.

        Text fields (summary, importance_reason) are in the language set by
        LANGUAGE in .env.  All other fields remain in English.

        Args:
            article: article dict from ArticleStore.
                     Required keys: source_name, credibility, tier, url,
                                    title, text_content
        Returns:
            dict with keys:
                summary           (str, in output language)
                importance_score  (float 0.0-10.0)
                importance_reason (str, in output language)
                topics            (list[str], English)
                key_entities      (list[str], English)
                sentiment         (str)
                has_actionable_intel (bool)
        Raises:
            RuntimeError: Gemini API call failed after all retries
            ValueError: JSON parsing failed
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
            output_lang=self._output_lang,
        )

        logger.info(
            f"[GeminiAnalyst] Analyzing: {article.get('title', '')[:60]} "
            f"(id={article.get('id')}, lang={self._output_lang})"
        )

        result = self._client.generate_json(
            prompt=prompt,
            model=_ANALYSIS_MODEL,
            system_instruction=_SYSTEM_PROMPT,
        )

        # Fill in any fields the model omitted
        result.setdefault("summary", "Analysis unavailable")
        result.setdefault("importance_score", _DEFAULT_IMPORTANCE)
        result.setdefault("importance_reason", "")
        result.setdefault("topics", [])
        result.setdefault("key_entities", [])
        result.setdefault("sentiment", "neutral")
        result.setdefault("has_actionable_intel", False)

        # Normalise importance_score to float in [0.0, 10.0]
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
        Batch-analyze multiple articles.
        Each result includes an article_id key for DB write-back.

        Args:
            articles: list of article dicts (same format as analyze_article)
            stop_on_error: if True, raise immediately on first failure;
                           if False (default), failed articles are returned
                           with fallback values so the pipeline keeps running
        Returns:
            list[dict]: analysis result per article (failures filled with defaults
                        when stop_on_error=False)
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

                # Fallback: keep the pipeline running even on individual failures
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

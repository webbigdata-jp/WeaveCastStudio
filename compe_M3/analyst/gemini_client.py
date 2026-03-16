"""
analyst/gemini_client.py

Low-level wrapper around the google-genai SDK.
- Client initialisation (GOOGLE_API_KEY and LANGUAGE loaded automatically from .env)
- Thin wrapper around generate_content (with retry and timeout)
- JSON response helper

All other M3 modules call Gemini through this client.
5B (Grounding/Search) and 5C (Image) will be added here in future.
"""

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

# Default model
DEFAULT_TEXT_MODEL = "gemini-2.5-flash-lite"

# Retry settings
_MAX_RETRIES = 3
_RETRY_DELAY_SEC = 2.0


def _load_env(env_path: str | None = None) -> None:
    """
    Load environment variables from a .env file (no python-dotenv dependency).
    Existing environment variables are never overwritten.

    Args:
        env_path: explicit path to a .env file. When None, the following
                  locations are tried in order:
                  1. Project root (WeaveCastStudio/)/.env  ← recommended
                  2. Current working directory/.env
                  3. config/.env  (legacy)
                  4. ../config/.env  (legacy)
    """
    # Project root = two levels above this file (compe_M3/analyst/ → WeaveCastStudio/)
    _project_root = Path(__file__).resolve().parent.parent.parent

    candidates = (
        [Path(env_path)]
        if env_path
        else [
            _project_root / ".env",
            Path(".env"),
            Path("config/.env"),
            Path("../config/.env"),
        ]
    )
    for p in candidates:
        if p.exists():
            with open(p) as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    key, _, val = line.partition("=")
                    key = key.strip()
                    val = val.strip().strip('"').strip("'")
                    if key and key not in os.environ:
                        os.environ[key] = val
            logger.debug(f"[GeminiClient] Loaded env from: {p}")
            return
    logger.debug("[GeminiClient] No .env file found; using existing env vars")


class GeminiClient:
    """
    Thin wrapper around the google-genai SDK.
    Reads GOOGLE_API_KEY and LANGUAGE from environment variables on instantiation.

    Usage:
        client = GeminiClient()
        result = client.generate_json(prompt="...", model="gemini-2.5-flash")

    The resolved language config is available as client.language for use in
    prompt construction (e.g. instructing the model to respond in a specific language).
    """

    def __init__(self, env_path: str | None = None):
        _load_env(env_path)
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GOOGLE_API_KEY not found. "
                "Please check your .env file or environment variables."
            )
        self._client = genai.Client(api_key=api_key)

        # Resolve LANGUAGE from .env via shared language_utils
        import sys
        _project_root = Path(__file__).resolve().parent.parent.parent
        if str(_project_root) not in sys.path:
            sys.path.insert(0, str(_project_root))
        from shared.language_utils import get_language_config
        self.language = get_language_config()

        logger.debug(
            f"[GeminiClient] Initialized "
            f"(language={self.language.bcp47_code} / {self.language.prompt_lang})"
        )

    # ──────────────────────────────────────────
    # Public interface
    # ──────────────────────────────────────────

    def generate_text(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.2,
        max_output_tokens: int = 4096,
    ) -> str:
        """
        Generate text and return response.text.

        Args:
            prompt: user prompt
            model: model name to use
            system_instruction: optional system prompt
            temperature: sampling temperature
            max_output_tokens: maximum output token count
        Returns:
            str: generated text
        Raises:
            RuntimeError: when retry limit is reached
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            max_output_tokens=max_output_tokens,
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            **({"system_instruction": system_instruction} if system_instruction else {}),
        )
        response = self._call_with_retry(
            model=model,
            contents=prompt,
            config=config,
        )
        return response.text or ""

    def generate_json(
        self,
        prompt: str,
        model: str = DEFAULT_TEXT_MODEL,
        system_instruction: str | None = None,
        temperature: float = 0.1,
        max_output_tokens: int = 4096,
    ) -> dict[str, Any]:
        """
        Generate a JSON response and return it as a dict.
        Sets response_mime_type="application/json"; falls back to regex
        extraction if JSON parsing fails.

        Args:
            prompt: user prompt (should request JSON output)
            model: model name to use
            system_instruction: optional system prompt
            temperature: sampling temperature (low values recommended for JSON)
            max_output_tokens: maximum output token count
        Returns:
            dict: parsed JSON
        Raises:
            ValueError: when JSON parsing fails
            RuntimeError: when retry limit is reached
        """
        config = types.GenerateContentConfig(
            temperature=temperature,
            thinking_config=types.ThinkingConfig(thinking_budget=2048),
            max_output_tokens=max_output_tokens,
            response_mime_type="application/json",
            **({"system_instruction": system_instruction} if system_instruction else {}),
        )
        response = self._call_with_retry(
            model=model,
            contents=prompt,
            config=config,
        )
        return self._parse_json(response.text or "")

    # ──────────────────────────────────────────
    # Internal utilities
    # ──────────────────────────────────────────

    def _call_with_retry(
        self,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        """
        Call generate_content with retry logic.
        Retries on rate-limit (429) and transient server errors (5xx).
        """
        last_exc: Exception | None = None
        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                return self._client.models.generate_content(
                    model=model,
                    contents=contents,
                    config=config,
                )
            except Exception as e:
                err_str = str(e).lower()
                is_retryable = (
                    "429" in err_str
                    or "rate" in err_str
                    or "quota" in err_str
                    or "503" in err_str
                    or "500" in err_str
                )
                if is_retryable and attempt < _MAX_RETRIES:
                    wait = _RETRY_DELAY_SEC * attempt
                    logger.warning(
                        f"[GeminiClient] Attempt {attempt}/{_MAX_RETRIES} failed "
                        f"({e}), retrying in {wait:.1f}s..."
                    )
                    time.sleep(wait)
                    last_exc = e
                else:
                    raise
        raise RuntimeError(
            f"Gemini API failed after {_MAX_RETRIES} retries"
        ) from last_exc

    @staticmethod
    def _parse_json(text: str) -> dict[str, Any]:
        """
        Extract and parse JSON from text.
        Attempts in order:
        1. Direct parse
        2. Strip ```json ... ``` fences, then parse
        3. Extract the first { ... } block with regex, then parse
        """
        text = text.strip()

        # 1. Direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. Strip code fences
        fenced = re.sub(r"^```(?:json)?\s*", "", text)
        fenced = re.sub(r"\s*```$", "", fenced).strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass

        # 3. Extract first { } block
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"JSON parsing failed. Response prefix: {text[:200]!r}")


"""
analyst/gemini_client.py

google-genai SDK の低レベルラッパー。
- Client の初期化（GOOGLE_API_KEY は .env から自動ロード）
- generate_content の薄いラッパー（リトライ・タイムアウト）
- JSON レスポンス取得ヘルパー

M3 内の他モジュールはこのクライアントを経由して Gemini を呼び出す。
5B (Grounding/Search)・5C (Image) は将来このモジュールに追加する。
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

# デフォルトモデル
DEFAULT_TEXT_MODEL = "gemini-2.5-flash-lite"

# リトライ設定
_MAX_RETRIES = 3
_RETRY_DELAY_SEC = 2.0


def _load_env(env_path: str | None = None) -> None:
    """
    .env ファイルから環境変数をロードする（python-dotenv 非依存の簡易実装）。
    既に環境変数が設定済みの場合は上書きしない。

    Args:
        env_path: .env ファイルのパス。None の場合は以下の順で探索:
                  1. カレントディレクトリ/.env
                  2. config/.env
                  3. ../config/.env（M3 からの相対パス）
    """
    candidates = (
        [Path(env_path)]
        if env_path
        else [
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
    google-genai SDK の薄いラッパー。
    インスタンス化時に GOOGLE_API_KEY を環境変数から読み込む。

    Usage:
        client = GeminiClient()
        result = client.generate_json(prompt="...", model="gemini-2.5-flash")
    """

    def __init__(self, env_path: str | None = None):
        _load_env(env_path)
        api_key = os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise EnvironmentError(
                "GOOGLE_API_KEY が見つかりません。"
                ".env ファイルまたは環境変数を確認してください。"
            )
        self._client = genai.Client(api_key=api_key)
        logger.debug("[GeminiClient] Initialized")

    # ──────────────────────────────────────────
    # 公開インターフェース
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
        テキスト生成。レスポンスの .text を返す。

        Args:
            prompt: ユーザープロンプト
            model: 使用するモデル名
            system_instruction: システムプロンプト（任意）
            temperature: サンプリング温度
            max_output_tokens: 最大出力トークン数
        Returns:
            str: 生成テキスト
        Raises:
            RuntimeError: リトライ上限到達時
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
        JSON レスポンスを生成し、dict として返す。
        response_mime_type="application/json" を指定し、
        パース失敗時は正規表現でフォールバック抽出を試みる。

        Args:
            prompt: ユーザープロンプト（JSON を要求する内容）
            model: 使用するモデル名
            system_instruction: システムプロンプト（任意）
            temperature: サンプリング温度（JSON生成時は低めを推奨）
            max_output_tokens: 最大出力トークン数
        Returns:
            dict: パース済み JSON
        Raises:
            ValueError: JSON パース失敗時
            RuntimeError: リトライ上限到達時
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
    # 内部ユーティリティ
    # ──────────────────────────────────────────

    def _call_with_retry(
        self,
        model: str,
        contents: Any,
        config: types.GenerateContentConfig,
    ) -> types.GenerateContentResponse:
        """
        generate_content をリトライ付きで呼び出す。
        レート制限（429）・一時的なサーバーエラー（5xx）に対してリトライする。
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
        テキストから JSON を抽出してパースする。
        1. そのままパースを試みる
        2. ```json ... ``` フェンスを除去してパース
        3. 最初の { ... } ブロックを正規表現で抽出してパース
        """
        text = text.strip()

        # 1. 直接パース
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # 2. コードフェンス除去
        fenced = re.sub(r"^```(?:json)?\s*", "", text)
        fenced = re.sub(r"\s*```$", "", fenced).strip()
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass

        # 3. 最初の { } ブロックを抽出
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                return json.loads(match.group())
            except json.JSONDecodeError:
                pass

        raise ValueError(f"JSON パースに失敗しました。レスポンス冒頭: {text[:200]!r}")


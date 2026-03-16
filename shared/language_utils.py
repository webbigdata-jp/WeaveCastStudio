"""
language_utils.py — Language configuration utilities for WeaveCast.

Reads LANGUAGE (BCP-47 code) from .env and provides:
  - bcp47_code   : used directly by Live API (e.g. "ja")
  - prompt_lang  : natural-language name for prompt instructions (e.g. "Japanese")

Usage:
    from shared.language_utils import get_language_config
    lang = get_language_config()
    # lang.bcp47_code  -> "ja"
    # lang.prompt_lang -> "Japanese"
"""

import os
from dataclasses import dataclass

# BCP-47 code -> natural language name used in Gemini prompts.
# Extend this table as needed.
_BCP47_TO_PROMPT_LANG: dict[str, str] = {
    "af": "Afrikaans",
    "am": "Amharic",
    "ar": "Arabic",
    "as": "Assamese",
    "az": "Azerbaijani",
    "be": "Belarusian",
    "bg": "Bulgarian",
    "bn": "Bengali",
    "bs": "Bosnian",
    "ca": "Catalan",
    "cs": "Czech",
    "cy": "Welsh",
    "da": "Danish",
    "de": "German",
    "el": "Greek",
    "en": "English",
    "es": "Spanish",
    "et": "Estonian",
    "eu": "Basque",
    "fa": "Persian",
    "fi": "Finnish",
    "fil": "Filipino",
    "fr": "French",
    "gl": "Galician",
    "gu": "Gujarati",
    "he": "Hebrew",
    "hi": "Hindi",
    "hr": "Croatian",
    "hu": "Hungarian",
    "hy": "Armenian",
    "id": "Indonesian",
    "is": "Icelandic",
    "it": "Italian",
    "iw": "Hebrew",
    "ja": "Japanese",
    "ka": "Georgian",
    "kk": "Kazakh",
    "km": "Khmer",
    "kn": "Kannada",
    "ko": "Korean",
    "lo": "Lao",
    "lt": "Lithuanian",
    "lv": "Latvian",
    "mk": "Macedonian",
    "ml": "Malayalam",
    "mn": "Mongolian",
    "mr": "Marathi",
    "ms": "Malay",
    "ne": "Nepali",
    "nl": "Dutch",
    "no": "Norwegian",
    "or": "Odia",
    "pa": "Punjabi",
    "pl": "Polish",
    "pt": "Portuguese",
    "ro": "Romanian",
    "ru": "Russian",
    "sk": "Slovak",
    "sl": "Slovenian",
    "sq": "Albanian",
    "sr": "Serbian",
    "sv": "Swedish",
    "sw": "Swahili",
    "ta": "Tamil",
    "te": "Telugu",
    "th": "Thai",
    "tr": "Turkish",
    "uk": "Ukrainian",
    "ur": "Urdu",
    "uz": "Uzbek",
    "vi": "Vietnamese",
    "zh": "Chinese",
    "zu": "Zulu",
}

_DEFAULT_BCP47 = "en"


@dataclass(frozen=True)
class LanguageConfig:
    """Resolved language configuration."""
    bcp47_code: str   # For Live API: "ja", "en", ...
    prompt_lang: str  # For prompt text: "Japanese", "English", ...


def get_language_config() -> LanguageConfig:
    """
    Read LANGUAGE from environment and return a LanguageConfig.

    Falls back to 'en' / 'English' if LANGUAGE is not set or unrecognised.
    Call load_dotenv() before this function if .env has not been loaded yet.
    """
    code = os.environ.get("LANGUAGE", _DEFAULT_BCP47).strip().lower()
    prompt_lang = _BCP47_TO_PROMPT_LANG.get(code)
    if prompt_lang is None:
        import logging
        logging.getLogger(__name__).warning(
            f"LANGUAGE='{code}' is not in the BCP-47 table. "
            f"Falling back to '{_DEFAULT_BCP47}' / 'English'."
        )
        code = _DEFAULT_BCP47
        prompt_lang = "English"
    return LanguageConfig(bcp47_code=code, prompt_lang=prompt_lang)

"""
Translation layer — powers the Transcript "View English" toggle.

Provider: **Sarvam LLM** (sole provider; 3-key rotation handled in app/utils/sarvam.py).
The transcript is stored in the original language; the app shows a one-tap English translation.
"""

from __future__ import annotations

import logging
import re
from typing import Dict, Any, List, Optional, Protocol

from app.config import settings  # noqa: F401  (kept for parity / future config)

logger = logging.getLogger(__name__)

# Languages we expect from Indian telecalling (ISO-ish codes used in the app)
SUPPORTED_LANGS = {"hi": "Hindi", "te": "Telugu", "ta": "Tamil", "kn": "Kannada", "en": "English"}


class Translator(Protocol):
    def translate(self, text: str, source_lang: str, target_lang: str = "en") -> str: ...


class SarvamTranslator:
    """Dedicated Sarvam Indic translator; fails open to the original text."""

    def translate(self, text: str, source_lang: str, target_lang: str = "en") -> str:
        if not text.strip():
            return text
        from app.utils.sarvam import sarvam_translate_text
        src = _provider_language_code(source_lang, allow_auto=True)
        tgt = _provider_language_code(target_lang)
        try:
            translated = [
                sarvam_translate_text(
                    chunk,
                    source_language_code=src,
                    target_language_code=tgt,
                )
                for chunk in _translation_chunks(text)
            ]
            out = " ".join(part for part in translated if part).strip()
            return out or text
        except Exception as e:
            logger.error(f"Sarvam translation failed ({src}->{tgt}): {e}")
            return text  # fail open: show original rather than nothing


def get_translator() -> Translator:
    return SarvamTranslator()


def detect_language(text: str) -> str:
    """Cheap script-based language guess (decides whether 'View English' is even needed)."""
    if re.search(r"[ऀ-ॿ]", text):   # Devanagari (Hindi/Marathi)
        return "hi"
    if re.search(r"[ఀ-౿]", text):   # Telugu
        return "te"
    if re.search(r"[஀-௿]", text):   # Tamil
        return "ta"
    if re.search(r"[ಀ-೿]", text):   # Kannada
        return "kn"
    return "en"


def _provider_language_code(language: str, *, allow_auto: bool = False) -> str:
    """Normalise app/ASR language tags to Sarvam's translation API codes."""
    raw = (language or "").strip()
    if allow_auto and raw in ("", "unknown", "auto"):
        return "auto"
    base = raw.split("-")[0].lower()
    return f"{base or 'en'}-IN"


def _translation_chunks(text: str, max_chars: int = 950) -> List[str]:
    """Split long text under Mayura's 1,000-character request limit."""
    remaining = text.strip()
    chunks: List[str] = []
    while len(remaining) > max_chars:
        window = remaining[: max_chars + 1]
        cut = max(window.rfind(mark) for mark in ("\n", ".", "?", "!", " "))
        if cut < max_chars // 2:
            cut = max_chars
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    if remaining:
        chunks.append(remaining)
    return chunks


def translate_turns(turns: List[Dict[str, Any]], source_lang: str, target_lang: str = "en") -> List[Dict[str, Any]]:
    """
    Translate each diarized turn with Sarvam's dedicated Indic translation API.
    Original content and speaker roles remain untouched so the app can toggle
    instantly without speaker/turn reassembly errors.
    """
    translator = get_translator()
    result = []
    for t in turns:
        nt = dict(t)
        original = t.get("content", "")
        nt["content_translated"] = translator.translate(
            original,
            source_lang,
            target_lang,
        )
        result.append(nt)
    return result


def translate_strings(strings: List[str], target_lang: str = "en") -> List[str]:
    """
    Translate a list of free-text UI strings to `target_lang`, index-aligned, via tool-calling.
    Powers the Score / AI-Summary "View English" toggle. Fails open (returns originals on error).
    """
    items = [(i, s) for i, s in enumerate(strings) if isinstance(s, str) and s.strip()]
    if not items:
        return list(strings)
    translator = get_translator()
    result = list(strings)
    for orig_idx, text in items:
        source_lang = detect_language(text)
        if source_lang != target_lang:
            result[orig_idx] = translator.translate(text, source_lang, target_lang)
    return result


def translate_text(text: str, source_lang: Optional[str] = None, target_lang: str = "en") -> str:
    src = source_lang or detect_language(text)
    return text if src == target_lang else get_translator().translate(text, src, target_lang)

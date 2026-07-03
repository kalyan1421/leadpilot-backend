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
    """Sarvam LLM translator. Handles 3-key rotation internally; fails open (returns original)."""

    def translate(self, text: str, source_lang: str, target_lang: str = "en") -> str:
        if not text.strip():
            return text
        from app.utils.sarvam import sarvam_chat
        src = SUPPORTED_LANGS.get(source_lang, source_lang)
        tgt = SUPPORTED_LANGS.get(target_lang, target_lang)
        try:
            out = sarvam_chat(
                [
                    {"role": "system", "content": f"You are a translator. Translate {src} to {tgt}. "
                                                   f"Return ONLY the translation, preserving line breaks. No notes."},
                    {"role": "user", "content": text},
                ],
                temperature=0.0, max_tokens=2000,
            )
            return re.sub(r"<think>.*?</think>", "", out, flags=re.DOTALL).strip()
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


_TRANSLATE_SCHEMA = {
    "type": "object",
    "properties": {"translations": {"type": "array", "items": {"type": "object", "properties": {
        "turn": {"type": "integer"}, "text": {"type": "string"}}, "required": ["turn", "text"]}}},
    "required": ["translations"],
}


def translate_turns(turns: List[Dict[str, Any]], source_lang: str, target_lang: str = "en") -> List[Dict[str, Any]]:
    """
    Translate transcript turns in ONE structured (tool-calling) call, aligned by turn number.
    Reliable on Sarvam's reasoning models (plain-prompt JSON returns empty/garbled). Returns new
    turns with `content_translated` added — original `content` preserved so the toggle flips instantly.
    """
    from app.utils.sarvam import sarvam_extract
    texts = [t.get("content", "") for t in turns]
    if not any(texts):
        return turns
    src = SUPPORTED_LANGS.get(source_lang, source_lang)
    tgt = SUPPORTED_LANGS.get(target_lang, target_lang)
    numbered = "\n".join(f"{i + 1}. {tx}" for i, tx in enumerate(texts) if tx)
    by_turn: Dict[int, str] = {}
    try:
        out = sarvam_extract(
            [
                {"role": "system", "content": f"Translate each numbered line from {src} to natural, faithful {tgt}. "
                                               f"Return one entry per line, preserving its number. Translate meaning, not transliteration."},
                {"role": "user", "content": numbered},
            ],
            schema=_TRANSLATE_SCHEMA, tool_name="record_translations", max_tokens=4000,
        )
        by_turn = {item.get("turn"): item.get("text", "") for item in (out.get("translations") or [])
                   if isinstance(item.get("turn"), int)}
    except Exception as e:
        logger.error(f"translate_turns failed ({src}->{tgt}): {e}")

    result = []
    for i, t in enumerate(turns, 1):
        nt = dict(t)
        nt["content_translated"] = by_turn.get(i) or t.get("content", "")  # fail open to original
        result.append(nt)
    return result


def translate_strings(strings: List[str], target_lang: str = "en") -> List[str]:
    """
    Translate a list of free-text UI strings to `target_lang`, index-aligned, via tool-calling.
    Powers the Score / AI-Summary "View English" toggle. Fails open (returns originals on error).
    """
    from app.utils.sarvam import sarvam_extract
    items = [(i, s) for i, s in enumerate(strings) if isinstance(s, str) and s.strip()]
    if not items:
        return list(strings)
    tgt = SUPPORTED_LANGS.get(target_lang, target_lang)
    numbered = "\n".join(f"{pos}. {s}" for pos, (_, s) in enumerate(items, 1))
    by_pos: Dict[int, str] = {}
    try:
        out = sarvam_extract(
            [
                {"role": "system", "content": f"Translate each numbered line to natural, faithful {tgt}. "
                                               f"Return one entry per line, preserving its number. Meaning, not transliteration. "
                                               f"If a line is already {tgt}, return it unchanged."},
                {"role": "user", "content": numbered},
            ],
            schema=_TRANSLATE_SCHEMA, tool_name="record_translations", max_tokens=4000,
        )
        by_pos = {item.get("turn"): item.get("text", "") for item in (out.get("translations") or [])
                  if isinstance(item.get("turn"), int)}
    except Exception as e:
        logger.error(f"translate_strings failed (->{tgt}): {e}")
    result = list(strings)
    for pos, (orig_idx, _s) in enumerate(items, 1):
        if by_pos.get(pos):
            result[orig_idx] = by_pos[pos]
    return result


def translate_text(text: str, source_lang: Optional[str] = None, target_lang: str = "en") -> str:
    src = source_lang or detect_language(text)
    return text if src == target_lang else get_translator().translate(text, src, target_lang)

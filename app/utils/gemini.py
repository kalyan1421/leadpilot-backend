"""
Gemini reasoning provider — optional, stronger GENERAL reasoner for post-call analysis.

WHY THIS EXISTS
  Sarvam is Indic-SOTA for STT / diarization / language understanding, but a *weak
  general reasoner* — which is exactly why our numeric scores ship as `beta`. Gemini
  3.1 Pro is a frontier reasoner with native structured output and a thinking budget,
  so it can materially raise trust in the SCORING step.

WHAT IT DOES / DOESN'T
  This ONLY swaps the "reasoning brain" for post-call analysis (scoring, sentiment,
  digests). STT + 2-speaker diarization always stay on Sarvam (Saaras v3). The active
  reasoning provider is chosen by REASONING_PROVIDER in .env ("sarvam" | "gemini").

DROP-IN CONTRACT
  gemini_extract(messages, schema=..., tool_name=..., model=..., max_tokens=...) mirrors
  sarvam_extract() 1:1 and returns a schema-valid dict. Instead of forced tool-calling we
  use Gemini's native structured output (responseMimeType=application/json + responseSchema)
  — the Gemini-equivalent guarantee of valid JSON. Comma-separated GEMINI_API_KEYS rotate
  on 429/503, same pattern as the Sarvam client.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_key_idx = 0  # rotates forward across GEMINI_API_KEYS on quota/rate errors
_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def _keys() -> List[str]:
    return [k.strip() for k in (settings.gemini_api_keys or "").split(",") if k.strip()]


def _to_gemini(messages: List[Dict[str, str]]):
    """Convert our [{role, content}] messages → Gemini (systemInstruction, contents)."""
    sys_parts: List[str] = []
    contents: List[Dict[str, Any]] = []
    for m in messages:
        role = (m.get("role") or "user").lower()
        text = m.get("content") or ""
        if role == "system":
            sys_parts.append(text)
        else:
            contents.append({"role": "model" if role == "assistant" else "user",
                             "parts": [{"text": text}]})
    system_instruction = {"parts": [{"text": "\n\n".join(sys_parts)}]} if sys_parts else None
    return system_instruction, contents


def _parse(data: Dict[str, Any]) -> Dict[str, Any]:
    """Pull the JSON answer out of a generateContent response (ignores thought parts)."""
    cands = data.get("candidates") or []
    if not cands:
        pf = (data.get("promptFeedback") or {}).get("blockReason")
        raise ValueError(f"Gemini returned no candidates (blockReason={pf})")
    cand = cands[0]
    finish = cand.get("finishReason")
    parts = ((cand.get("content") or {}).get("parts")) or []
    text = "".join(p.get("text", "") for p in parts
                   if isinstance(p, dict) and not p.get("thought")).strip()
    if not text:
        raise ValueError(f"Gemini returned empty content (finishReason={finish})")
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        s, e = text.find("{"), text.rfind("}") + 1
        if s != -1 and e > s:
            return json.loads(text[s:e])
        raise ValueError(f"Gemini output was not valid JSON: {text[:200]}")


def gemini_extract(messages: List[Dict[str, str]], *, schema: Dict[str, Any],
                   tool_name: str = "extract", model: Optional[str] = None,
                   max_tokens: Optional[int] = 4000,
                   thinking_level: Optional[str] = None) -> Dict[str, Any]:
    """
    Schema-constrained structured output from Gemini — a drop-in for sarvam_extract().
    `tool_name` is accepted for signature parity (Gemini uses responseSchema, not tools).
    Rotates GEMINI_API_KEYS on 429/503. Raises on hard failure (caller retries).
    """
    keys = _keys()
    if not keys:
        raise RuntimeError("No GEMINI_API_KEYS configured in .env")
    mdl = model or settings.gemini_model
    level = thinking_level or settings.gemini_thinking_level
    system_instruction, contents = _to_gemini(messages)

    gen_cfg: Dict[str, Any] = {
        "responseMimeType": "application/json",
        "responseSchema": schema,
        "temperature": 0.2,
        # Thinking tokens share the output budget on Gemini 3 — cap generously so a
        # thinking burst never truncates the JSON answer (billed on actual usage, not the cap).
        "maxOutputTokens": max(int(max_tokens or 4096), 32768),
    }
    if level:
        gen_cfg["thinkingConfig"] = {"thinkingLevel": level}

    body: Dict[str, Any] = {"contents": contents, "generationConfig": gen_cfg}
    if system_instruction:
        body["systemInstruction"] = system_instruction

    global _key_idx
    last_err: Optional[Exception] = None
    for _ in range(len(keys)):
        with _lock:
            idx = _key_idx % len(keys)
            key = keys[idx]
        # Key goes in a header, not the URL: httpx.HTTPStatusError.__str__ embeds
        # the full request URL, and callers log that exception message verbatim —
        # a `?key=...` query param would leak the key into application logs.
        url = f"{settings.gemini_base_url}/models/{mdl}:generateContent"
        try:
            r = httpx.post(url, json=body, timeout=_TIMEOUT, headers={"x-goog-api-key": key})
            if r.status_code in (429, 503):  # quota / rate limit / overloaded → rotate
                last_err = RuntimeError(f"Gemini {r.status_code}: {r.text[:160]}")
                logger.warning(f"Gemini key #{idx + 1}/{len(keys)} {r.status_code}; rotating")
                with _lock:
                    if _key_idx % len(keys) == idx:
                        _key_idx = (idx + 1) % len(keys)
                continue
            r.raise_for_status()
            return _parse(r.json())
        except httpx.HTTPStatusError:
            raise  # 4xx (bad request / auth) — don't silently rotate; surface it
        except Exception as e:  # noqa: BLE001 — transient network/parse; brief retry
            last_err = e
            logger.warning(f"Gemini call failed on key #{idx + 1}: {str(e)[:120]}")
            time.sleep(1.5)
            continue
    raise RuntimeError(f"Gemini extract failed after {len(keys)} key(s): {last_err}")

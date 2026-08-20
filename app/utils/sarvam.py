"""
Sarvam AI — the SOLE provider for the whole pipeline:
  - Speech-to-Text + speaker DIARIZATION (batch job, saaras:v3)  → transcript.py
  - Translation (LLM)                                            → translation.py
  - LLM analysis + memory synthesis (sarvam-30b/105b)            → lead_analyzer.py / memory_bubble.py

THREE-KEY ROTATION: keys come from SARVAM_API_KEYS (comma-separated in .env). When a
key hits insufficient-credits (403) or rate-limit (429), we advance to the next key and
retry the SAME call. When all keys are exhausted we raise — caller decides what to do.

This is the one place provider auth/rotation lives, so swapping or adding keys is config-only.
"""

from __future__ import annotations

import glob
import json
import logging
import os
import tempfile
import threading
import time
from typing import Any, Callable, Dict, List, Optional

from app.config import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_key_idx = 0  # which key we're currently using (module-global, rotates forward)

# Brief pause before rotating on a 429 specifically — without it, a burst of rate-limit
# errors across all keys burns through the whole rotation in milliseconds with zero
# cooldown. Not applied to 403 (insufficient credits) — no amount of waiting fixes an
# exhausted quota, so that case should still rotate immediately.
_RATE_LIMIT_BACKOFF_SECONDS = 0.75
ROLE_MAPPING_VERSION = "semantic_v1"


def _keys() -> List[str]:
    return [k.strip() for k in (settings.sarvam_api_keys or "").split(",") if k.strip()]


def _client_for(key: str):
    from sarvamai import SarvamAI
    return SarvamAI(api_subscription_key=key)


def _is_quota_error(e: Exception) -> bool:
    """
    Only a 403 (insufficient credits) or 429 (rate limit) should rotate keys.
    Classify by status_code + the structured body message — NOT the raw repr, whose
    header dump contains noise like 'payment=()' that caused false rotations.
    """
    # 401 = invalid key (config error) — let it propagate, don't burn through all keys.
    if getattr(e, "status_code", None) in (403, 429):
        return True
    body = getattr(e, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        msg = str(err.get("message", "") if isinstance(err, dict) else err).lower()
    return any(x in msg for x in (
        "insufficient", "quota", "credit", "exhaust", "rate limit", "too many requests",
    ))


def _is_rate_limit_error(e: Exception) -> bool:
    """429 specifically, as distinct from 403 (insufficient credits) — used to decide
    whether a brief backoff is worth it before rotating (see _RATE_LIMIT_BACKOFF_SECONDS)."""
    if getattr(e, "status_code", None) == 429:
        return True
    body = getattr(e, "body", None)
    msg = ""
    if isinstance(body, dict):
        err = body.get("error")
        msg = str(err.get("message", "") if isinstance(err, dict) else err).lower()
    return any(x in msg for x in ("rate limit", "too many requests"))


def _run_with_rotation(call: Callable[[Any], Any]) -> Any:
    """Run `call(client)`, rotating through all keys on quota/rate errors."""
    global _key_idx
    keys = _keys()
    if not keys:
        raise RuntimeError("No SARVAM_API_KEYS configured in .env")
    last_err: Optional[Exception] = None
    for _ in range(len(keys)):
        with _lock:
            idx = _key_idx % len(keys)
            key = keys[idx]
        try:
            return call(_client_for(key))
        except Exception as e:  # noqa: BLE001 — we classify below
            last_err = e
            if _is_quota_error(e):
                logger.warning(f"Sarvam key #{idx + 1}/{len(keys)} exhausted/limited "
                               f"({str(e)[:100]}); rotating to next key")
                if _is_rate_limit_error(e):
                    time.sleep(_RATE_LIMIT_BACKOFF_SECONDS)
                with _lock:
                    if _key_idx % len(keys) == idx:  # only advance once per exhaustion
                        _key_idx = (idx + 1) % len(keys)
                continue
            raise
    raise RuntimeError(f"All {len(keys)} Sarvam keys exhausted/limited. Last error: {last_err}")


# ---------------------------------------------------------------------------
# LLM chat (analysis, memory synthesis, translation)
# ---------------------------------------------------------------------------

def sarvam_chat(messages: List[Dict[str, str]], *, model: Optional[str] = None,
                temperature: Optional[float] = 0.2, max_tokens: Optional[int] = 4000,
                reasoning_effort: Optional[str] = "low") -> str:
    """
    One chat completion → the assistant text. Rotates keys on exhaustion.

    NOTE: sarvam-30b/105b are REASONING models — with the default effort they spend the
    whole token budget on hidden reasoning and return EMPTY content. We force
    reasoning_effort='low' so the budget goes to the actual answer (right for our
    structured-JSON extraction). Verified live: 'low' returns content, default returns None.
    """
    mdl = model or settings.sarvam_chat_model

    def call(client):
        kw: Dict[str, Any] = {"messages": messages, "model": mdl}
        if temperature is not None:
            kw["temperature"] = temperature
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kw["reasoning_effort"] = reasoning_effort
        resp = client.chat.completions(**kw)
        return resp.choices[0].message.content or ""

    return _run_with_rotation(call)


def sarvam_extract(messages: List[Dict[str, str]], *, schema: Dict[str, Any],
                   tool_name: str = "extract", model: Optional[str] = None,
                   max_tokens: Optional[int] = 4000,
                   reasoning_effort: Optional[str] = "low") -> Dict[str, Any]:
    """
    Schema-constrained structured output via FORCED tool-calling — the 2026 way to
    guarantee valid JSON (no 'return only JSON' prompt-and-pray, no unparseable output).

    The model's tool arguments are constrained to `schema`; we parse + return them as a
    dict. Going straight to the tool call also stops the reasoning model from burning the
    whole token budget on hidden thinking. Rotates keys on exhaustion.
    """
    mdl = model or settings.sarvam_chat_model
    tools = [{"type": "function", "function": {
        "name": tool_name,
        "description": "Return the structured analysis result. Fill every field.",
        "parameters": schema,
    }}]
    tool_choice = {"type": "function", "function": {"name": tool_name}}

    def call(client):
        kw: Dict[str, Any] = {"messages": messages, "model": mdl,
                              "tools": tools, "tool_choice": tool_choice}
        if max_tokens is not None:
            kw["max_tokens"] = max_tokens
        if reasoning_effort is not None:
            kw["reasoning_effort"] = reasoning_effort
        resp = client.chat.completions(**kw)
        msg = resp.choices[0].message
        tcs = getattr(msg, "tool_calls", None) or []
        if tcs:
            args = tcs[0].function.arguments
            if isinstance(args, str):
                args = args.strip()
                return json.loads(args) if args else {}
            return args or {}
        # Fallback: model answered in content instead of a tool call.
        content = (getattr(msg, "content", None) or "").strip()
        if content:
            start, end = content.find("{"), content.rfind("}") + 1
            if start != -1 and end > start:
                return json.loads(content[start:end])
        raise ValueError("Sarvam returned no tool_call and no parseable content")

    return _run_with_rotation(call)


def sarvam_translate_text(
    text: str,
    *,
    source_language_code: str,
    target_language_code: str = "en-IN",
    mode: str = "modern-colloquial",
) -> str:
    """Translate with Sarvam's dedicated Indic translation API.

    Translation used to go through the general-purpose chat model. That model
    is useful for reasoning, but it paraphrased and occasionally invented
    details in short Telugu call turns. Mayura/Sarvam-Translate is the provider
    API designed for faithful Indic translation and preserves conversational
    meaning substantially better.
    """
    if not text.strip():
        return text

    def call(client):
        model = settings.sarvam_translate_model
        response = client.text.translate(
            input=text,
            source_language_code=source_language_code,
            target_language_code=target_language_code,
            # Sarvam-Translate supports formal mode only; Mayura supports the
            # conversational mode that is a better fit for call transcripts.
            mode="formal" if model == "sarvam-translate:v1" else mode,
            model=model,
        )
        return (response.translated_text or "").strip()

    return _run_with_rotation(call)


# ---------------------------------------------------------------------------
# Speech-to-Text + diarization (batch job)
# ---------------------------------------------------------------------------

def _seconds_to_mmss(sec: Any) -> str:
    try:
        s = max(0, int(float(sec)))
    except (TypeError, ValueError):
        return "00:00"
    return f"{s // 60:02d}:{s % 60:02d}"


def _infer_agent_speaker(entries: List[Dict[str, Any]], speaker_ids: List[Any]) -> Any:
    """Identify the telecaller by conversational role, never turn order.

    Call recordings can begin after the lead has already said hello, so "first
    speaker = agent" swaps the full conversation surprisingly often. The STT
    provider supplies speaker separation but not business roles; this small
    constrained classification step maps those stable speaker IDs to the person
    representing the company.
    """
    if len(speaker_ids) <= 1:
        return speaker_ids[0] if speaker_ids else None

    labels = {sid: f"SPEAKER_{i + 1}" for i, sid in enumerate(speaker_ids)}
    label_to_id = {label: sid for sid, label in labels.items()}
    lines = []
    used_chars = 0
    for entry in entries:
        text = (entry.get("transcript") or "").strip()
        sid = entry.get("speaker_id")
        if not text or sid not in labels:
            continue
        line = f"{labels[sid]}: {text}"
        # Role cues are concentrated in introductions, qualification questions,
        # and product explanations. Keep enough conversation for those cues
        # without sending an unbounded transcript to the classifier.
        if used_chars + len(line) > 8000:
            break
        lines.append(line)
        used_chars += len(line)

    schema = {
        "type": "object",
        "properties": {
            "agent_speaker": {
                "type": "string",
                "enum": list(label_to_id),
            },
            "confidence": {
                "type": "string",
                "enum": ["high", "medium", "low"],
            },
        },
        "required": ["agent_speaker", "confidence"],
    }
    try:
        result = sarvam_extract(
            [
                {
                    "role": "system",
                    "content": (
                        "Identify which diarized speaker is the TELECALLER/AGENT in this sales call. "
                        "The agent represents the company: they may introduce the company, explain "
                        "the product, ask qualification questions, handle objections, or arrange a "
                        "follow-up. The other speaker is the LEAD/CUSTOMER. Do not assume the first "
                        "speaker is the agent. The conversation may be Telugu, English, or code-mixed."
                    ),
                },
                {"role": "user", "content": "\n".join(lines)},
            ],
            schema=schema,
            tool_name="identify_call_roles",
            max_tokens=500,
        )
        inferred = label_to_id.get(result.get("agent_speaker"))
        if inferred is not None:
            return inferred
    except Exception as exc:  # fail soft; STT must still be usable
        logger.warning("Speaker-role classification failed; using fallback: %s", exc)

    return None


def reclassify_transcript_roles(turns: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Repair AGENT/USER labels while preserving turn order and text.

    Used for older stored transcripts created before semantic speaker-role
    mapping was added. Requires the stable ``speaker_id`` emitted by Sarvam's
    diarizer; if it is unavailable, the original labels are preserved.
    """
    speaker_ids: List[Any] = []
    entries: List[Dict[str, Any]] = []
    for turn in turns:
        text = (turn.get("content") or "").strip()
        sid = turn.get("speaker_id")
        if not text or sid is None:
            continue
        if sid not in speaker_ids:
            speaker_ids.append(sid)
        entries.append({"speaker_id": sid, "transcript": text})

    if len(speaker_ids) < 2:
        return [dict(turn) for turn in turns]

    agent_speaker = _infer_agent_speaker(entries, speaker_ids)
    if agent_speaker is None:
        raise RuntimeError("Speaker-role classifier returned no agent")
    repaired = []
    for turn in turns:
        updated = dict(turn)
        sid = turn.get("speaker_id")
        if sid is not None:
            updated["role"] = "AGENT" if sid == agent_speaker else "USER"
        repaired.append(updated)
    return repaired


def _parse_diarized(output_dir: str) -> Dict[str, Any]:
    """
    Turn Sarvam's diarized JSON into our turn contract:
      {"turns": [{"role":"AGENT|USER","content","timestamp","speaker_id"}], "full_text", "language"}

    Speaker→role mapping is inferred from what each participant says. Sarvam
    diarization separates voices but does not identify which voice belongs to
    the telecaller, and the lead may be the first audible speaker.
    """
    files = sorted(glob.glob(os.path.join(output_dir, "*.json")))
    if not files:
        return {"turns": [], "full_text": "", "language": "unknown", "quality": "failed", "error": "no Sarvam output json"}

    with open(files[0], "r", encoding="utf-8") as f:
        data = json.load(f)

    lang = (data.get("language_code") or "unknown").split("-")[0] or "unknown"
    entries = (data.get("diarized_transcript") or {}).get("entries") or []
    turns: List[Dict[str, Any]] = []
    role_mapping = ROLE_MAPPING_VERSION

    if entries:
        # Ignore empty segments when establishing the real speaker set.
        order: List[Any] = []
        for e in entries:
            if not (e.get("transcript") or "").strip():
                continue
            sid = e.get("speaker_id")
            if sid not in order:
                order.append(sid)
        agent_speaker = _infer_agent_speaker(entries, order)
        if agent_speaker is None:
            # Keep transcription available during a temporary classifier/API
            # outage, but do not mark this mapping as semantically verified.
            agent_speaker = order[0] if order else None
            role_mapping = "first_speaker_fallback_v1"
        role_map = {sid: ("AGENT" if sid == agent_speaker else "USER") for sid in order}
        for e in entries:
            text = (e.get("transcript") or "").strip()
            if not text:
                continue
            turns.append({
                "role": role_map.get(e.get("speaker_id"), "USER"),
                "content": text,
                "timestamp": _seconds_to_mmss(e.get("start_time_seconds")),
                "speaker_id": e.get("speaker_id"),
            })
    else:
        text = (data.get("transcript") or "").strip()
        if text:
            turns = [{"role": "AGENT", "content": text, "timestamp": "00:00"}]

    words = sum(len((t.get("content") or "").split()) for t in turns)
    # Heuristic quality flag (no per-word confidence on the batch API): failed / low / ok.
    quality = "failed" if not turns else ("low" if (len(turns) < 2 or words < 20) else "ok")
    return {
        "turns": turns,
        "full_text": " ".join(t["content"] for t in turns),
        "language": lang,
        "quality": quality,
        "role_mapping": role_mapping,
    }


def transcribe_file(audio_path: str, *, mode: Optional[str] = None,
                    with_diarization: bool = True, num_speakers: int = 2,
                    language_code: str = "unknown") -> Dict[str, Any]:
    """
    Batch STT with speaker diarization. Returns our turn contract. Rotates keys.
    `mode`: 'transcribe' (original language) or 'translate' (→ English). Default from config.
    """
    if not os.path.exists(audio_path):
        return {"turns": [], "full_text": "", "language": "unknown", "quality": "failed", "error": "file not found"}

    stt_mode = mode or settings.sarvam_stt_mode

    def call(client):
        job = client.speech_to_text_job.create_job(
            model=settings.sarvam_stt_model,
            mode=stt_mode,
            with_diarization=with_diarization,
            with_timestamps=True,
            language_code=language_code,
            num_speakers=num_speakers,
        )
        job.upload_files(file_paths=[audio_path], timeout=600)
        job.start()
        # Diarization is BATCH-only on Sarvam (sync STT can't do it), so this job is the
        # only path to a 2-speaker transcript. Its latency is Sarvam's queue/processing
        # (~30s for a 2-min call), not our code — poll tighter (2s vs default 5s) to shave
        # the detection slack. This runs in the background ProcessingJob queue, so it never
        # blocks the user (upload → 202 + call_id → poll status).
        job.wait_until_complete(poll_interval=2)
        if job.is_failed():
            raise RuntimeError(f"Sarvam STT job {job.job_id} failed")
        out_dir = tempfile.mkdtemp(prefix="sarvam_stt_")
        job.download_outputs(output_dir=out_dir)
        return _parse_diarized(out_dir)

    return _run_with_rotation(call)

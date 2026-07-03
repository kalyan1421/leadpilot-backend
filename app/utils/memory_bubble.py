"""
Memory Bubble engine — the LeadPilot moat.

A memory bubble is a per-contact (phone number) cumulative store of everything
the AI has learned across ALL calls with that person. It powers:
  - The Lead Detail "Memory Bubble" card (facts with Call #N attribution)
  - AI script generation before the next call
  - Cumulative BANT / verdict evolution

Design:
  - Keyed by `contact_key` (phone number in production; a name-slug today since
    the current dataset has no phone field — backend swaps this for `lead.phone`).
  - Rebuilt incrementally: each new call's lead_analysis is MERGED into the bubble.
    We re-synthesise with the LLM so duplicate / superseded facts collapse
    (e.g. "budget 50L" in call 1 -> "budget confirmed 80L-1Cr" in call 2).
  - Every fact carries: category, the call_index it came from, and confidence.

The LLM call is small (summaries only, never raw transcripts), so it stays fast
and cheap even with many calls.
"""

import json
import logging
import re
from typing import Dict, Any, List, Optional

from app.config import settings
from app.utils.sarvam import sarvam_extract

logger = logging.getLogger(__name__)

# Categories shown in the Figma memory bubble (colored dots map to these)
FACT_CATEGORIES = [
    "budget",          # green   — "Confirmed budget ₹80L–₹1Cr"
    "concern",         # orange  — "Worried about project completion timeline"
    "decision_maker",  # purple  — "Wife's opinion needed before decision"
    "preference",      # purple  — "Prefers Phase 2 over Phase 3 location"
    "commitment",      # blue    — "Agreed to site visit this weekend"
    "objection",       # red     — "Thinks price is higher than Sobha"
    "personal",        # gray    — "Works in IT, has a 4-year-old"
]

_MEMORY_PROMPT = """You are building a cumulative MEMORY BUBBLE for a sales prospect.
You are given the AI analysis of every call made to this person so far, in order.
Synthesise a single, deduplicated, up-to-date memory of what matters about THIS prospect.

When a later call updates an earlier fact (budget changed, objection resolved), keep ONLY
the latest version. Attribute each fact to the call number it most recently came from.

CALLS (oldest first):
{calls_block}

Return ONLY this JSON (no markdown, no preamble):

{{
  "facts": [
    {{"category": "budget|concern|decision_maker|preference|commitment|objection|personal",
      "text": "short fact in plain language, max 12 words",
      "call_index": 2,
      "confidence": "high|medium|low"}}
  ],
  "cumulative_bant": {{
    "budget": {{"score": 0, "note": "latest budget signal"}},
    "authority": {{"score": 0, "note": "who decides"}},
    "need": {{"score": 0, "note": "core need"}},
    "timeline": {{"score": 0, "note": "when they'll decide"}}
  }},
  "running_verdict": "Hot|Warm|Cold|Junk",
  "sentiment_trend": "improving|declining|flat|mixed",
  "open_objections": ["unresolved objection 1"],
  "pending_commitments": ["promise that is not yet fulfilled"],
  "next_call_strategy": "one sentence: how the agent should open the next call",
  "headline": "one line summary of this prospect's state"
}}

Rules:
- Max 8 facts. Prioritise budget, decision_maker, concern, commitment.
- call_index is 1-based and must reference a call shown above.
- cumulative_bant scores are 0-25 each, reflecting the BEST evidence across all calls.
- If only one call exists, still produce the full structure.
"""

# Schema for FORCED tool-calling (guaranteed-valid JSON — plain-prompt JSON is unreliable
# on Sarvam's reasoning models, same reason the analyzer uses tool-calling).
_MEMORY_SCHEMA = {
    "type": "object",
    "properties": {
        "facts": {"type": "array", "items": {"type": "object", "properties": {
            "category": {"type": "string", "enum": FACT_CATEGORIES},
            "text": {"type": "string"},
            "call_index": {"type": "integer"},
            "confidence": {"type": "string", "enum": ["high", "medium", "low"]},
        }, "required": ["category", "text"]}},
        "cumulative_bant": {"type": "object", "properties": {
            "budget": {"type": "object", "properties": {"score": {"type": "integer"}, "note": {"type": "string"}}},
            "authority": {"type": "object", "properties": {"score": {"type": "integer"}, "note": {"type": "string"}}},
            "need": {"type": "object", "properties": {"score": {"type": "integer"}, "note": {"type": "string"}}},
            "timeline": {"type": "object", "properties": {"score": {"type": "integer"}, "note": {"type": "string"}}},
        }},
        "running_verdict": {"type": "string", "enum": ["Hot", "Warm", "Cold", "Junk"]},
        "sentiment_trend": {"type": "string", "enum": ["improving", "declining", "flat", "mixed"]},
        "open_objections": {"type": "array", "items": {"type": "string"}},
        "pending_commitments": {"type": "array", "items": {"type": "string"}},
        "next_call_strategy": {"type": "string"},
        "headline": {"type": "string"},
    },
    "required": ["facts", "running_verdict", "headline"],
}


class MemoryBubbleBuilder:
    def __init__(self):
        # Sarvam is the sole provider (rotation handled in app/utils/sarvam.py).
        self.model = settings.sarvam_chat_model

    def build(self, contact_key: str, calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """
        calls: list (oldest first) of dicts:
          {"call_id", "timestamp", "analysis": <lead_analysis dict>}
        Returns the memory bubble dict, or a minimal empty bubble on failure.
        """
        if not calls:
            return self._empty(contact_key)

        calls_block = self._format_calls(calls)

        try:
            logger.info(f"Building memory bubble for {contact_key} from {len(calls)} call(s)")
            data = sarvam_extract(
                [
                    {"role": "system", "content": "You synthesise cumulative sales memory for a prospect."},
                    {"role": "user", "content": _MEMORY_PROMPT.format(calls_block=calls_block)},
                ],
                schema=_MEMORY_SCHEMA, tool_name="record_memory", model=self.model, max_tokens=2000,
            )
            if not data:
                return self._empty(contact_key)

            data["contact_key"] = contact_key
            data["total_calls"] = len(calls)
            data["last_call_id"] = calls[-1].get("call_id")
            data["last_call_at"] = calls[-1].get("timestamp")
            data["facts"] = self._sanitise_facts(data.get("facts", []), len(calls))
            return data
        except Exception as e:
            logger.error(f"Memory bubble build failed for {contact_key}: {e}", exc_info=True)
            return self._empty(contact_key)

    # ------------------------------------------------------------------

    @staticmethod
    def _format_calls(calls: List[Dict[str, Any]]) -> str:
        """Compact each call's analysis into a short block (never raw transcript)."""
        blocks = []
        for i, c in enumerate(calls, 1):
            a = c.get("analysis") or {}
            summary = a.get("call_summary") or {}
            ents = a.get("entities") or {}
            bant = a.get("bant_breakdown") or {}
            parts = [
                f"--- CALL {i} (id={c.get('call_id')}, at={c.get('timestamp')}) ---",
                f"verdict: {a.get('lead_verdict')} (bant {a.get('bant_score')})",
                f"headline: {summary.get('headline')}",
            ]
            if ents:
                parts.append(
                    "entities: "
                    + ", ".join(
                        f"{k}={ents.get(k)}"
                        for k in ("budget", "authority", "need", "timeline", "location", "product_interest")
                        if ents.get(k)
                    )
                )
            if ents.get("objections"):
                parts.append("objections: " + "; ".join(ents["objections"]))
            if summary.get("commitments_made"):
                parts.append("commitments: " + "; ".join(summary["commitments_made"]))
            if bant:
                parts.append(
                    "bant_notes: "
                    + ", ".join(f"{d}={bant.get(d, {}).get('reason')}" for d in ("budget", "authority", "need", "timeline"))
                )
            # Drop only fields that rendered as a bare null (": None"), not legit
            # content that happens to end in the word "None".
            blocks.append("\n".join(p for p in parts if p and not str(p).rstrip().endswith(": None")))
        return "\n\n".join(blocks)

    @staticmethod
    def _sanitise_facts(facts: List[Dict[str, Any]], n_calls: int) -> List[Dict[str, Any]]:
        clean = []
        for f in facts[:8]:
            cat = f.get("category", "personal")
            if cat not in FACT_CATEGORIES:
                cat = "personal"
            ci = f.get("call_index", n_calls)
            try:
                ci = max(1, min(int(ci), n_calls))
            except (ValueError, TypeError):
                ci = n_calls
            text = str(f.get("text", "")).strip()
            if text:
                clean.append({"category": cat, "text": text, "call_index": ci,
                              "confidence": f.get("confidence", "medium")})
        return clean

    @staticmethod
    def _strip(content: str) -> str:
        content = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL)
        content = re.sub(r"^```(?:json)?\s*", "", content, flags=re.MULTILINE)
        content = re.sub(r"\s*```\s*$", "", content, flags=re.MULTILINE)
        return content.strip()

    @staticmethod
    def _parse(content: str) -> Optional[Dict[str, Any]]:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass
        s, e = content.find("{"), content.rfind("}") + 1
        if s != -1 and e > s:
            try:
                return json.loads(content[s:e])
            except json.JSONDecodeError:
                pass
        logger.error(f"Could not parse memory bubble JSON: {content[:300]}")
        return None

    @staticmethod
    def _empty(contact_key: str) -> Dict[str, Any]:
        return {
            "contact_key": contact_key,
            "total_calls": 0,
            "facts": [],
            "cumulative_bant": {
                "budget": {"score": 0, "note": ""},
                "authority": {"score": 0, "note": ""},
                "need": {"score": 0, "note": ""},
                "timeline": {"score": 0, "note": ""},
            },
            "running_verdict": "Junk",
            "sentiment_trend": "flat",
            "open_objections": [],
            "pending_commitments": [],
            "next_call_strategy": "",
            "headline": "No history yet",
        }


_builder: Optional[MemoryBubbleBuilder] = None


def get_builder() -> MemoryBubbleBuilder:
    global _builder
    if _builder is None:
        _builder = MemoryBubbleBuilder()
    return _builder


def build_memory_bubble(contact_key: str, calls: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    return get_builder().build(contact_key, calls)


def contact_key_from_call_id(call_id: str) -> str:
    """
    TEMPORARY: derive a stable contact key from call_id so multiple calls to the
    same person group together in today's phone-less dataset.

    call_abdul_latif_02ce678a   -> abdul_latif
    call_nitish_1.2_0ec60fcd    -> nitish      (version marker dropped so repeat
    call_nitish_b1043aec        -> nitish       calls to one person group together)

    BACKEND: replace every call to this with the real `lead.phone`.
    """
    s = re.sub(r"^call_", "", call_id)
    s = re.sub(r"_[0-9a-f]{6,}$", "", s)   # strip trailing uuid fragment
    s = re.sub(r"_\d+\.\d+$", "", s)       # strip version marker like _1.2
    return s or call_id


def slugify_contact(name: str) -> str:
    """
    Stable contact_key from a lead's name — matches the slug embedded in call_ids
    so a Lead row and its uploaded calls group together.

    "Rakesh Sharma" -> "rakesh_sharma"   (same as contact_key_from_call_id of an
    uploaded recording named "Rakesh Sharma").

    BACKEND: key by normalised phone instead.
    """
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_") or "lead"

"""
Pre-Call Brief generator.

Produces the AI content the Pre-Call screen needs to actually be useful:
opening line, key talking points, a step-by-step call script, likely
objections + suggested responses, and a prep checklist — all grounded in the
Organisation Knowledge Base (industry/services/pricing/USPs/brand voice) and,
when they exist, this specific contact's memory bubble (facts, BANT,
open objections, pending commitments, running verdict) and any note left on
their pending follow-up.

Same design as LeadAnalyzer / MemoryBubbleBuilder: forced tool-calling via
sarvam_extract/gemini_extract so output is schema-valid JSON, never
parse-and-pray. Provider follows settings.reasoning_provider, same switch the
analyzer uses.

Without a memory bubble (brand-new lead, zero calls yet) this still produces
a sensible cold-open brief from the org KB alone — a lead's very first call
is exactly when a script matters most.
"""

import logging
import time
from typing import Any, Dict, List, Optional

from app.config import settings
from app.utils.sarvam import sarvam_extract

logger = logging.getLogger(__name__)

_MAX_RETRIES = 2
_RETRY_BASE = 2.0

_BRIEF_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "opening_line": {"type": "string"},
        "key_points": {"type": "array", "items": {"type": "string"}},
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "subtitle": {"type": "string"},
                },
                "required": ["title", "subtitle"],
            },
        },
        "objections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "response": {"type": "string"},
                },
                "required": ["question", "response"],
            },
        },
        "checklist": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["opening_line", "key_points", "steps", "checklist"],
}

_BRIEF_SYS = (
    "You are preparing an Indian telecaller to make (or continue) a sales call, grounded "
    "STRICTLY in the ORGANISATION CONTEXT below — never invent services, pricing, or a business "
    "type the org doesn't have. Produce a short, practical pre-call brief:\n"
    "- opening_line: ONE natural spoken line to start the call, referencing this specific "
    "contact's situation if known, else a warm generic opener for this business.\n"
    "- key_points: 3-5 short talking points the telecaller must raise this call — grounded in "
    "the org's services/USPs and, if known, this contact's stated needs/objections/commitments. "
    "Each under 14 words.\n"
    "- steps: an ordered call flow of 4-6 steps (e.g. opening, discovery, pitch, objection "
    "handling, closing — but phrase titles naturally for THIS business, not literally those "
    "words). Each step: a short title (2-4 words) and a one-sentence subtitle telling the "
    "telecaller what to actually do/ask at that point, tailored to this contact when known.\n"
    "- objections: 2-4 objections this contact is likely to raise (prioritise any already-known "
    "open objections), each with a short suggested response grounded in the org's USPs/pricing/"
    "competitors. If nothing is known yet, use the most common objections for this industry.\n"
    "- checklist: 3-6 short imperative prep items the telecaller should confirm/do THIS call — "
    "grounded in what this org actually needs to qualify a lead (NOT generic real-estate items "
    "unless the org genuinely is real estate). If there is a pending follow-up note or "
    "commitment from a prior call, include a checklist item to close that loop specifically.\n"
    "Keep everything concise, spoken-language, and specific to the org's industry — a cosmetic "
    "clinic's checklist looks nothing like a real-estate broker's."
)


class PreCallBriefGenerator:
    def __init__(self):
        self.provider = (settings.reasoning_provider or "sarvam").lower()
        self.model = settings.gemini_model if self.provider == "gemini" else settings.sarvam_chat_model

    def generate(
        self,
        *,
        lead_name: str,
        intent_bucket: Optional[str] = None,
        org_context: Optional[Dict[str, Any]] = None,
        memory: Optional[Dict[str, Any]] = None,
        follow_up_note: Optional[str] = None,
    ) -> Optional[Dict[str, Any]]:
        try:
            messages = self._messages(lead_name, intent_bucket, org_context, memory, follow_up_note)
            data = self._call(messages)
            if not data:
                return None
            return self._sanitise(data)
        except Exception as e:
            logger.error(f"Pre-call brief generation failed for {lead_name!r}: {e}", exc_info=True)
            return None

    # -- LLM call (with light retry) ---------------------------------------

    def _call(self, messages: List[Dict[str, str]]) -> Dict[str, Any]:
        last = None
        for attempt in range(_MAX_RETRIES + 1):
            try:
                if self.provider == "gemini":
                    from app.utils.gemini import gemini_extract
                    return gemini_extract(messages, schema=_BRIEF_SCHEMA, tool_name="record_precall_brief",
                                          model=self.model, max_tokens=1500)
                return sarvam_extract(messages, schema=_BRIEF_SCHEMA, tool_name="record_precall_brief",
                                      model=self.model, max_tokens=1500)
            except Exception as e:
                last = e
                logger.warning(f"precall_brief attempt {attempt + 1} failed: {str(e)[:120]}")
                time.sleep(_RETRY_BASE * (attempt + 1))
        raise RuntimeError(f"precall_brief failed after retries: {last}")

    # -- prompt building ------------------------------------------------

    @staticmethod
    def _org_block(org_context: Optional[Dict[str, Any]]) -> str:
        if not org_context:
            return "\n\nORGANISATION CONTEXT: none configured — write a generic but professional brief."
        services = ", ".join(org_context.get("services") or []) or "not specified"
        usps = ", ".join(org_context.get("usps") or []) or "not specified"
        competitors = ", ".join(org_context.get("competitors") or []) or "not specified"
        pricing_min, pricing_max = org_context.get("pricing_min"), org_context.get("pricing_max")
        pricing = f"{pricing_min or '?'} - {pricing_max or '?'}" if (pricing_min or pricing_max) else "not specified"
        return (
            "\n\nORGANISATION CONTEXT:\n"
            f"Business: {org_context.get('name') or 'unknown'} ({org_context.get('industry') or 'unspecified industry'})\n"
            f"Services offered: {services}\n"
            f"Pricing range: {pricing}\n"
            f"Target audience: {org_context.get('target_audience') or 'not specified'}\n"
            f"Brand voice: {org_context.get('brand_voice') or 'not specified'}\n"
            f"Competitors: {competitors}\n"
            f"USPs: {usps}\n"
        )

    @staticmethod
    def _memory_block(memory: Optional[Dict[str, Any]]) -> str:
        if not memory or not memory.get("total_calls"):
            return "\n\nCONTACT HISTORY: none yet — this will be the first call to this contact."
        facts = memory.get("facts") or []
        facts_txt = "; ".join(f.get("text", "") for f in facts if f.get("text")) or "none recorded"
        open_obj = memory.get("open_objections") or []
        commitments = memory.get("pending_commitments") or []
        return (
            "\n\nCONTACT HISTORY (from prior calls with this same person):\n"
            f"Calls so far: {memory.get('total_calls')}\n"
            f"Running verdict: {memory.get('running_verdict') or 'unknown'}\n"
            f"Headline: {memory.get('headline') or 'none'}\n"
            f"Known facts: {facts_txt}\n"
            f"Open objections: {'; '.join(open_obj) or 'none'}\n"
            f"Pending commitments (promises made to this contact, not yet fulfilled): "
            f"{'; '.join(commitments) or 'none'}\n"
            f"Suggested strategy from memory: {memory.get('next_call_strategy') or 'none'}\n"
        )

    def _messages(
        self,
        lead_name: str,
        intent_bucket: Optional[str],
        org_context: Optional[Dict[str, Any]],
        memory: Optional[Dict[str, Any]],
        follow_up_note: Optional[str],
    ) -> List[Dict[str, str]]:
        follow_up_line = (
            f"\n\nPENDING FOLLOW-UP the telecaller scheduled with themself for this call: {follow_up_note}"
            if follow_up_note else ""
        )
        user = (
            f"Contact: {lead_name or 'this lead'}"
            + (f" (intent: {intent_bucket})" if intent_bucket else "")
            + self._org_block(org_context)
            + self._memory_block(memory)
            + follow_up_line
        )
        return [{"role": "system", "content": _BRIEF_SYS}, {"role": "user", "content": user}]

    # -- sanitisation ------------------------------------------------------

    @staticmethod
    def _sanitise(data: Dict[str, Any]) -> Dict[str, Any]:
        steps = []
        for s in (data.get("steps") or [])[:6]:
            if isinstance(s, dict) and s.get("title"):
                steps.append({"title": str(s["title"]).strip(), "subtitle": str(s.get("subtitle") or "").strip()})
        objections = []
        for o in (data.get("objections") or [])[:4]:
            if isinstance(o, dict) and o.get("question"):
                objections.append({
                    "question": str(o["question"]).strip(),
                    "response": str(o.get("response") or "").strip(),
                })
        return {
            "opening_line": str(data.get("opening_line") or "").strip(),
            "key_points": [str(p).strip() for p in (data.get("key_points") or [])[:5] if str(p).strip()],
            "steps": steps,
            "objections": objections,
            "checklist": [str(c).strip() for c in (data.get("checklist") or [])[:6] if str(c).strip()],
        }


_generator: Optional[PreCallBriefGenerator] = None


def get_generator() -> PreCallBriefGenerator:
    global _generator
    if _generator is None:
        _generator = PreCallBriefGenerator()
    return _generator


def generate_precall_brief(
    *,
    lead_name: str,
    intent_bucket: Optional[str] = None,
    org_context: Optional[Dict[str, Any]] = None,
    memory: Optional[Dict[str, Any]] = None,
    follow_up_note: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    return get_generator().generate(
        lead_name=lead_name, intent_bucket=intent_bucket, org_context=org_context,
        memory=memory, follow_up_note=follow_up_note,
    )

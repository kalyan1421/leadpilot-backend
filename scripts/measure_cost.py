"""
Offline cost measurement — counts real tokens with tiktoken (no API calls),
using actual prompts (input) and actual stored analysis/memory rows (output).
"""
import os, sys; sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import json
import tiktoken

from app.database import SessionLocal
from app.models import AudioCall, LeadAnalysis, MemoryBubble
from app.utils.lead_analyzer import LeadAnalyzer, _ANALYSIS_PROMPT
from app.utils.memory_bubble import MemoryBubbleBuilder, _MEMORY_PROMPT

enc = tiktoken.get_encoding("cl100k_base")  # close proxy for Llama tokenizer (~±15%)
def ntok(s: str) -> int:
    return len(enc.encode(s))

db = SessionLocal()

# Use the richest real call we have
cid = "call_uppala_manasa_4b7ccc1a"
call = db.query(AudioCall).filter(AudioCall.call_id == cid).first()
la_row = db.query(LeadAnalysis).filter(LeadAnalysis.call_id == cid).first()

# ---- INPUT tokens (analysis) ----
la = LeadAnalyzer()
text = la._truncate_transcript(la._to_text(call.transcript), max_turns=55)
analysis_in = ntok(_ANALYSIS_PROMPT.format(transcript=text)) + 40  # +system

# ---- OUTPUT tokens (analysis) = the actual stored JSON it produced ----
stored = {
    "sentiment_arc": la_row.sentiment_arc, "intent_tags": la_row.intent_tags,
    "entities": la_row.entities, "bant_breakdown": la_row.bant_breakdown,
    "bant_score": la_row.bant_score, "lead_verdict": la_row.lead_verdict,
    "lead_verdict_reason": la_row.lead_verdict_reason, "call_summary": la_row.call_summary,
    "key_points": la_row.key_points, "next_steps": la_row.next_steps,
    "next_action": la_row.next_action, "agent_debrief": la_row.agent_debrief,
}
analysis_out = ntok(json.dumps(stored))

# ---- Memory bubble input/output ----
mb_row = db.query(MemoryBubble).filter(MemoryBubble.contact_key == "uppala_manasa").first()
mb = MemoryBubbleBuilder()
calls_block = mb._format_calls([{
    "call_id": cid, "timestamp": "2026-06-10",
    "analysis": {"lead_verdict": la_row.lead_verdict, "bant_score": la_row.bant_score,
                 "call_summary": la_row.call_summary, "entities": la_row.entities,
                 "bant_breakdown": la_row.bant_breakdown}}])
memory_in = ntok(_MEMORY_PROMPT.format(calls_block=calls_block)) + 40
if mb_row:
    mem_stored = {"facts": mb_row.facts, "cumulative_bant": mb_row.cumulative_bant,
                  "running_verdict": mb_row.running_verdict, "sentiment_trend": mb_row.sentiment_trend,
                  "open_objections": mb_row.open_objections, "pending_commitments": mb_row.pending_commitments,
                  "next_call_strategy": mb_row.next_call_strategy, "headline": mb_row.headline}
    memory_out = ntok(json.dumps(mem_stored))
else:
    memory_out = 600
db.close()

print("=" * 56)
print(f"Measured on {cid}  ({len(call.transcript.get('turns', []))} turns)")
print("-" * 56)
print(f"{'Stage':<12}{'input':>8}{'output':>8}{'total':>8}")
print(f"{'Analysis':<12}{analysis_in:>8}{analysis_out:>8}{analysis_in+analysis_out:>8}")
print(f"{'Memory':<12}{memory_in:>8}{memory_out:>8}{memory_in+memory_out:>8}")
tin = analysis_in + memory_in
tout = analysis_out + memory_out
print(f"{'PER CALL':<12}{tin:>8}{tout:>8}{tin+tout:>8}")
print("=" * 56)

# Groq on-demand pricing for llama-3.3-70b-versatile
IN_RATE, OUT_RATE = 0.59 / 1e6, 0.79 / 1e6
cost = tin * IN_RATE + tout * OUT_RATE
USD_INR = 83.5
print(f"Groq llama-3.3-70b: ${IN_RATE*1e6:.2f}/M in  ${OUT_RATE*1e6:.2f}/M out")
print(f"COST PER CALL (analysis + memory):  ${cost:.5f}  =  Rs {cost*USD_INR:.4f}")
print(f"   1,000 calls   ${cost*1e3:>9.2f}   Rs {cost*1e3*USD_INR:>10.0f}")
print(f"  10,000 calls   ${cost*1e4:>9.2f}   Rs {cost*1e4*USD_INR:>10.0f}")
print(f" 100,000 calls   ${cost*1e5:>9.2f}   Rs {cost*1e5*USD_INR:>10.0f}")
print("-" * 56)
print(f"Free-tier ceiling: 100,000 tokens/DAY  ->  ~{100000 // (tin+tout)} calls/day max on free Groq")

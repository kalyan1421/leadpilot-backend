"""
Lead Intelligence — deterministic (no-LLM) aggregations that power:

  - The Lead Inbox cards:   lead_score (the circle), intent_bucket (the filter
                            chip: High Intent / New / Follow-up / Cold), tags (HOT…)
  - The Score tab:          telecaller rolling score + trend arrow (^5, ^2)
  - The inbox header:       "Avg Score 86/100"

These are pure functions over already-computed lead_analysis rows, so they are
instant and free — no model call. They are the glue between per-call AI output
and the list/aggregate views the app shows.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional


# ----------------------------------------------------------------------------
# Lead-level (one contact, possibly many calls)
# ----------------------------------------------------------------------------

def lead_score_from_calls(call_analyses: List[Dict[str, Any]]) -> int:
    """Average BANT across a contact's calls -> the inbox score circle (0-100)."""
    scores = [a.get("bant_score") for a in call_analyses if isinstance(a.get("bant_score"), (int, float))]
    if not scores:
        return 0
    return round(sum(scores) / len(scores))


def _has_buy_signal(call_analyses: List[Dict[str, Any]]) -> bool:
    for a in call_analyses:
        for t in (a.get("intent_tags") or []):
            if t.get("intent") in ("buy_signal", "close"):
                return True
    return False


def _has_pending_followup(call_analyses: List[Dict[str, Any]]) -> bool:
    for a in call_analyses:
        if a.get("next_steps"):
            return True
        na = a.get("next_action") or {}
        if na.get("recommended_action") in ("call_back", "schedule_visit", "send_brochure"):
            return True
    return False


def intent_bucket(
    call_analyses: List[Dict[str, Any]],
    *,
    lead_status: Optional[str] = None,
) -> str:
    """
    Classify a lead into one inbox filter bucket: high_intent | new | follow_up | cold.

    Priority order matters — a lead is shown under the most actionable bucket.
    `lead_status` (from the backend Lead row) wins for the 'new' case when known.
    """
    # Backend Lead.status wins for explicit lifecycle states when provided.
    if (lead_status or "").lower() == "new":
        return "new"

    if not call_analyses:
        return "new"  # ingested but never called

    score = lead_score_from_calls(call_analyses)
    verdict = (call_analyses[-1].get("lead_verdict") or "").lower()

    # Strong positive signal wins first.
    if verdict == "hot" or score >= 70:
        return "high_intent"
    # Junk/cold or very low score wins next — a stray buy_signal tag on a garbage
    # transcript should NOT promote a dead lead to high intent.
    if verdict in ("cold", "junk") or score < 25:
        return "cold"
    # Mid-range: genuine buy signal -> high intent, else follow-up.
    if _has_buy_signal(call_analyses):
        return "high_intent"
    return "follow_up"


def lead_tags(call_analyses: List[Dict[str, Any]], *, source: Optional[str] = None) -> List[str]:
    """Build the colored chips shown on the lead card / detail header."""
    tags: List[str] = []
    if not call_analyses:
        if source:
            tags.append(source.upper())
        tags.append("NEW")
        return tags

    score = lead_score_from_calls(call_analyses)
    verdict = (call_analyses[-1].get("lead_verdict") or "").capitalize()

    if verdict == "Hot":
        tags.append("HOT")
    elif verdict == "Warm":
        tags.append("WARM")

    if _has_buy_signal(call_analyses) and verdict != "Hot":
        tags.append("HIGH INTENT")
    if source:
        tags.append(source.upper())
    if score >= 90:
        tags.append("PRIORITY")
    return tags


def lead_card(
    contact_key: str,
    call_analyses: List[Dict[str, Any]],
    *,
    name: Optional[str] = None,
    source: Optional[str] = None,
    lead_status: Optional[str] = None,
    created_at: Optional[datetime] = None,
) -> Dict[str, Any]:
    """Everything the inbox card needs for one lead, in one object."""
    # Most-recent call timestamp across this contact's analyses, so the mobile
    # inbox can show a real "last contacted N days ago" that actually ages.
    call_times = [t for t in (_parse_ts(c.get("timestamp")) for c in call_analyses) if t]
    last_call_at = max(call_times) if call_times else None
    return {
        "contact_key": contact_key,
        "name": name,
        "lead_score": lead_score_from_calls(call_analyses),
        "intent_bucket": intent_bucket(call_analyses, lead_status=lead_status),
        "verdict": call_analyses[-1].get("lead_verdict") if call_analyses else None,
        "tags": lead_tags(call_analyses, source=source),
        "total_calls": len(call_analyses),
        # Timestamps the inbox tile falls back through: last call → lead
        # creation. Without these every never-called lead defaulted to "now"
        # on the client, so all cards read the same stale relative time.
        "last_call_at": last_call_at.isoformat() if last_call_at else None,
        "created_at": created_at.isoformat() if created_at else None,
    }


# ----------------------------------------------------------------------------
# Telecaller-level (one agent, many calls over time)
# ----------------------------------------------------------------------------

def _parse_ts(ts: Any) -> Optional[datetime]:
    if isinstance(ts, datetime):
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)
    if isinstance(ts, str):
        try:
            d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
        except ValueError:
            return None
    return None


def _avg(nums: List[float]) -> Optional[float]:
    nums = [n for n in nums if isinstance(n, (int, float))]
    return round(sum(nums) / len(nums), 1) if nums else None


# The six scored dimensions of an agent_debrief. Summing their averaged values
# gives the /110 composite "quality" score used across the founder portal
# (5 skill dims * 20pts + punctuality * 10pts). Kept here — the single source of
# truth — so the team, performance, and comparison surfaces can't drift apart
# (which is exactly what happened when the Manage Team page averaged the raw
# 0-100 agent_debrief.total_score instead).
DEBRIEF_DIMENSIONS = ["opening", "discovery", "pitch", "objection_handling", "closing", "punctuality"]


def averaged_debrief_dimensions(debriefs: List[Dict[str, Any]]) -> Dict[str, int]:
    """Mean score per dimension across the given agent_debrief dicts. Missing or
    non-numeric dimension scores contribute 0. `sum(result.values())` is the
    /110 composite quality; callers decide how to treat an empty debrief list
    (0 for aggregate math, None for a "no calls yet" display)."""
    dims: Dict[str, int] = {}
    valid = [d for d in debriefs if isinstance(d, dict)]
    for dim in DEBRIEF_DIMENSIONS:
        vals = [d[f"{dim}_score"] for d in valid if isinstance(d.get(f"{dim}_score"), (int, float))]
        dims[dim] = round(sum(vals) / len(vals)) if vals else 0
    return dims


def telecaller_score(
    calls: List[Dict[str, Any]],
    *,
    window_days: int = 7,
    now: Optional[datetime] = None,
) -> Dict[str, Any]:
    """
    Rolling telecaller performance + trend.

    `calls`: [{"timestamp", "agent_total_score", "bant_score", "lead_verdict"}]
      agent_total_score = agent_debrief.total_score for that call (0-100).

    Returns the numbers behind the Score tab:
      - telecaller_score: rolling avg agent score over the recent window
      - trend: delta vs the previous window  (drives the ^5 / ^2 arrow)
      - calls, avg_lead_score, hot_leads
    """
    now = now or datetime.now(timezone.utc)
    cur_start = now - timedelta(days=window_days)
    prev_start = now - timedelta(days=2 * window_days)

    cur, prev, all_agent = [], [], []
    hot, lead_scores = 0, []

    for c in calls:
        ts = _parse_ts(c.get("timestamp"))
        a_score = c.get("agent_total_score")
        # Exclude no-data calls (empty/failed analysis → total_score 0) from the
        # PERFORMANCE average. A real conversation always scores > 0 on some
        # dimension; counting empty transcripts as 0 unfairly tanks the agent.
        if isinstance(a_score, (int, float)) and a_score > 0:
            all_agent.append(a_score)
            if ts and ts >= cur_start:
                cur.append(a_score)
            elif ts and prev_start <= ts < cur_start:
                prev.append(a_score)
        if isinstance(c.get("bant_score"), (int, float)):
            lead_scores.append(c["bant_score"])
        if (c.get("lead_verdict") or "").lower() == "hot":
            hot += 1

    # Display: current-window avg if we have current calls, else fall back to all-time so the
    # ring isn't blank. But a TREND is only meaningful with current-window data — never derive
    # a delta from the fallback (that produced a misleading "flat 0" for a silent week).
    cur_avg = _avg(cur) if cur else _avg(all_agent)
    prev_avg = _avg(prev)
    trend = round(_avg(cur) - prev_avg, 1) if (cur and prev_avg is not None) else None

    return {
        "telecaller_score": cur_avg if cur_avg is not None else 0,
        "trend": trend,                       # +5 -> green ^5 ; None -> no arrow (no current basis)
        "trend_direction": (None if trend is None else ("up" if trend > 0 else "down" if trend < 0 else "flat")),
        "calls": len(calls),
        "avg_lead_score": _avg(lead_scores) or 0,
        "hot_leads": hot,
        "window_days": window_days,
    }


# ----------------------------------------------------------------------------
# Per-call Score tab components (deterministic, no LLM)
#   Powers the four rings (Overall / Telecaller / Lead Quality / Sentiment),
#   the composite hero "Call Score", and the sentiment timeline bar + caption.
# ----------------------------------------------------------------------------

# Composite hero "Call Score" weights. One per-call number answering
# "how did this call go?" — tunable in this one place.
#   overall      = agent_debrief.total_score (telecaller execution this call)
#   lead_quality = bant_score                (how willing the lead is)
#   sentiment    = derived sentiment ring    (how the prospect felt)
CALL_SCORE_WEIGHTS = {"overall": 0.45, "lead_quality": 0.30, "sentiment": 0.25}

# The four emotional bands shown in the Figma sentiment-timeline legend,
# mapped from the raw -1..1 per-turn sentiment score. Ordered by valence:
#   frustrated < cautious < neutral < interested
def sentiment_label(score: float) -> str:
    if score <= -0.35:
        return "frustrated"
    if score <= -0.05:
        return "cautious"
    if score < 0.35:
        return "neutral"
    return "interested"


def _to_0_100(score: float) -> int:
    """Map a -1..1 sentiment score to a 0..100 ring value."""
    return round((max(-1.0, min(1.0, score)) + 1.0) / 2.0 * 100)


def mmss_to_seconds(ts: Any) -> Optional[int]:
    """Parse a transcript turn timestamp like '03:54' -> 234. None if unparseable."""
    if isinstance(ts, (int, float)):
        return int(ts)
    if not isinstance(ts, str) or ":" not in ts:
        return None
    try:
        m, s = ts.split(":")[-2:]
        return int(m) * 60 + int(s)
    except (ValueError, TypeError):
        return None


def _seconds_to_mmss(sec: float) -> str:
    sec = max(0, int(round(sec)))
    return f"{sec // 60}:{sec % 60:02d}"


def sentiment_score(sentiment_arc: Optional[List[Dict[str, Any]]], *, prospect_role: str = "USER") -> int:
    """
    Single 0-100 sentiment ring for one call (the 'Sentiment 63' card).

    Prefers the PROSPECT's turns (how the lead felt). Falls back to all turns
    when roles aren't present. Returns 0 when there is no sentiment signal.
    """
    arc = sentiment_arc or []
    prospect = [t.get("score") for t in arc
                if (t.get("role") or "").upper() == prospect_role and isinstance(t.get("score"), (int, float))]
    pool = prospect or [t.get("score") for t in arc if isinstance(t.get("score"), (int, float))]
    if not pool:
        return 0
    return _to_0_100(sum(pool) / len(pool))


def sentiment_timeline(
    sentiment_arc: Optional[List[Dict[str, Any]]],
    *,
    turn_seconds: Optional[Dict[int, int]] = None,
    segments: int = 5,
) -> Dict[str, Any]:
    """
    Bucket the per-turn sentiment arc into `segments` equal time slices for the
    Figma 'Sentiment Timeline' bar. Each slice gets a label (frustrated/cautious/
    neutral/interested) + average score, plus a one-line caption.

    `turn_seconds`: optional {turn_index -> seconds} built from the transcript
    timestamps. When absent, arc points are spaced evenly (no real clock).
    """
    arc = [t for t in (sentiment_arc or []) if isinstance(t.get("score"), (int, float))]
    if not arc:
        return {"segments": [], "caption": "No sentiment signal in this call."}

    # (seconds, score) for every arc point; fill unknown times by even spacing.
    pts: List[List[float]] = []
    for t in arc:
        sec = turn_seconds.get(t.get("turn")) if turn_seconds else None
        pts.append([sec, float(t.get("score"))])

    known = [p[0] for p in pts if p[0] is not None]
    # Need >=2 DISTINCT real timestamps for a real clock; otherwise (all-equal/degenerate)
    # fall back to even spacing so points spread across segments instead of collapsing into one.
    use_clock = len(set(known)) >= 2
    total = float(max(known)) if (use_clock and known) else float(max(1, len(arc) - 1))
    if total <= 0:
        total = float(max(1, len(arc) - 1))
    for i, p in enumerate(pts):
        if (not use_clock) or p[0] is None:
            p[0] = (i / max(1, len(pts) - 1)) * total

    seg_len = total / segments
    out: List[Dict[str, Any]] = []
    for s in range(segments):
        lo = s * seg_len
        hi = (s + 1) * seg_len if s < segments - 1 else total + 1e-6
        scores = [sc for (sec, sc) in pts if lo <= sec < hi]
        if not scores:  # empty slice -> carry the nearest point so the bar has no gap
            mid = (lo + hi) / 2
            scores = [min(pts, key=lambda p: abs(p[0] - mid))[1]]
        avg = sum(scores) / len(scores)
        out.append({
            "index": s,
            "t0_sec": int(round(lo)),
            "t1_sec": int(round(min(hi, total))),
            "t0": _seconds_to_mmss(lo),
            "label": sentiment_label(avg),
            "avg_score": round(avg, 2),
        })
    return {"segments": out, "caption": _timeline_caption(out)}


def _timeline_caption(segs: List[Dict[str, Any]]) -> str:
    """A short, deterministic sentence describing the sentiment journey."""
    if not segs:
        return "No sentiment signal in this call."
    first, last = segs[0], segs[-1]
    delta = last["avg_score"] - first["avg_score"]
    spike = next((s for s in segs if s["label"] == "frustrated"), None)

    if delta >= 0.25:
        # first slice that reached roughly the final (higher) level
        turn = next((s for s in segs if s["avg_score"] >= last["avg_score"] - 0.1), last)
        lead = f"Prospect warmed up around {turn['t0']}."
    elif delta <= -0.25:
        turn = next((s for s in segs if s["avg_score"] <= last["avg_score"] + 0.1), last)
        lead = f"Prospect cooled off around {turn['t0']}."
    else:
        lead = f"Sentiment stayed {last['label']} through the call."

    tail = f"Frustration spike near {spike['t0']}." if spike else "No negative spike detected."
    return f"{lead} {tail}"


def call_score(overall: Optional[float], lead_quality: Optional[float], sentiment: Optional[float]) -> int:
    """
    Composite hero 'Call Score' (0-100) = weighted blend of the three per-call
    rings. Missing components are dropped and the remaining weights renormalise,
    so the number is still meaningful before analysis fully populates.
    """
    parts = [
        (overall, CALL_SCORE_WEIGHTS["overall"]),
        (lead_quality, CALL_SCORE_WEIGHTS["lead_quality"]),
        (sentiment, CALL_SCORE_WEIGHTS["sentiment"]),
    ]
    num = sum((v or 0) * w for v, w in parts if isinstance(v, (int, float)))
    den = sum(w for v, w in parts if isinstance(v, (int, float)))
    return round(num / den) if den else 0


def score_trend(current: Optional[float], previous: Optional[float]) -> Optional[int]:
    """
    Delta vs this contact's previous call -> the ^N / vN arrow. None when there
    is no prior call yet (the UI shows '—', no arrow).
    """
    if not isinstance(current, (int, float)) or not isinstance(previous, (int, float)):
        return None
    return round(current - previous)


def inbox_header(lead_cards: List[Dict[str, Any]]) -> Dict[str, Any]:
    """The numbers at the top of the inbox: 'Calls Today', 'Avg Score'."""
    scores = [c["lead_score"] for c in lead_cards if c.get("lead_score")]
    buckets: Dict[str, int] = {}
    for c in lead_cards:
        b = c.get("intent_bucket", "new")
        buckets[b] = buckets.get(b, 0) + 1
    return {
        "total_leads": len(lead_cards),
        "avg_score": round(sum(scores) / len(scores)) if scores else 0,
        "buckets": buckets,
    }

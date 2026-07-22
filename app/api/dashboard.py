"""Org-scoped dashboard aggregates for the founder web portal — leads kanban
board and telecaller performance. Every query here filters to the caller's
org_id; nothing is cross-tenant.
"""

import calendar
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user, require_role
from app.database import get_db
from app.models import Attendance, AudioCall, Lead, LeadAnalysis, Organization, User
from app.utils.lead_intelligence import DEBRIEF_DIMENSIONS, averaged_debrief_dimensions, mmss_to_seconds
from app.utils.memory_bubble import contact_key_from_call_id

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["dashboard"])

KANBAN_STAGES = [
    "New", "Assigned", "Contacted", "Interested", "Proposal Sent",
    "Negotiation", "Closed Won", "Closed Lost", "Junk",
]

_DIMENSIONS = DEBRIEF_DIMENSIONS


# ---------------------------------------------------------------------------
# Leads board (kanban)
# ---------------------------------------------------------------------------

def _latest_scores_by_contact(db: Session, org_id: str) -> Dict[str, float]:
    """{contact_key: latest bant_score}, newest call per contact wins.

    AudioCall has no contact_key column — it's derived from call_id (see
    contact_key_from_call_id) — so this groups in Python from a single org-
    scoped query rather than joining on a column that doesn't exist.
    """
    rows = (
        db.query(AudioCall.call_id, AudioCall.timestamp, LeadAnalysis.bant_score)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(AudioCall.org_id == org_id, LeadAnalysis.status == "completed")
        .order_by(AudioCall.timestamp.asc())
        .all()
    )
    latest: Dict[str, float] = {}
    for call_id, _ts, bant_score in rows:
        if bant_score is not None:
            latest[contact_key_from_call_id(call_id)] = bant_score  # later rows overwrite → newest wins
    return latest


@router.get("/leads/board", status_code=status.HTTP_200_OK)
async def get_leads_board(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    leads = db.query(Lead).filter(Lead.org_id == current_user.org_id).all()
    now = datetime.now(timezone.utc)

    scores_by_contact = _latest_scores_by_contact(db, current_user.org_id)
    telecallers = {
        u.id: u.name
        for u in db.query(User).filter(User.org_id == current_user.org_id).all()
    }

    out = []
    for lead in leads:
        # A lead with no scored calls yet simply has no score (not zero, which
        # would misleadingly rank it as a confirmed-bad lead).
        score = scores_by_contact.get(lead.contact_key)
        score = round(score) if score is not None else None

        updated = lead.updated_at or lead.created_at
        days_stuck = (now - updated).days if updated else 0

        out.append({
            "id": lead.id,
            "name": lead.name or lead.contact_key,
            "source": lead.source,
            "score": score,
            "pipeline_stage": lead.pipeline_stage,
            "telecaller_name": telecallers.get(lead.assigned_to),
            "days_stuck": max(0, days_stuck),
        })

    return {"stages": KANBAN_STAGES, "leads": out}


def _apply_stage_update(lead: Lead, body: Dict[str, Any]) -> Lead:
    """Shared by both the web Kanban (looked up by Lead.id) and the mobile
    pipeline strip (looked up by contact_key, see update_lead_stage_by_contact)
    so Closed Won/discount handling can't drift between the two callers."""
    stage = body.get("stage")
    if stage not in KANBAN_STAGES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"stage must be one of {KANBAN_STAGES}")

    # Pipeline is forward-only: a lead may advance (or drop into the terminal
    # Closed Lost / Junk stages, which sit last), but never regress to an
    # earlier stage. Without this guard a lead moved to "Assigned" could be
    # clicked straight back to "New", silently undoing real progress. A no-op
    # (same stage) is always allowed. Unknown current stages (legacy rows)
    # are treated as "before everything" so they can still be classified.
    current = lead.pipeline_stage
    current_idx = KANBAN_STAGES.index(current) if current in KANBAN_STAGES else -1
    new_idx = KANBAN_STAGES.index(stage)
    if new_idx < current_idx:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            detail=f"Cannot move a lead backward from '{current}' to '{stage}' — pipeline stages only advance.",
        )

    lead.pipeline_stage = stage

    # Revenue tracking: a lead only counts toward the dashboard's revenue chart
    # while it's actually sitting in Closed Won with a closed_at timestamp — if
    # it's moved back out (reopened), clear closed_at so it stops counting
    # until it's closed again.
    if stage == "Closed Won":
        lead.closed_at = datetime.now(timezone.utc)
        deal_value = body.get("deal_value")
        if deal_value is not None:
            lead.deal_value = int(deal_value)
        # Discount/margin tracking (PRD Layer 4-C) — only meaningful alongside
        # a Closed Won deal_value, so both are only ever set on this branch.
        list_price = body.get("list_price")
        if list_price is not None:
            lead.list_price = int(list_price)
        discount_pct = body.get("discount_pct")
        if discount_pct is not None:
            lead.discount_pct = float(discount_pct)
    else:
        lead.closed_at = None

    return lead


@router.patch("/leads/{lead_id}/stage", status_code=status.HTTP_200_OK)
async def update_lead_stage(
    lead_id: str,
    body: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    lead = db.query(Lead).filter(Lead.id == lead_id, Lead.org_id == current_user.org_id).first()
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead = _apply_stage_update(lead, body)
    db.commit()
    return {
        "id": lead.id,
        "pipeline_stage": lead.pipeline_stage,
        "deal_value": lead.deal_value,
        "list_price": lead.list_price,
        "discount_pct": lead.discount_pct,
    }


@router.patch("/leads/by-contact/{contact_key}/stage", status_code=status.HTTP_200_OK)
async def update_lead_stage_by_contact(
    contact_key: str,
    body: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Mobile-app variant of update_lead_stage: the Flutter app only ever knows
    a lead by contact_key (see AI_HANDOVER's contact_key convention), never the
    Lead.id UUID the web Kanban uses — so it needs its own lookup rather than
    forcing the client to learn a new identifier."""
    lead = (
        db.query(Lead)
        .filter(Lead.contact_key == contact_key, Lead.org_id == current_user.org_id)
        .first()
    )
    if lead is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Lead not found")

    lead = _apply_stage_update(lead, body)
    db.commit()
    return {
        "contact_key": lead.contact_key,
        "pipeline_stage": lead.pipeline_stage,
        "deal_value": lead.deal_value,
        "list_price": lead.list_price,
        "discount_pct": lead.discount_pct,
    }


# ---------------------------------------------------------------------------
# Telecaller performance
# ---------------------------------------------------------------------------

def _compute_telecaller_metrics(
    calls: List[AudioCall],
    analyses: Dict[str, LeadAnalysis],
    assigned_leads: List[Lead],
) -> Dict[str, Any]:
    """Pure aggregation over already-fetched rows for one telecaller — split out
    from `_telecaller_metrics` so `_telecaller_metrics_batch` can pre-fetch all
    telecallers' rows in a handful of queries and reuse this instead of re-querying
    per telecaller."""
    total_calls = len(calls)
    connected = [c for c in calls if isinstance(c.transcript, dict) and c.transcript.get("turns")]
    connect_pct = round(100 * len(connected) / total_calls, 1) if total_calls else 0.0

    verdicts = [analyses[c.call_id].lead_verdict for c in calls if c.call_id in analyses]
    positive = [v for v in verdicts if v in ("Hot", "Warm")]
    positive_pct = round(100 * len(positive) / len(verdicts), 1) if verdicts else 0.0

    closed_won = [leadrow for leadrow in assigned_leads if leadrow.pipeline_stage == "Closed Won"]
    close_pct = round(100 * len(closed_won) / len(assigned_leads), 1) if assigned_leads else 0.0
    revenue = sum(leadrow.deal_value or 0 for leadrow in closed_won)

    debriefs = [analyses[c.call_id].agent_debrief for c in calls
                if c.call_id in analyses and isinstance(analyses[c.call_id].agent_debrief, dict)]
    # 5 dims * 20pts + punctuality * 10pts = /110 (punctuality is additive, see
    # lead_analyzer.py — doesn't change what the original 5 dims/scale mean).
    # Shared with the Manage Team page via averaged_debrief_dimensions so the
    # same telecaller can't show two different quality numbers.
    dims = averaged_debrief_dimensions(debriefs)
    quality = round(sum(dims.values())) if debriefs else 0

    talk_times = []
    for c in connected:
        turns = c.transcript.get("turns") or []
        if turns:
            secs = mmss_to_seconds(turns[-1].get("timestamp"))
            if secs is not None:
                talk_times.append(secs)
    talk_time_seconds = round(sum(talk_times) / len(talk_times)) if talk_times else 0

    now = datetime.now(timezone.utc)
    cur_start, prev_start = now - timedelta(days=14), now - timedelta(days=28)
    cur_scores, prev_scores = [], []
    for c in calls:
        a = analyses.get(c.call_id)
        if not a or not isinstance(a.agent_debrief, dict):
            continue
        ts = c.timestamp
        if ts is None:
            continue
        total = a.agent_debrief.get("total_score")
        if not isinstance(total, (int, float)):
            continue
        if ts >= cur_start:
            cur_scores.append(total)
        elif prev_start <= ts < cur_start:
            prev_scores.append(total)
    trend = None
    if cur_scores and prev_scores:
        trend = "up" if (sum(cur_scores) / len(cur_scores)) >= (sum(prev_scores) / len(prev_scores)) else "down"

    return {
        "calls": total_calls,
        "connected": len(connected),
        "connect_pct": connect_pct,
        "positive_pct": positive_pct,
        "closed_won": len(closed_won),
        "close_pct": close_pct,
        "revenue": revenue,
        "talk_time_seconds": talk_time_seconds,
        "quality": quality,
        "trend": trend,
        "dimensions": dims,
    }


def _telecaller_metrics(db: Session, telecaller_id: str, org_id: str) -> Dict[str, Any]:
    """Single-telecaller path — used by the detail endpoint, where one telecaller's
    worth of queries is the right cost. Bulk callers should use the _batch variant
    below instead of calling this in a loop."""
    calls = (
        db.query(AudioCall)
        .filter(AudioCall.telecaller_id == telecaller_id, AudioCall.org_id == org_id)
        .all()
    )
    analyses = {
        a.call_id: a
        for a in db.query(LeadAnalysis)
        .join(AudioCall, AudioCall.call_id == LeadAnalysis.call_id)
        .filter(AudioCall.telecaller_id == telecaller_id, AudioCall.org_id == org_id,
                LeadAnalysis.status == "completed")
        .all()
    }
    assigned_leads = db.query(Lead).filter(Lead.assigned_to == telecaller_id, Lead.org_id == org_id).all()
    return _compute_telecaller_metrics(calls, analyses, assigned_leads)


def _telecaller_metrics_batch(db: Session, telecaller_ids: List[str], org_id: str) -> Dict[str, Dict[str, Any]]:
    """Same aggregation as `_telecaller_metrics`, but for many telecallers in 3
    queries total instead of 3 queries per telecaller — `get_telecaller_performance`,
    `_insights`, and the report-preview endpoint were all doing that N+1 in a loop."""
    if not telecaller_ids:
        return {}

    calls_by_tc: Dict[str, List[AudioCall]] = {tid: [] for tid in telecaller_ids}
    for call in (
        db.query(AudioCall)
        .filter(AudioCall.telecaller_id.in_(telecaller_ids), AudioCall.org_id == org_id)
        .all()
    ):
        calls_by_tc[call.telecaller_id].append(call)

    analyses_by_call_id = {
        a.call_id: a
        for a in db.query(LeadAnalysis)
        .join(AudioCall, AudioCall.call_id == LeadAnalysis.call_id)
        .filter(AudioCall.telecaller_id.in_(telecaller_ids), AudioCall.org_id == org_id,
                LeadAnalysis.status == "completed")
        .all()
    }

    leads_by_tc: Dict[str, List[Lead]] = {tid: [] for tid in telecaller_ids}
    for lead in (
        db.query(Lead)
        .filter(Lead.assigned_to.in_(telecaller_ids), Lead.org_id == org_id)
        .all()
    ):
        leads_by_tc[lead.assigned_to].append(lead)

    return {
        tid: _compute_telecaller_metrics(calls_by_tc[tid], analyses_by_call_id, leads_by_tc[tid])
        for tid in telecaller_ids
    }


@router.get("/telecallers/performance", status_code=status.HTTP_200_OK)
async def get_telecaller_performance(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    telecallers = (
        db.query(User)
        .filter(User.org_id == current_user.org_id, User.role == "telecaller")
        .all()
    )

    metrics_by_tc = _telecaller_metrics_batch(db, [tc.id for tc in telecallers], current_user.org_id)
    results: List[Dict[str, Any]] = [
        {"id": tc.id, "name": tc.name, **metrics_by_tc[tc.id]} for tc in telecallers
    ]

    def _avg(key: str) -> float:
        vals = [r[key] for r in results]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    team_average = {
        "calls": round(_avg("calls")),
        "connect_pct": _avg("connect_pct"),
        "positive_pct": _avg("positive_pct"),
        "close_pct": _avg("close_pct"),
        "talk_time_seconds": round(_avg("talk_time_seconds")),
        "quality": round(_avg("quality")),
        "dimensions": {
            dim: round(sum(r["dimensions"][dim] for r in results) / len(results)) if results else 0
            for dim in _DIMENSIONS
        },
    }

    return {"telecallers": results, "team_average": team_average}


# ---------------------------------------------------------------------------
# Team health board (Active/Break/Inactive/Absent) — PRD Layer 1-B
# ---------------------------------------------------------------------------

# PRD's own status thresholds (Section 1-B "Status Definitions"):
# Active = on a call or logged in and making calls
# Break = logged in, no call in the last 15 min
# Inactive = no call in the last 45 min, or logged out
# Absent = not logged in during working hours
_BREAK_THRESHOLD_MIN = 15
_INACTIVE_THRESHOLD_MIN = 45


def _telecaller_status(
    now: datetime, attendance: Optional[Attendance], last_call_at: Optional[datetime]
) -> str:
    """Heuristic status derived from existing Attendance + AudioCall timestamps.
    There's no live presence/break-toggle system in this codebase, so this
    approximates the PRD's board rather than building real-time presence
    tracking — a telecaller who's actually on a break but has no stale-call
    signal yet (just checked in, hasn't called) would still read as Active."""
    if attendance is None or attendance.check_in_at is None:
        return "Absent"
    if attendance.check_out_at is not None:
        return "Inactive"

    reference = last_call_at or attendance.check_in_at
    minutes_since = (now - reference).total_seconds() / 60
    if minutes_since <= _BREAK_THRESHOLD_MIN:
        return "Active"
    if minutes_since <= _INACTIVE_THRESHOLD_MIN:
        return "Break"
    return "Inactive"


@router.get("/telecallers/status", status_code=status.HTTP_200_OK)
async def get_team_status(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    """Team Health board — one row per telecaller with a derived
    Active/Break/Inactive/Absent status plus today's calls/connected/
    closed/quality/revenue, reusing the same batch metrics as
    /telecallers/performance instead of re-querying."""
    telecallers = (
        db.query(User)
        .filter(User.org_id == current_user.org_id, User.role == "telecaller")
        .all()
    )
    telecaller_ids = [tc.id for tc in telecallers]

    now = datetime.now(timezone.utc)
    today = now.date()
    start_of_today = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)

    metrics_by_tc = _telecaller_metrics_batch(db, telecaller_ids, current_user.org_id)

    attendance_by_tc: Dict[str, Attendance] = {
        a.user_id: a
        for a in db.query(Attendance)
        .filter(
            Attendance.org_id == current_user.org_id,
            Attendance.user_id.in_(telecaller_ids),
            Attendance.date == today,
        )
        .all()
    }

    last_call_by_tc: Dict[str, datetime] = {}
    for call in (
        db.query(AudioCall)
        .filter(
            AudioCall.org_id == current_user.org_id,
            AudioCall.telecaller_id.in_(telecaller_ids),
            AudioCall.timestamp >= start_of_today,
        )
        .order_by(AudioCall.timestamp.asc())
        .all()
    ):
        last_call_by_tc[call.telecaller_id] = call.timestamp  # later rows overwrite -> latest wins

    revenue_today_by_tc: Dict[str, int] = {tid: 0 for tid in telecaller_ids}
    for lead in (
        db.query(Lead)
        .filter(
            Lead.org_id == current_user.org_id,
            Lead.assigned_to.in_(telecaller_ids),
            Lead.pipeline_stage == "Closed Won",
            Lead.closed_at >= start_of_today,
        )
        .all()
    ):
        revenue_today_by_tc[lead.assigned_to] = (
            revenue_today_by_tc.get(lead.assigned_to, 0) + (lead.deal_value or 0)
        )

    results = [
        {
            "id": tc.id,
            "name": tc.name,
            "status": _telecaller_status(now, attendance_by_tc.get(tc.id), last_call_by_tc.get(tc.id)),
            "calls": metrics_by_tc.get(tc.id, {}).get("calls", 0),
            "connected": metrics_by_tc.get(tc.id, {}).get("connected", 0),
            "closed_won": metrics_by_tc.get(tc.id, {}).get("closed_won", 0),
            "quality": metrics_by_tc.get(tc.id, {}).get("quality", 0),
            "trend": metrics_by_tc.get(tc.id, {}).get("trend"),
            "revenue_today": revenue_today_by_tc.get(tc.id, 0),
        }
        for tc in telecallers
    ]

    return {"telecallers": results}


# ---------------------------------------------------------------------------
# Coaching & Development recommendation queue (read-only) — PRD Layer 2-D
# ---------------------------------------------------------------------------

# /20 for the 5 original dimensions, /10 for punctuality (see lead_analyzer.py).
_COACHING_THRESHOLDS = {
    "opening": 12, "discovery": 12, "pitch": 12,
    "objection_handling": 12, "closing": 12, "punctuality": 5,
}
_COACHING_LABELS = {
    "opening": "Opening", "discovery": "Discovery", "pitch": "Pitch",
    "objection_handling": "Objection Handling", "closing": "Closing", "punctuality": "Punctuality",
}
_COACHING_ACTIONS = {
    "opening": "Review opening scripts and greeting technique",
    "discovery": "Coach on asking better discovery/qualifying questions",
    "pitch": "Practice pitch delivery and value-proposition framing",
    "objection_handling": "Role-play the team's most common objections",
    "closing": "Review closing techniques and call-to-action phrasing",
    "punctuality": "Work on call pacing — avoid dead air and rambling",
}
_COACHING_WINDOW_DAYS = 14
_COACHING_MIN_CALLS = 3  # below this, a low average isn't a confident signal


@router.get("/coaching/queue", status_code=status.HTTP_200_OK)
async def get_coaching_queue(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    """
    Read-only recommendation queue: flags telecallers whose trailing-14-day
    average on any scoring dimension falls below a threshold. Derived entirely
    from existing call/analysis data — no new table, no session-logging or
    before/after tracking (explicitly out of scope for this pass, see plan).
    """
    telecallers = (
        db.query(User)
        .filter(User.org_id == current_user.org_id, User.role == "telecaller")
        .all()
    )
    telecaller_ids = [tc.id for tc in telecallers]
    window_start = datetime.now(timezone.utc) - timedelta(days=_COACHING_WINDOW_DAYS)

    calls_by_tc: Dict[str, List[AudioCall]] = {tid: [] for tid in telecaller_ids}
    for call in (
        db.query(AudioCall)
        .filter(
            AudioCall.telecaller_id.in_(telecaller_ids),
            AudioCall.org_id == current_user.org_id,
            AudioCall.timestamp >= window_start,
        )
        .all()
    ):
        calls_by_tc[call.telecaller_id].append(call)

    analyses_by_call_id = {
        a.call_id: a
        for a in db.query(LeadAnalysis)
        .join(AudioCall, AudioCall.call_id == LeadAnalysis.call_id)
        .filter(
            AudioCall.telecaller_id.in_(telecaller_ids),
            AudioCall.org_id == current_user.org_id,
            AudioCall.timestamp >= window_start,
            LeadAnalysis.status == "completed",
        )
        .all()
    }

    queue: List[Dict[str, Any]] = []
    for tc in telecallers:
        debriefs = [
            analyses_by_call_id[c.call_id].agent_debrief
            for c in calls_by_tc.get(tc.id, [])
            if c.call_id in analyses_by_call_id and isinstance(analyses_by_call_id[c.call_id].agent_debrief, dict)
        ]
        # Telecallers with no scored calls used to be dropped here, which made an
        # otherwise-empty queue read as "the whole team is above threshold" even
        # when someone had a 0/110 (i.e. no reviewed calls at all). Surface these
        # as their own queue items instead of hiding them.
        if len(debriefs) == 0:
            queue.append({
                "telecaller_id": tc.id,
                "telecaller_name": tc.name,
                "issue": f"No scored calls in the last {_COACHING_WINDOW_DAYS} days — nothing to coach on yet",
                "recommended_action": "Check that this telecaller is active and their calls are being recorded and analysed.",
                "priority": "High",
            })
            continue
        if len(debriefs) < _COACHING_MIN_CALLS:
            queue.append({
                "telecaller_id": tc.id,
                "telecaller_name": tc.name,
                "issue": (
                    f"Only {len(debriefs)} scored call(s) in the last {_COACHING_WINDOW_DAYS} days — "
                    "too few to assess dimensions reliably"
                ),
                "recommended_action": "Revisit once there are a few more scored calls this period.",
                "priority": "Low",
            })
            continue

        for dim, threshold in _COACHING_THRESHOLDS.items():
            vals = [d.get(f"{dim}_score") for d in debriefs if isinstance(d.get(f"{dim}_score"), (int, float))]
            if not vals:
                continue
            avg = sum(vals) / len(vals)
            if avg >= threshold:
                continue
            gap_ratio = (threshold - avg) / threshold
            priority = "High" if gap_ratio >= 0.5 else "Medium" if gap_ratio >= 0.25 else "Low"
            max_score = 10 if dim == "punctuality" else 20
            queue.append({
                "telecaller_id": tc.id,
                "telecaller_name": tc.name,
                "issue": (
                    f"{_COACHING_LABELS[dim]} averaging {round(avg, 1)}/{max_score} "
                    f"over the last {_COACHING_WINDOW_DAYS} days ({len(debriefs)} calls)"
                ),
                "recommended_action": _COACHING_ACTIONS[dim],
                "priority": priority,
            })

    priority_rank = {"High": 0, "Medium": 1, "Low": 2}
    queue.sort(key=lambda r: priority_rank[r["priority"]])
    return {"queue": queue}


# ---------------------------------------------------------------------------
# Founder home dashboard snapshot
# ---------------------------------------------------------------------------

def _snapshot(
    db: Session,
    org_id: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> Dict[str, Any]:
    today = datetime.now(timezone.utc).date()
    start_of_today = datetime.combine(today, datetime.min.time(), tzinfo=timezone.utc)
    start_of_tomorrow = start_of_today + timedelta(days=1)

    # New-lead and call counts are scoped to the [start, end) window when the
    # caller supplies a date range (the Daily Snapshot date picker); otherwise
    # they fall back to "today". Pipeline totals / hot / conversion below are
    # current-state and not range-dependent.
    win_start = start if start is not None else start_of_today
    win_end = end if end is not None else start_of_tomorrow
    ranged = start is not None or end is not None

    leads_today = (
        db.query(Lead)
        .filter(
            Lead.org_id == org_id,
            Lead.created_at >= win_start,
            Lead.created_at < win_end,
        )
        .count()
    )
    calls_today = (
        db.query(AudioCall)
        .filter(
            AudioCall.org_id == org_id,
            AudioCall.timestamp >= win_start,
            AudioCall.timestamp < win_end,
        )
        .count()
    )

    scores_by_contact = _latest_verdicts_by_contact(db, org_id)
    hot_leads = sum(1 for v in scores_by_contact.values() if v == "Hot")

    total_leads = db.query(Lead).filter(Lead.org_id == org_id).count()
    closed_won = (
        db.query(Lead)
        .filter(Lead.org_id == org_id, Lead.pipeline_stage == "Closed Won")
        .count()
    )
    conversion_rate_pct = round(100 * closed_won / total_leads, 1) if total_leads else 0.0

    return {
        "leads_today": leads_today,
        "calls_today": calls_today,
        "hot_leads": hot_leads,
        "conversion_rate_pct": conversion_rate_pct,
        "total_leads": total_leads,
        "ranged": ranged,
    }


def _parse_snapshot_date(value: Optional[str], *, field: str) -> Optional[datetime]:
    """Parse a YYYY-MM-DD query param to a UTC midnight datetime, or None."""
    if not value:
        return None
    try:
        d = datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"{field} must be a YYYY-MM-DD date",
        )
    return datetime.combine(d, datetime.min.time(), tzinfo=timezone.utc)


@router.get("/dashboard/snapshot", status_code=status.HTTP_200_OK)
async def get_dashboard_snapshot(
    start: Optional[str] = None,
    end: Optional[str] = None,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """`start`/`end` (YYYY-MM-DD, inclusive) scope the new-lead and call counts to
    that window; omit both for today's figures."""
    s = _parse_snapshot_date(start, field="start")
    e = _parse_snapshot_date(end, field="end")
    if e is not None:
        e = e + timedelta(days=1)  # inclusive end date -> exclusive next-day bound
    if s is not None and e is not None and e <= s:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail="end must be on or after start")
    return _snapshot(db, current_user.org_id, s, e)


# ---------------------------------------------------------------------------
# Revenue — chart + monthly goal
#
# Backed by Lead.deal_value / Lead.closed_at, populated when a lead is moved
# to "Closed Won" on the kanban board (see update_lead_stage above), and by
# Organization.monthly_revenue_target, set from the settings page. An org that
# hasn't set a target gets null target fields rather than a fabricated number.
# ---------------------------------------------------------------------------

_VALID_REVENUE_RANGE_DAYS = {1, 7, 30, 90}


def _closed_leads_between(db: Session, org_id: str, start: datetime, end: datetime) -> List[Lead]:
    return (
        db.query(Lead)
        .filter(
            Lead.org_id == org_id,
            Lead.pipeline_stage == "Closed Won",
            Lead.closed_at.isnot(None),
            Lead.closed_at >= start,
            Lead.closed_at < end,
        )
        .all()
    )


def _utc_date(dt: datetime):
    """dt.date() alone is wrong for a tz-aware datetime unless dt is already in
    UTC — psycopg2 returns timestamptz values in the connection's local
    timezone (IST here), not UTC, so `.date()` can disagree with the UTC
    calendar day used everywhere else in this file (worst around the
    UTC/IST midnight boundary, a ~5.5h window every day)."""
    return dt.astimezone(timezone.utc).date()


def _month_bounds(d) -> tuple:
    start = datetime(d.year, d.month, 1, tzinfo=timezone.utc)
    days_in_month = calendar.monthrange(d.year, d.month)[1]
    end = start + timedelta(days=days_in_month)
    return start, end, days_in_month


def _prev_month_bounds(month_start: datetime) -> tuple:
    prev_last_day = month_start - timedelta(days=1)
    return _month_bounds(prev_last_day)


@router.get("/dashboard/revenue", status_code=status.HTTP_200_OK)
async def get_dashboard_revenue(
    range_days: int = Query(30, alias="range"),
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    if range_days not in _VALID_REVENUE_RANGE_DAYS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"range must be one of {sorted(_VALID_REVENUE_RANGE_DAYS)}",
        )

    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    org_id = current_user.org_id
    now = datetime.now(timezone.utc)
    today = now.date()

    month_start, month_end, days_in_month = _month_bounds(today)
    days_elapsed = today.day  # 1-indexed, inclusive of today

    # Daily totals for the full current month — used for MTD, on/off-target
    # day counts, and the previous-month comparison below.
    month_closed = _closed_leads_between(db, org_id, month_start, month_end)
    month_by_day: Dict[str, int] = {}
    for lead in month_closed:
        key = _utc_date(lead.closed_at).isoformat()
        month_by_day[key] = month_by_day.get(key, 0) + (lead.deal_value or 0)
    mtd_total = sum(month_by_day.values())

    target_per_day: Optional[float] = (
        org.monthly_revenue_target / days_in_month if org and org.monthly_revenue_target else None
    )
    on_target_days = off_target_days = None
    if target_per_day is not None:
        on_target_days, off_target_days = 0, 0
        for i in range(days_elapsed):
            day_key = (month_start.date() + timedelta(days=i)).isoformat()
            if month_by_day.get(day_key, 0) >= target_per_day:
                on_target_days += 1
            else:
                off_target_days += 1

    # Previous month, same number of elapsed days — an apples-to-apples
    # comparison for the "+X% vs last mo" figure.
    prev_start, _prev_end, prev_days_in_month = _prev_month_bounds(month_start)
    prev_compare_end = prev_start + timedelta(days=min(days_elapsed, prev_days_in_month))
    prev_partial_total = sum(
        (lead.deal_value or 0) for lead in _closed_leads_between(db, org_id, prev_start, prev_compare_end)
    )
    pct_change_vs_last_month = (
        round(100 * (mtd_total - prev_partial_total) / prev_partial_total, 1) if prev_partial_total > 0 else None
    )

    # Chart series for the selected range (1D/7D/30D/90D) — may span into
    # previous months, so it's queried separately from the month-scoped totals.
    window_start_date = today - timedelta(days=range_days - 1)
    window_start = datetime(window_start_date.year, window_start_date.month, window_start_date.day, tzinfo=timezone.utc)
    window_by_day: Dict[str, int] = dict(month_by_day)
    if window_start < month_start:
        for lead in _closed_leads_between(db, org_id, window_start, month_start):
            key = _utc_date(lead.closed_at).isoformat()
            window_by_day[key] = window_by_day.get(key, 0) + (lead.deal_value or 0)

    series = []
    for i in range(range_days):
        d = window_start_date + timedelta(days=i)
        series.append({"date": d.isoformat(), "day": d.day, "revenue": window_by_day.get(d.isoformat(), 0)})

    best_day = max(series, key=lambda p: p["revenue"]) if series and any(p["revenue"] for p in series) else None
    avg_per_day = round(mtd_total / days_elapsed) if days_elapsed else 0

    return {
        "range_days": range_days,
        "series": series,
        "mtd_total": mtd_total,
        "avg_per_day": avg_per_day,
        "best_day": best_day,
        "pct_change_vs_last_month": pct_change_vs_last_month,
        "target_per_day": round(target_per_day) if target_per_day is not None else None,
        "working_days_elapsed": days_elapsed,
        "days_in_month": days_in_month,
        "on_target_days": on_target_days,
        "off_target_days": off_target_days,
    }


@router.get("/dashboard/goal", status_code=status.HTTP_200_OK)
async def get_dashboard_goal(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    org_id = current_user.org_id
    now = datetime.now(timezone.utc)
    today = now.date()

    month_start, month_end, days_in_month = _month_bounds(today)
    closed = _closed_leads_between(db, org_id, month_start, month_end)
    mtd_revenue = sum(lead.deal_value or 0 for lead in closed)
    deals_closed = len(closed)
    avg_deal_value = round(mtd_revenue / deals_closed) if deals_closed else None

    days_left = max(0, days_in_month - today.day)
    target = org.monthly_revenue_target if org else None
    pct_of_target = round(100 * mtd_revenue / target, 1) if target else None
    remaining = max(0, target - mtd_revenue) if target else None
    needed_per_day = round(remaining / days_left) if remaining is not None and days_left > 0 else (
        0 if remaining == 0 else None
    )

    return {
        "monthly_target": target,
        "mtd_revenue": mtd_revenue,
        "pct_of_target": pct_of_target,
        "days_left": days_left,
        "needed_per_day": needed_per_day,
        "deals_closed": deals_closed,
        "avg_deal_value": avg_deal_value,
    }


# ---------------------------------------------------------------------------
# Live activity feed — real recent events, no ad-platform-derived events
# (budget/campaign alerts) since Meta/Google Ads integration is out of scope
# per the campaigns page's own deliberate mock-data decision.
# ---------------------------------------------------------------------------

_IDLE_THRESHOLD_MINUTES = 30
_ACTIVE_QUEUE_STAGES = ["New", "Assigned", "Contacted", "Interested", "Proposal Sent", "Negotiation"]


@router.get("/dashboard/activity", status_code=status.HTTP_200_OK)
async def get_dashboard_activity(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    org_id = current_user.org_id
    now = datetime.now(timezone.utc)
    events: List[Dict[str, Any]] = []

    recent_closed = (
        db.query(Lead)
        .filter(Lead.org_id == org_id, Lead.pipeline_stage == "Closed Won", Lead.closed_at.isnot(None))
        .order_by(Lead.closed_at.desc())
        .limit(5)
        .all()
    )
    for lead in recent_closed:
        value = f"₹{lead.deal_value:,}" if lead.deal_value else "value not recorded"
        events.append({
            "id": f"deal-{lead.id}",
            "type": "success",
            "time": lead.closed_at.isoformat(),
            "title": "Deal Closed",
            "detail": f"{lead.name or lead.contact_key} · {value}",
            "cta": "View",
        })

    hot_rows = (
        db.query(AudioCall.call_id, AudioCall.timestamp, LeadAnalysis.bant_score)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(
            AudioCall.org_id == org_id,
            LeadAnalysis.status == "completed",
            LeadAnalysis.lead_verdict == "Hot",
        )
        .order_by(AudioCall.timestamp.desc())
        .limit(5)
        .all()
    )
    leads_by_contact = {
        lead.contact_key: lead
        for lead in db.query(Lead).filter(Lead.org_id == org_id).all()
    }
    for call_id, ts, bant_score in hot_rows:
        contact_key = contact_key_from_call_id(call_id)
        lead = leads_by_contact.get(contact_key)
        name = lead.name if lead and lead.name else contact_key
        source = lead.source if lead else None
        events.append({
            "id": f"hot-{call_id}",
            "type": "info",
            "time": ts.isoformat() if ts else now.isoformat(),
            "title": "High-score Lead",
            "detail": f"{source or 'Unknown source'} · {name} · Score {round(bant_score)}",
            "cta": "Assign",
        })

    telecallers = db.query(User).filter(User.org_id == org_id, User.role == "telecaller").all()
    last_call_by_tc: Dict[str, datetime] = {}
    for tc_id, ts in (
        db.query(AudioCall.telecaller_id, AudioCall.timestamp)
        .filter(AudioCall.org_id == org_id, AudioCall.telecaller_id.isnot(None))
        .all()
    ):
        if tc_id not in last_call_by_tc or ts > last_call_by_tc[tc_id]:
            last_call_by_tc[tc_id] = ts
    queue_counts: Dict[str, int] = {}
    for lead in db.query(Lead).filter(Lead.org_id == org_id, Lead.pipeline_stage.in_(_ACTIVE_QUEUE_STAGES)).all():
        if lead.assigned_to:
            queue_counts[lead.assigned_to] = queue_counts.get(lead.assigned_to, 0) + 1
    for tc in telecallers:
        queue = queue_counts.get(tc.id, 0)
        if queue == 0:
            continue
        last_call = last_call_by_tc.get(tc.id)
        idle_minutes = int((now - last_call).total_seconds() / 60) if last_call else None
        if idle_minutes is not None and idle_minutes < _IDLE_THRESHOLD_MINUTES:
            continue
        idle_label = f"idle {idle_minutes} min" if idle_minutes is not None else "no calls yet"
        events.append({
            "id": f"idle-{tc.id}",
            "type": "warning",
            "time": (last_call or now).isoformat(),
            "title": "Telecaller Idle",
            "detail": f"{tc.name} · {idle_label} · {queue} lead{'s' if queue != 1 else ''} in queue",
            "cta": "Review",
        })

    events.sort(key=lambda e: e["time"], reverse=True)
    return {"events": events[:8]}


# ---------------------------------------------------------------------------
# Lead quality deep-dive
# ---------------------------------------------------------------------------

def _latest_verdicts_by_contact(db: Session, org_id: str) -> Dict[str, str]:
    """{contact_key: latest lead_verdict}, newest call per contact wins.

    Mirrors _latest_scores_by_contact's approach (AudioCall has no contact_key
    column; it's derived from call_id) but tracks lead_verdict instead of
    bant_score.
    """
    rows = (
        db.query(AudioCall.call_id, AudioCall.timestamp, LeadAnalysis.lead_verdict)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(AudioCall.org_id == org_id, LeadAnalysis.status == "completed")
        .order_by(AudioCall.timestamp.asc())
        .all()
    )
    latest: Dict[str, str] = {}
    for call_id, _ts, verdict in rows:
        if verdict:
            latest[contact_key_from_call_id(call_id)] = verdict  # later rows overwrite → newest wins
    return latest


def _lead_quality(db: Session, org_id: str) -> Dict[str, Any]:
    verdicts_by_contact = _latest_verdicts_by_contact(db, org_id)
    verdict_breakdown = {"Hot": 0, "Warm": 0, "Cold": 0, "Junk": 0}
    for verdict in verdicts_by_contact.values():
        if verdict in verdict_breakdown:
            verdict_breakdown[verdict] += 1

    leads = db.query(Lead).filter(Lead.org_id == org_id).all()
    source_breakdown: Dict[str, int] = {}
    # Cross-tab source × verdict/close — previously source_breakdown and
    # verdict_breakdown were computed independently, so "junk% per source"
    # couldn't be derived from the response at all.
    by_source: Dict[str, Dict[str, int]] = {}
    for lead in leads:
        source = lead.source or "Unknown"
        source_breakdown[source] = source_breakdown.get(source, 0) + 1
        row = by_source.setdefault(source, {"total": 0, "junk": 0, "positive": 0, "closed_won": 0})
        row["total"] += 1
        verdict = verdicts_by_contact.get(lead.contact_key)
        if verdict == "Junk":
            row["junk"] += 1
        if verdict in ("Hot", "Warm"):
            row["positive"] += 1
        if lead.pipeline_stage == "Closed Won":
            row["closed_won"] += 1

    source_matrix = [
        {
            "source": source,
            "total": row["total"],
            "junk_pct": round(100 * row["junk"] / row["total"], 1) if row["total"] else 0.0,
            "positive_pct": round(100 * row["positive"] / row["total"], 1) if row["total"] else 0.0,
            "close_pct": round(100 * row["closed_won"] / row["total"], 1) if row["total"] else 0.0,
        }
        for source, row in by_source.items()
    ]
    source_matrix.sort(key=lambda r: r["total"], reverse=True)

    scores_by_contact = _latest_scores_by_contact(db, org_id)
    avg_bant_score = (
        round(sum(scores_by_contact.values()) / len(scores_by_contact), 1)
        if scores_by_contact else None
    )

    return {
        "verdict_breakdown": verdict_breakdown,
        "source_breakdown": source_breakdown,
        "source_matrix": source_matrix,
        "avg_bant_score": avg_bant_score,
    }


@router.get("/leads/quality", status_code=status.HTTP_200_OK)
async def get_leads_quality(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return _lead_quality(db, current_user.org_id)


_SCORE_BANDS = [(81, 100, "81-100"), (61, 80, "61-80"), (41, 60, "41-60"), (21, 40, "21-40"), (0, 20, "0-20")]


@router.get("/leads/score-distribution", status_code=status.HTTP_200_OK)
async def get_score_distribution(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Buckets each contact's latest BANT score into the PRD's 5 bands, with
    close-rate per band (PRD Layer 3-B.2)."""
    scores_by_contact = _latest_scores_by_contact(db, current_user.org_id)
    leads = db.query(Lead).filter(Lead.org_id == current_user.org_id).all()
    stage_by_contact = {lead.contact_key: lead.pipeline_stage for lead in leads}

    bands = []
    total = len(scores_by_contact)
    for lo, hi, label in _SCORE_BANDS:
        contacts = [ck for ck, score in scores_by_contact.items() if lo <= score <= hi]
        count = len(contacts)
        closed = sum(1 for ck in contacts if stage_by_contact.get(ck) == "Closed Won")
        bands.append({
            "label": label,
            "count": count,
            "pct_of_total": round(100 * count / total, 1) if total else 0.0,
            "close_rate_pct": round(100 * closed / count, 1) if count else 0.0,
        })
    return {"bands": bands}


@router.get("/leads/ageing", status_code=status.HTTP_200_OK)
async def get_leads_ageing(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Buckets open (non-terminal) leads by days-since-last-update (PRD Layer
    3-A.2) — reuses the same days_stuck computation as /leads/board."""
    leads = (
        db.query(Lead)
        .filter(
            Lead.org_id == current_user.org_id,
            Lead.pipeline_stage.notin_(["Closed Won", "Closed Lost", "Junk"]),
        )
        .all()
    )
    now = datetime.now(timezone.utc)
    summary = {"0-3": 0, "3-7": 0, "7+": 0}
    rows = []
    for lead in leads:
        updated = lead.updated_at or lead.created_at
        days = max(0, (now - updated).days) if updated else 0
        bucket = "0-3" if days <= 3 else "3-7" if days <= 7 else "7+"
        summary[bucket] += 1
        rows.append({
            "id": lead.id,
            "name": lead.name or lead.contact_key,
            "source": lead.source,
            "pipeline_stage": lead.pipeline_stage,
            "days_stuck": days,
            "bucket": bucket,
        })
    rows.sort(key=lambda r: r["days_stuck"], reverse=True)
    return {"summary": summary, "leads": rows}


# ---------------------------------------------------------------------------
# Lead wastage — leads with zero calls, aged out
# ---------------------------------------------------------------------------

# Built-in alert thresholds — used when an org hasn't set its own alert_config
# (settings → Alert Configuration). Keys mirror schemas_auth.AlertConfig.
_ALERT_DEFAULTS = {"wastage_days": 3, "zombie_days": 7, "performance_gap": 15, "quality_floor": 40}


def _alert_config(db: Session, org_id: str) -> Dict[str, int]:
    """Merge an org's saved alert thresholds over the built-in defaults, keeping
    only sane numeric overrides so a malformed blob can't break the engine."""
    cfg = dict(_ALERT_DEFAULTS)
    org = db.query(Organization).filter(Organization.id == org_id).first()
    raw = org.alert_config if org else None
    if isinstance(raw, dict):
        for key in _ALERT_DEFAULTS:
            val = raw.get(key)
            if isinstance(val, (int, float)) and val >= 0:
                cfg[key] = int(val)
    return cfg


def _wasted_leads(db: Session, org_id: str, threshold_days: int = 3) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)

    called_contact_keys = {
        contact_key_from_call_id(call_id)
        for (call_id,) in db.query(AudioCall.call_id).filter(AudioCall.org_id == org_id).all()
    }

    leads = (
        db.query(Lead)
        .filter(Lead.org_id == org_id, Lead.pipeline_stage.in_(["New", "Assigned"]))
        .all()
    )

    wasted = []
    for lead in leads:
        if lead.contact_key in called_contact_keys:
            continue
        created = lead.created_at or now
        days_since_created = (now - created).days
        if days_since_created >= threshold_days:
            wasted.append({
                "id": lead.id,
                "name": lead.name or lead.contact_key,
                "source": lead.source,
                "days_since_created": days_since_created,
                "pipeline_stage": lead.pipeline_stage,
            })

    wasted.sort(key=lambda l: l["days_since_created"], reverse=True)
    return wasted


@router.get("/leads/wastage", status_code=status.HTTP_200_OK)
async def get_leads_wastage(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _alert_config(db, current_user.org_id)
    wasted = _wasted_leads(db, current_user.org_id, cfg["wastage_days"])
    return {"leads": wasted, "total_wasted": len(wasted)}


# ---------------------------------------------------------------------------
# Zombie leads — stalled mid-pipeline
# ---------------------------------------------------------------------------

_ZOMBIE_EXCLUDED_STAGES = ["New", "Closed Won", "Closed Lost", "Junk"]
_ZOMBIE_THRESHOLD_DAYS = 7


def _zombie_leads(db: Session, org_id: str, threshold_days: int = _ZOMBIE_THRESHOLD_DAYS) -> List[Dict[str, Any]]:
    now = datetime.now(timezone.utc)

    leads = (
        db.query(Lead)
        .filter(Lead.org_id == org_id, ~Lead.pipeline_stage.in_(_ZOMBIE_EXCLUDED_STAGES))
        .all()
    )
    telecallers = {
        u.id: u.name
        for u in db.query(User).filter(User.org_id == org_id).all()
    }

    zombies = []
    for lead in leads:
        updated = lead.updated_at or lead.created_at
        if updated is None:
            continue
        days_stalled = (now - updated).days
        if days_stalled >= threshold_days:
            zombies.append({
                "id": lead.id,
                "name": lead.name or lead.contact_key,
                "pipeline_stage": lead.pipeline_stage,
                "days_stalled": days_stalled,
                "telecaller_name": telecallers.get(lead.assigned_to),
            })

    zombies.sort(key=lambda l: l["days_stalled"], reverse=True)
    return zombies


@router.get("/leads/zombie", status_code=status.HTTP_200_OK)
async def get_leads_zombie(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    cfg = _alert_config(db, current_user.org_id)
    zombies = _zombie_leads(db, current_user.org_id, cfg["zombie_days"])
    return {"leads": zombies, "threshold_days": cfg["zombie_days"]}


# ---------------------------------------------------------------------------
# Telecaller performance detail (single telecaller)
# ---------------------------------------------------------------------------

@router.get("/telecallers/performance/{telecaller_id}", status_code=status.HTTP_200_OK)
async def get_telecaller_performance_detail(
    telecaller_id: str,
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    telecaller = (
        db.query(User)
        .filter(User.id == telecaller_id, User.org_id == current_user.org_id, User.role == "telecaller")
        .first()
    )
    if telecaller is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Telecaller not found")

    metrics = _telecaller_metrics(db, telecaller_id, current_user.org_id)

    calls = (
        db.query(AudioCall)
        .filter(AudioCall.telecaller_id == telecaller_id, AudioCall.org_id == current_user.org_id)
        .all()
    )
    analyses = {
        a.call_id: a
        for a in db.query(LeadAnalysis)
        .join(AudioCall, AudioCall.call_id == LeadAnalysis.call_id)
        .filter(AudioCall.telecaller_id == telecaller_id, AudioCall.org_id == current_user.org_id,
                LeadAnalysis.status == "completed")
        .all()
    }

    scored_calls = []
    for c in calls:
        a = analyses.get(c.call_id)
        if a is None or not isinstance(a.agent_debrief, dict):
            continue
        total_score = a.agent_debrief.get("total_score")
        if not isinstance(total_score, (int, float)):
            continue
        scored_calls.append({
            "call_id": c.call_id,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
            "lead_verdict": a.lead_verdict,
            "total_score": total_score,
        })

    best_calls = sorted(scored_calls, key=lambda c: c["total_score"], reverse=True)[:5]
    needs_review = sorted(scored_calls, key=lambda c: c["total_score"])[:5]

    timeline_calls = sorted(
        [c for c in calls if c.timestamp is not None],
        key=lambda c: c.timestamp,
        reverse=True,
    )[:20]
    timeline = [
        {
            "call_id": c.call_id,
            "timestamp": c.timestamp.isoformat() if c.timestamp else None,
            "lead_verdict": analyses[c.call_id].lead_verdict if c.call_id in analyses else None,
        }
        for c in timeline_calls
    ]

    return {
        "id": telecaller.id,
        "name": telecaller.name,
        **metrics,
        "best_calls": best_calls,
        "needs_review": needs_review,
        "timeline": timeline,
    }


# ---------------------------------------------------------------------------
# Rule-based insights
# ---------------------------------------------------------------------------

def _insights(db: Session, org_id: str) -> List[Dict[str, str]]:
    insights: List[Dict[str, str]] = []
    cfg = _alert_config(db, org_id)

    wasted = _wasted_leads(db, org_id, cfg["wastage_days"])
    if len(wasted) > 0:
        insights.append({
            "id": uuid.uuid4().hex,
            "category": "wastage",
            "severity": "high" if len(wasted) >= 5 else "medium",
            "title": f"{len(wasted)} leads have gone untouched for {cfg['wastage_days']}+ days",
            "description": (
                f"{len(wasted)} leads are still in New/Assigned with no calls made, "
                f"and have sat for {cfg['wastage_days']} or more days without contact."
            ),
        })

    zombies = _zombie_leads(db, org_id, cfg["zombie_days"])
    if len(zombies) > 0:
        insights.append({
            "id": uuid.uuid4().hex,
            "category": "zombie",
            "severity": "high" if len(zombies) >= 5 else "medium",
            "title": f"{len(zombies)} leads stalled mid-pipeline for {cfg['zombie_days']}+ days",
            "description": (
                f"{len(zombies)} leads have been sitting in an active pipeline stage "
                f"for {cfg['zombie_days']} or more days without progressing."
            ),
        })

    telecallers = (
        db.query(User)
        .filter(User.org_id == org_id, User.role == "telecaller")
        .all()
    )
    if telecallers:
        metrics_by_tc = _telecaller_metrics_batch(db, [tc.id for tc in telecallers], org_id)
        tc_metrics = [
            {"id": tc.id, "name": tc.name, **metrics_by_tc[tc.id]}
            for tc in telecallers
        ]
        team_avg_quality = sum(t["quality"] for t in tc_metrics) / len(tc_metrics)
        for t in tc_metrics:
            if team_avg_quality - t["quality"] > cfg["performance_gap"]:
                gap = round(team_avg_quality - t["quality"])
                insights.append({
                    "id": uuid.uuid4().hex,
                    "category": "performance",
                    "severity": "medium",
                    "title": f"{t['name']}'s quality score is {gap} points below team average",
                    "description": (
                        f"{t['name']} has a quality score of {t['quality']}, compared to the "
                        f"team average of {round(team_avg_quality, 1)}."
                    ),
                })

    quality = _lead_quality(db, org_id)
    if quality["avg_bant_score"] is not None and quality["avg_bant_score"] < cfg["quality_floor"]:
        insights.append({
            "id": uuid.uuid4().hex,
            "category": "quality",
            "severity": "low",
            "title": f"Average lead quality this period is below {cfg['quality_floor']}/100",
            "description": (
                f"The average BANT score across scored leads is {quality['avg_bant_score']}, "
                f"below the healthy threshold of {cfg['quality_floor']}."
            ),
        })

    return insights


@router.get("/insights", status_code=status.HTTP_200_OK)
async def get_insights(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    return {"insights": _insights(db, current_user.org_id)}


# ---------------------------------------------------------------------------
# Report preview
# ---------------------------------------------------------------------------

_REPORT_TYPES = ["weekly_summary", "telecaller_performance", "lead_quality"]


async def _telecaller_performance_payload(db: Session, org_id: str) -> Dict[str, Any]:
    telecallers = (
        db.query(User)
        .filter(User.org_id == org_id, User.role == "telecaller")
        .all()
    )

    metrics_by_tc = _telecaller_metrics_batch(db, [tc.id for tc in telecallers], org_id)
    results: List[Dict[str, Any]] = [
        {"id": tc.id, "name": tc.name, **metrics_by_tc[tc.id]} for tc in telecallers
    ]

    def _avg(key: str) -> float:
        vals = [r[key] for r in results]
        return round(sum(vals) / len(vals), 1) if vals else 0.0

    team_average = {
        "calls": round(_avg("calls")),
        "connect_pct": _avg("connect_pct"),
        "positive_pct": _avg("positive_pct"),
        "close_pct": _avg("close_pct"),
        "talk_time_seconds": round(_avg("talk_time_seconds")),
        "quality": round(_avg("quality")),
        "dimensions": {
            dim: round(sum(r["dimensions"][dim] for r in results) / len(results)) if results else 0
            for dim in _DIMENSIONS
        },
    }

    return {"telecallers": results, "team_average": team_average}


@router.get("/reports/preview", status_code=status.HTTP_200_OK)
async def get_report_preview(
    report_type: str,
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    if report_type not in _REPORT_TYPES:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"report_type must be one of {_REPORT_TYPES}",
        )

    org_id = current_user.org_id

    if report_type == "weekly_summary":
        data = _snapshot(db, org_id)
        cfg = _alert_config(db, org_id)
        data["zombie_count"] = len(_zombie_leads(db, org_id, cfg["zombie_days"]))
        data["wastage_count"] = len(_wasted_leads(db, org_id, cfg["wastage_days"]))
    elif report_type == "telecaller_performance":
        data = await _telecaller_performance_payload(db, org_id)
    else:  # lead_quality
        data = _lead_quality(db, org_id)

    return {
        "report_type": report_type,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "data": data,
    }

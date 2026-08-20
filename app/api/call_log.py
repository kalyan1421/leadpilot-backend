"""Device call-log sync — every inbound/outbound/missed call a telecaller's
phone recorded, not just calls placed through this app's own dialer button.

Previously the app only ever knew about calls it placed itself (via its own
Call button), with no direction, duration, or phone stored server-side at
all. This module gives the phone's real call history somewhere to sync to.
"""

import logging
import uuid
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy import and_
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models import CallLog, Lead, User
from app.utils.notifications import add_notification
from app.schemas_call_log import (
    CallLogListResponse,
    CallLogSyncRequest,
    CallLogSyncResponse,
)

logger = logging.getLogger(__name__)

# Sibling to /api/calls (not nested under it) so this never collides with
# calls.py's GET /api/calls/{call_id} catch-all — see that file's own comment
# about router-registration-order path collisions for why nesting here would
# be fragile.
router = APIRouter(prefix="/api/call-log", tags=["call-log"])


@router.post("/sync", response_model=CallLogSyncResponse)
async def sync_call_log(
    payload: CallLogSyncRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Upserts device call-log entries, keyed by (telecaller_id, device_call_id)
    so a re-sync (app relaunch, retry) never duplicates a call already known."""
    if not payload.entries:
        return CallLogSyncResponse(synced=0)

    # Best-effort resolve each phone to a known lead in this org, so tapping a
    # call-log tile can navigate straight to the lead (same as an app-placed
    # call already does).
    phones = {e.phone for e in payload.entries}
    lead_by_phone = {
        row.phone: row.id
        for row in db.query(Lead.id, Lead.phone).filter(
            Lead.org_id == current_user.org_id, Lead.phone.in_(phones)
        )
    }
    # entry.lead_id is client-supplied and was previously trusted verbatim
    # with no ownership check — a malformed or malicious client could link a
    # call-log row to an arbitrary lead id from another org. Only accept it
    # when it actually names a lead in the caller's own org; otherwise fall
    # back to the phone-based match like an entry with no lead_id at all.
    claimed_lead_ids = {e.lead_id for e in payload.entries if e.lead_id}
    valid_lead_ids = (
        {
            row.id
            for row in db.query(Lead.id).filter(
                Lead.org_id == current_user.org_id, Lead.id.in_(claimed_lead_ids)
            )
        }
        if claimed_lead_ids
        else set()
    )

    # Manual upsert (not a DB-level ON CONFLICT) so this works identically
    # against the SQLite test DB and production Postgres — same convention
    # as the content_hash dedup in calls.py's upload endpoint.
    existing_by_device_id = {
        row.device_call_id: row
        for row in db.query(CallLog).filter(
            CallLog.telecaller_id == current_user.id,
            CallLog.device_call_id.in_([e.device_call_id for e in payload.entries]),
        )
    }

    synced = 0
    new_entries = 0
    linked_entries = 0
    for entry in payload.entries:
        claimed = entry.lead_id if entry.lead_id in valid_lead_ids else None
        lead_id = claimed or lead_by_phone.get(entry.phone)
        row = existing_by_device_id.get(entry.device_call_id)
        if row is not None:
            row.duration_seconds = entry.duration_seconds
            row.direction = entry.direction
            row.called_at = entry.called_at
            row.lead_id = lead_id
        else:
            db.add(
                CallLog(
                    id=str(uuid.uuid4()),
                    org_id=current_user.org_id,
                    telecaller_id=current_user.id,
                    phone=entry.phone,
                    direction=entry.direction,
                    duration_seconds=entry.duration_seconds,
                    called_at=entry.called_at,
                    device_call_id=entry.device_call_id,
                    lead_id=lead_id,
                )
            )
            new_entries += 1
            if lead_id:
                linked_entries += 1
        synced += 1
    if new_entries:
        add_notification(
            db,
            org_id=current_user.org_id,
            notification_type="telecaller_call_activity",
            title="Telecaller call activity logged",
            message=(
                f"{current_user.name} logged {new_entries} new call{'s' if new_entries != 1 else ''}"
                f"{f' linked to {linked_entries} lead' + ('s' if linked_entries != 1 else '') if linked_entries else ''}."
            ),
            severity="info",
            entity_type="telecaller",
            entity_id=current_user.id,
            actor_name=current_user.name,
        )
    db.commit()
    return CallLogSyncResponse(synced=synced)


@router.get("", response_model=CallLogListResponse)
async def list_call_log(
    direction: Optional[str] = Query(None, pattern="^(inbound|outbound|missed)$"),
    start_date: Optional[datetime] = None,
    end_date: Optional[datetime] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(200, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A telecaller's own call history (org + telecaller scoped, mirroring
    follow-ups) — the dialer-style 'all calls' list, with optional direction
    and date-range filters."""
    filters = [
        CallLog.org_id == current_user.org_id,
        CallLog.telecaller_id == current_user.id,
    ]
    if direction:
        filters.append(CallLog.direction == direction)
    if start_date:
        filters.append(CallLog.called_at >= start_date)
    if end_date:
        filters.append(CallLog.called_at <= end_date)

    query = db.query(CallLog).filter(and_(*filters))
    total = query.count()
    rows = (
        query.order_by(CallLog.called_at.desc()).offset(skip).limit(limit).all()
    )
    return CallLogListResponse(calls=rows, total=total)

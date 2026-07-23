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
    for entry in payload.entries:
        lead_id = entry.lead_id or lead_by_phone.get(entry.phone)
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
        synced += 1
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

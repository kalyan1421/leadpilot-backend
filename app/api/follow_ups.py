"""Telecaller follow-ups — scheduled call/WhatsApp/email touches on a lead.

Previously this data lived only in the Flutter app's on-device
SharedPreferences (LocalFollowUpStore) with no backend endpoint at all, so the
founder dashboard's "missed follow-up rate" leakage metric had no real source
data. This gives the telecaller app somewhere real to sync to.
"""

import logging
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user
from app.database import get_db
from app.models import FollowUp, Lead, User
from app.schemas_follow_up import (
    FollowUpCreate,
    FollowUpListResponse,
    FollowUpResponse,
    FollowUpUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/follow-ups", tags=["follow-ups"])


@router.get("", response_model=FollowUpListResponse)
async def list_follow_ups(
    include_completed: bool = Query(False),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A telecaller's own follow-ups (org-scoped, and further scoped to the
    caller — founders/managers use org-wide leakage aggregates elsewhere, this
    endpoint is the telecaller's personal queue)."""
    query = db.query(FollowUp).filter(
        FollowUp.org_id == current_user.org_id,
        FollowUp.telecaller_id == current_user.id,
    )
    if not include_completed:
        query = query.filter(FollowUp.completed_at.is_(None))
    rows = query.order_by(FollowUp.due_at.asc()).all()
    return FollowUpListResponse(follow_ups=rows)


@router.post("", response_model=FollowUpResponse, status_code=status.HTTP_201_CREATED)
async def create_follow_up(
    payload: FollowUpCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if payload.lead_id is not None:
        lead_exists = (
            db.query(Lead.id)
            .filter(Lead.id == payload.lead_id, Lead.org_id == current_user.org_id)
            .first()
        )
        if lead_exists is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Lead not found")

    record = FollowUp(
        id=str(uuid.uuid4()),
        org_id=current_user.org_id,
        telecaller_id=current_user.id,
        lead_id=payload.lead_id,
        note=payload.note,
        due_at=payload.due_at,
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return record


@router.patch("/{follow_up_id}", response_model=FollowUpResponse)
async def update_follow_up(
    follow_up_id: str,
    payload: FollowUpUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = (
        db.query(FollowUp)
        .filter(
            FollowUp.id == follow_up_id,
            FollowUp.org_id == current_user.org_id,
            FollowUp.telecaller_id == current_user.id,
        )
        .first()
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Follow-up not found")

    if payload.note is not None:
        record.note = payload.note
    if payload.due_at is not None:
        record.due_at = payload.due_at
    if payload.completed is not None:
        record.completed_at = datetime.now(timezone.utc) if payload.completed else None

    db.commit()
    db.refresh(record)
    return record


@router.delete("/{follow_up_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_follow_up(
    follow_up_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    record = (
        db.query(FollowUp)
        .filter(
            FollowUp.id == follow_up_id,
            FollowUp.org_id == current_user.org_id,
            FollowUp.telecaller_id == current_user.id,
        )
        .first()
    )
    if record is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Follow-up not found")
    db.delete(record)
    db.commit()

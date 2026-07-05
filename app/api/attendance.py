"""Telecaller attendance — check-in/check-out pairs, timestamp only.

No geolocation, no photo captured — that was an explicit product decision.
One row per user per calendar day (enforced by the uq_attendance_user_date
constraint on the Attendance model).
"""

import logging
import uuid
from datetime import date as date_type
from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session

from app.api.auth import get_current_user, require_role
from app.database import get_db
from app.models import Attendance, User
from app.schemas_attendance import AttendanceListResponse, AttendanceRecordResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/attendance", tags=["attendance"])


def _hours_worked(check_in_at: Optional[datetime], check_out_at: Optional[datetime]) -> Optional[float]:
    if check_in_at is None or check_out_at is None:
        return None
    delta = check_out_at - check_in_at
    return round(delta.total_seconds() / 3600, 2)


def _to_record_response(record: Attendance, telecaller_name: Optional[str] = None) -> AttendanceRecordResponse:
    return AttendanceRecordResponse(
        id=record.id,
        user_id=record.user_id,
        telecaller_name=telecaller_name,
        date=record.date,
        check_in_at=record.check_in_at,
        check_out_at=record.check_out_at,
        hours_worked=_hours_worked(record.check_in_at, record.check_out_at),
    )


@router.post("/check-in", response_model=AttendanceRecordResponse)
async def check_in(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    record = (
        db.query(Attendance)
        .filter(Attendance.user_id == current_user.id, Attendance.date == today)
        .first()
    )

    if record is not None and record.check_in_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already checked in today")

    now = datetime.now(timezone.utc)
    if record is None:
        record = Attendance(
            id=str(uuid.uuid4()),
            org_id=current_user.org_id,
            user_id=current_user.id,
            date=today,
            check_in_at=now,
        )
        db.add(record)
    else:
        record.check_in_at = now

    db.commit()
    db.refresh(record)
    return _to_record_response(record)


@router.post("/check-out", response_model=AttendanceRecordResponse)
async def check_out(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    record = (
        db.query(Attendance)
        .filter(Attendance.user_id == current_user.id, Attendance.date == today)
        .first()
    )

    if record is None or record.check_in_at is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No check-in found for today")
    if record.check_out_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already checked out today")

    record.check_out_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(record)
    return _to_record_response(record)


@router.get("/today", response_model=Optional[AttendanceRecordResponse])
async def get_today(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    today = datetime.now(timezone.utc).date()
    record = (
        db.query(Attendance)
        .filter(Attendance.user_id == current_user.id, Attendance.date == today)
        .first()
    )
    if record is None:
        return None
    return _to_record_response(record)


@router.get("", response_model=AttendanceListResponse)
async def list_attendance(
    from_date: Optional[date_type] = Query(None),
    to_date: Optional[date_type] = Query(None),
    telecaller_id: Optional[str] = Query(None),
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    if to_date is None:
        to_date = datetime.now(timezone.utc).date()
    if from_date is None:
        from_date = to_date - timedelta(days=30)

    query = (
        db.query(Attendance, User)
        .join(User, User.id == Attendance.user_id)
        .filter(
            Attendance.org_id == current_user.org_id,
            Attendance.date >= from_date,
            Attendance.date <= to_date,
        )
    )
    if telecaller_id is not None:
        query = query.filter(Attendance.user_id == telecaller_id)

    rows = query.order_by(Attendance.date.desc(), User.name.asc()).all()

    records = [_to_record_response(record, telecaller_name=user.name) for record, user in rows]
    return AttendanceListResponse(records=records)

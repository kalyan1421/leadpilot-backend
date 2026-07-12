"""Pydantic schemas for the telecaller attendance module."""

from datetime import date, datetime
from typing import List, Optional

from pydantic import BaseModel


class AttendanceRecordResponse(BaseModel):
    id: str
    user_id: str
    telecaller_name: Optional[str] = None  # populated by joining User, None when not needed
    date: date
    check_in_at: Optional[datetime] = None
    check_out_at: Optional[datetime] = None  # raw persisted checkout (None if never checked out)
    # Effective checkout used for hours: the real check_out_at, or the auto-cap
    # for a forgotten checkout. None while genuinely still on shift.
    effective_check_out_at: Optional[datetime] = None
    hours_worked: Optional[float] = None  # (effective_check_out - check_in) in hours, 2dp
    # "completed" (checked out) | "on_shift" (open, within cap) | "auto_closed"
    # (open past the max-shift cap → a forgotten checkout).
    status: str = "on_shift"

    class Config:
        from_attributes = True


class AttendanceCorrectionRequest(BaseModel):
    check_out_at: datetime


class AttendanceListResponse(BaseModel):
    records: List[AttendanceRecordResponse]

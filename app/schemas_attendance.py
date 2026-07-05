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
    check_out_at: Optional[datetime] = None
    hours_worked: Optional[float] = None  # (check_out_at - check_in_at) in hours, rounded to 2dp

    class Config:
        from_attributes = True


class AttendanceListResponse(BaseModel):
    records: List[AttendanceRecordResponse]

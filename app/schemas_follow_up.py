"""Pydantic schemas for the telecaller follow-up module."""

from datetime import datetime
from typing import List, Optional

from pydantic import BaseModel


class FollowUpCreate(BaseModel):
    lead_id: Optional[str] = None
    note: Optional[str] = None
    due_at: datetime


class FollowUpUpdate(BaseModel):
    note: Optional[str] = None
    due_at: Optional[datetime] = None
    completed: Optional[bool] = None  # True -> stamp completed_at now; False -> clear it


class FollowUpResponse(BaseModel):
    id: str
    lead_id: Optional[str] = None
    telecaller_id: str
    note: Optional[str] = None
    due_at: datetime
    completed_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class FollowUpListResponse(BaseModel):
    follow_ups: List[FollowUpResponse]

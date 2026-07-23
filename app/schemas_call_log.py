"""Pydantic schemas for the device call-log sync module (app/api/call_log.py)."""

from datetime import datetime
from typing import List, Literal, Optional

from pydantic import BaseModel, Field

CallDirection = Literal["inbound", "outbound", "missed"]


class CallLogSyncItem(BaseModel):
    """One entry as read off the telecaller's device call log."""

    device_call_id: str
    phone: str
    direction: CallDirection
    duration_seconds: int = Field(ge=0, default=0)
    called_at: datetime
    lead_id: Optional[str] = None


class CallLogSyncRequest(BaseModel):
    entries: List[CallLogSyncItem]


class CallLogSyncResponse(BaseModel):
    synced: int


class CallLogEntryResponse(BaseModel):
    id: str
    phone: str
    direction: CallDirection
    duration_seconds: int
    called_at: datetime
    lead_id: Optional[str] = None
    audio_call_id: Optional[str] = None

    class Config:
        from_attributes = True


class CallLogListResponse(BaseModel):
    calls: List[CallLogEntryResponse]
    total: int

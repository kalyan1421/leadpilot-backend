"""Pydantic schemas for the team-management module."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field

from app.schemas_auth import Password


class TeamMemberResponse(BaseModel):
    id: str
    name: str
    email: str
    phone: Optional[str] = None
    role: str
    status: str  # "Active" / "Inactive", derived from User.is_active
    calls: int
    leads: int
    quality: Optional[int] = None
    last_active: Optional[datetime] = None

    class Config:
        from_attributes = True


class InviteMemberRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=255)
    role: str
    phone: Optional[str] = Field(None, max_length=20)


class InviteMemberResponse(BaseModel):
    member: TeamMemberResponse
    temp_password: str


class UpdateMemberRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None


class SetPasswordRequest(BaseModel):
    """Empty/omitted new_password -> a random temp password is generated (the
    original reset behaviour). Provided -> the founder's chosen value is used
    instead, still one-time-displayed and still forces must_reset_password."""

    new_password: Optional[Password] = None

"""Pydantic schemas for the team-management module."""

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, EmailStr, Field


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

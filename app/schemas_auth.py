"""Pydantic schemas for the org/user/auth module."""

from typing import List, Optional

from pydantic import BaseModel, EmailStr, Field


class RegisterRequest(BaseModel):
    """First user of a brand-new org — creates both the org and the founder account."""

    org_name: str = Field(min_length=2, max_length=255)
    name: str = Field(min_length=1, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: str
    org_id: str
    org_name: str
    email: str
    name: str
    role: str
    must_reset_password: bool

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class OrgProfileRequest(BaseModel):
    """All fields optional — the onboarding wizard sends everything at once on
    launch, the settings page sends only what changed on an edit."""

    # max_length on the string fields mirrors the Organization column widths so
    # overlong input is rejected with a clean 422 instead of hitting a Postgres
    # "value too long" DataError → opaque 500. target_audience/address/logo_url
    # are TEXT (unbounded) and the list fields are JSON (no width limit), so no
    # cap there. logo_url in particular carries a base64 data URL from the logo
    # upload, which is far larger than any VARCHAR width — capping it rejected
    # every logo upload during onboarding.
    name: Optional[str] = Field(None, min_length=2, max_length=255)
    industry: Optional[str] = Field(None, max_length=100)
    website_url: Optional[str] = Field(None, max_length=500)
    services: Optional[List[str]] = None
    pricing_min: Optional[int] = None
    pricing_max: Optional[int] = None
    target_audience: Optional[str] = None
    competitors: Optional[List[str]] = None
    brand_voice: Optional[str] = Field(None, max_length=50)
    languages: Optional[List[str]] = None
    usps: Optional[List[str]] = None
    monthly_revenue_target: Optional[int] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None


class OrgProfileResponse(BaseModel):
    id: str
    name: str
    slug: str
    industry: Optional[str] = None
    website_url: Optional[str] = None
    services: Optional[List[str]] = None
    pricing_min: Optional[int] = None
    pricing_max: Optional[int] = None
    target_audience: Optional[str] = None
    competitors: Optional[List[str]] = None
    brand_voice: Optional[str] = None
    languages: Optional[List[str]] = None
    usps: Optional[List[str]] = None
    monthly_revenue_target: Optional[int] = None
    logo_url: Optional[str] = None
    address: Optional[str] = None

    class Config:
        from_attributes = True

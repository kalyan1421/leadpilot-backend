"""Pydantic schemas for the org/user/auth module."""

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

    class Config:
        from_attributes = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class RenameOrgRequest(BaseModel):
    name: str = Field(min_length=2, max_length=255)


class OrgResponse(BaseModel):
    id: str
    name: str
    slug: str

    class Config:
        from_attributes = True

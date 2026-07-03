"""Org/user auth endpoints. FastAPI is the single identity provider for the whole
platform (web portals + mobile) — there is no separate NestJS auth service.
"""

import logging
import re
import uuid
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Organization, User
from app.schemas_auth import (
    LoginRequest,
    OrgResponse,
    RegisterRequest,
    RenameOrgRequest,
    TokenResponse,
    UserResponse,
)
from app.utils.security import create_access_token, decode_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or str(uuid.uuid4())[:8]


def _unique_slug(db: Session, name: str) -> str:
    base = _slugify(name)
    slug = base
    n = 1
    while db.query(Organization).filter(Organization.slug == slug).first() is not None:
        n += 1
        slug = f"{base}-{n}"
    return slug


def _to_user_response(user: User) -> UserResponse:
    return UserResponse(
        id=user.id,
        org_id=user.org_id,
        org_name=user.organization.name,
        email=user.email,
        name=user.name,
        role=user.role,
    )


def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> User:
    """Shared dependency for any endpoint (auth or otherwise) that needs the caller's identity."""
    if credentials is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid or expired token")

    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None or not user.is_active:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="User not found or inactive")
    return user


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
async def register(body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new org and its first user (role=founder). One call, not two, so the
    founder onboarding flow never ends up with an org and no owner."""
    if db.query(User).filter(User.email == body.email).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")

    org = Organization(id=str(uuid.uuid4()), name=body.org_name, slug=_unique_slug(db, body.org_name))
    db.add(org)
    db.flush()  # assign org.id before the FK reference below

    user = User(
        id=str(uuid.uuid4()),
        org_id=org.id,
        email=body.email,
        hashed_password=hash_password(body.password),
        name=body.name,
        role="founder",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": user.id, "org_id": user.org_id, "role": user.role})
    return TokenResponse(access_token=token, user=_to_user_response(user))


@router.post("/login", response_model=TokenResponse)
async def login(body: LoginRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == body.email).first()
    if user is None or not verify_password(body.password, user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = create_access_token({"sub": user.id, "org_id": user.org_id, "role": user.role})
    return TokenResponse(access_token=token, user=_to_user_response(user))


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return _to_user_response(current_user)


@router.patch("/org", response_model=OrgResponse)
async def rename_org(
    body: RenameOrgRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Lets the founder onboarding wizard's final step set the real org name —
    registration creates a placeholder org so signup stays a single atomic call."""
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    org.name = body.name
    if org.slug.startswith(_slugify(org.name)) is False:
        org.slug = _unique_slug(db, body.name)
    db.commit()
    db.refresh(org)
    return org

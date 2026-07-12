"""Org/user auth endpoints. FastAPI is the single identity provider for the whole
platform (web portals + mobile) — there is no separate NestJS auth service.
"""

import logging
import re
import uuid
from typing import Optional

import jwt
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import get_db
from app.ratelimit import limiter
from app.models import Organization, User
from app.schemas_auth import (
    ChangePasswordRequest,
    LoginRequest,
    OrgProfileRequest,
    OrgProfileResponse,
    RegisterRequest,
    TokenResponse,
    UserResponse,
)
from app.utils.security import create_access_token, decode_access_token, hash_password, verify_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

_bearer = HTTPBearer(auto_error=False)

# Fixed bcrypt hash used only to equalise login timing: on the "no such account"
# path we still run one bcrypt verify against this so the response takes the same
# ~time as the wrong-password path. Without it, the missing-account path returns
# much faster (no bcrypt), letting an attacker enumerate registered emails by
# timing despite the generic error message.
_DUMMY_PASSWORD_HASH = hash_password("account-enumeration-timing-guard")


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
        must_reset_password=user.must_reset_password,
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


def get_current_user_optional(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    db: Session = Depends(get_db),
) -> Optional[User]:
    """Same as get_current_user but returns None instead of 401 when no/invalid token.

    Needed on routes the Flutter app still calls anonymously (it doesn't send a bearer
    token yet — see [[leadpilot-supabase-flutter-plan]]). Lets those routes stamp
    org_id/telecaller_id when a caller *does* authenticate, without breaking the ones
    that don't yet.
    """
    if credentials is None:
        return None
    try:
        payload = decode_access_token(credentials.credentials)
    except jwt.PyJWTError:
        return None
    user = db.query(User).filter(User.id == payload.get("sub")).first()
    if user is None or not user.is_active:
        return None
    return user


def require_role(*roles: str):
    """Dependency factory: 403s unless the caller's role is one of `roles`.

    Lives here (not in team.py, where it was first written) because it's a
    general identity-module concern — the org-profile endpoints below need it
    too, and this avoids a circular import between auth.py and team.py.
    """

    def _check(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Insufficient role")
        return user

    return _check


@router.post("/register", response_model=TokenResponse, status_code=status.HTTP_201_CREATED)
@limiter.limit("10/hour")
async def register(request: Request, body: RegisterRequest, db: Session = Depends(get_db)):
    """Create a new org and its first user (role=founder). One call, not two, so the
    founder onboarding flow never ends up with an org and no owner."""
    email = body.email.lower()
    if db.query(User).filter(func.lower(User.email) == email).first() is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Email already registered")

    org = Organization(id=str(uuid.uuid4()), name=body.org_name, slug=_unique_slug(db, body.org_name))
    db.add(org)
    db.flush()  # assign org.id before the FK reference below

    user = User(
        id=str(uuid.uuid4()),
        org_id=org.id,
        email=email,
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
@limiter.limit("10/minute")
async def login(request: Request, body: LoginRequest, db: Session = Depends(get_db)):
    email = body.email.lower()
    user = db.query(User).filter(func.lower(User.email) == email).first()
    if user is None:
        # Spend the same bcrypt work as the wrong-password path so response timing
        # can't be used to tell whether an email is registered. Client still gets
        # the generic message; the server log distinguishes "no such account" from
        # "wrong password" so a failed login is debuggable without guessing.
        verify_password(body.password, _DUMMY_PASSWORD_HASH)
        logger.warning("login failed: no account for %s", email)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not verify_password(body.password, user.hashed_password):
        logger.warning("login failed: bad password for %s (user %s)", email, user.id)
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Incorrect email or password")
    if not user.is_active:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Account is disabled")

    token = create_access_token({"sub": user.id, "org_id": user.org_id, "role": user.role})
    return TokenResponse(access_token=token, user=_to_user_response(user))


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return _to_user_response(current_user)


@router.post("/change-password", response_model=UserResponse)
@limiter.limit("10/minute")
async def change_password(
    request: Request,
    body: ChangePasswordRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Self-service password change — for a founder/admin changing their own
    password by choice, and for a telecaller clearing must_reset_password after
    an invite/reset (their temp password is exactly what current_password is)."""
    if not verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Current password is incorrect")

    current_user.hashed_password = hash_password(body.new_password)
    current_user.must_reset_password = False
    db.commit()
    db.refresh(current_user)
    return _to_user_response(current_user)


@router.get("/org", response_model=OrgProfileResponse)
async def get_org_profile(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")
    return org


@router.patch("/org", response_model=OrgProfileResponse)
async def update_org_profile(
    body: OrgProfileRequest,
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    """Handles both the onboarding wizard's final step (sends everything at once
    — registration creates a placeholder org/name so signup stays a single
    atomic call) and later edits from the settings page (sends only what
    changed). Every field is the Organisation Knowledge Base every AI feature
    — scoring relevance, follow-up tone, script generation — is meant to read
    from; persisting it here is what makes that possible."""
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    if org is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Organization not found")

    updates = body.model_dump(exclude_unset=True)
    if "name" in updates and updates["name"]:
        org.name = updates.pop("name")
        if org.slug.startswith(_slugify(org.name)) is False:
            org.slug = _unique_slug(db, org.name)
    for field, value in updates.items():
        setattr(org, field, value)

    db.commit()
    db.refresh(org)
    return org

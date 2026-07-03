"""Password hashing and JWT issuing/verification for the platform auth module.

FastAPI is the sole identity provider for LeadPilot — the Next.js portals and Flutter
mobile app all authenticate against these endpoints, no separate NestJS auth service.
"""

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

import bcrypt
import jwt

from app.config import settings

# passlib (the more common choice here) is unmaintained and breaks against bcrypt>=4.1
# (raises ValueError during its own self-test). Using the `bcrypt` package directly
# sidesteps that entirely and is one less abstraction layer.


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return bcrypt.checkpw(plain_password.encode("utf-8"), hashed_password.encode("utf-8"))


def create_access_token(claims: Dict[str, Any], expires_minutes: Optional[int] = None) -> str:
    """Sign a JWT carrying `sub` (user id), `org_id`, and `role`.

    Flutter and the FastAPI-internal caller (none yet — single backend now) both
    verify with the same shared secret; there is no separate identity service to
    reconcile against.
    """
    expire = datetime.now(timezone.utc) + timedelta(
        minutes=expires_minutes or settings.jwt_expire_minutes
    )
    payload = {**claims, "exp": expire}
    return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str) -> Dict[str, Any]:
    """Raises jwt.PyJWTError (expired/invalid) — callers translate to HTTP 401."""
    return jwt.decode(token, settings.jwt_secret_key, algorithms=[settings.jwt_algorithm])

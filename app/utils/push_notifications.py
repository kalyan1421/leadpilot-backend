"""Push notifications to the telecaller app via Firebase Cloud Messaging.

Previously the only "notifications" a telecaller ever saw were local device
alarms they scheduled for themselves (see NotificationService in the Flutter
app) — nothing server-initiated (a new lead assigned to them, a founder
reopening/moving a lead they own, a password reset) ever reached the device.
This module is the send side of that gap; app/models.py's User.fcm_token is
where the device's registration token lands (see api/auth.py's
POST /fcm-token) and app/api/*.py call send_push_to_user at the moments a
telecaller should actually be told something happened.

Every call site treats this as fail-soft — a missing/invalid token, an
unconfigured service account, or a transient FCM error must never break the
request that triggered it (creating a lead, changing a stage, resetting a
password all have to succeed regardless of whether the push goes out).
"""

import json
import logging
import os
from typing import Any, Dict, Optional

import firebase_admin
from firebase_admin import credentials, messaging
from sqlalchemy.orm import Session

from app.config import settings
from app.models import User

logger = logging.getLogger(__name__)

_app: Optional[firebase_admin.App] = None
_init_attempted = False


def _get_app() -> Optional[firebase_admin.App]:
    """Lazily initializes the Firebase Admin app from whichever credential
    source is configured, at most once. Returns None (not raising) if
    nothing is configured or initialization fails — callers must tolerate
    push simply being unavailable, e.g. in tests or before a deploy sets the
    env var."""
    global _app, _init_attempted
    if _app is not None:
        return _app
    if _init_attempted:
        return None
    _init_attempted = True

    try:
        if settings.firebase_service_account_json:
            cred = credentials.Certificate(json.loads(settings.firebase_service_account_json))
        else:
            path = settings.firebase_service_account_file
            if not path or not os.path.exists(path):
                logger.info("Push notifications disabled: no Firebase service account configured")
                return None
            cred = credentials.Certificate(path)
        _app = firebase_admin.initialize_app(cred)
        return _app
    except Exception:
        logger.exception("Failed to initialize Firebase Admin SDK; push notifications disabled")
        return None


def send_push_to_user(
    db: Session,
    user_id: Optional[str],
    title: str,
    body: str,
    data: Optional[Dict[str, Any]] = None,
) -> bool:
    """Best-effort push to one user's registered device. No-ops quietly if
    the user has no token, Firebase isn't configured, or the send fails —
    see module docstring for why this can never raise into the caller."""
    if not user_id:
        return False
    app = _get_app()
    if app is None:
        return False

    try:
        user = db.query(User).filter(User.id == user_id).first()
        if user is None or not user.fcm_token:
            return False

        message = messaging.Message(
            notification=messaging.Notification(title=title, body=body),
            data={k: str(v) for k, v in (data or {}).items()},
            token=user.fcm_token,
        )
        messaging.send(message, app=app)
        return True
    except Exception:
        # Covers an expired/unregistered token, a transient FCM outage, or a
        # malformed payload — none of which should surface to the caller.
        logger.exception("Push notification failed for user_id=%s", user_id)
        return False

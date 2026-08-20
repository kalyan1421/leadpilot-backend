"""Small helpers for writing founder dashboard notifications in the caller's transaction."""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models import Notification


def add_notification(
    db: Session,
    *,
    org_id: str,
    notification_type: str,
    title: str,
    message: str,
    severity: str = "info",
    entity_type: Optional[str] = None,
    entity_id: Optional[str] = None,
    actor_name: Optional[str] = None,
) -> Notification:
    """Stage a notification for commit with the business event that caused it.

    Keeping this helper commit-free means an event and its notification are
    atomic: a failed lead/attendance update cannot leave a misleading alert.
    """
    row = Notification(
        id=str(uuid.uuid4()),
        org_id=org_id,
        type=notification_type,
        title=title,
        message=message,
        severity=severity,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_name=actor_name,
    )
    db.add(row)
    return row

"""Team membership management — list/invite/update org members.

There is no email-sending infra anywhere in this codebase yet, so invites create the
user directly with a generated temp password returned once in the response; the
founder shares it manually. Real email invites are a flagged follow-up, not built here.
"""

import logging
import secrets
import string
import uuid
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.auth import require_role
from app.database import get_db
from app.models import AudioCall, Lead, LeadAnalysis, User
from app.utils.lead_intelligence import averaged_debrief_dimensions
from app.schemas_team import (
    InviteMemberRequest,
    InviteMemberResponse,
    SetPasswordRequest,
    TeamMemberResponse,
    UpdateMemberRequest,
)
from app.utils.security import hash_password

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/team", tags=["team"])

VALID_ROLES = {"founder", "admin", "ad_manager", "telecaller"}


def _generate_temp_password() -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _member_metrics_batch(db: Session, user_ids: List[str]) -> Dict[str, Dict[str, object]]:
    """Batch-computes calls/leads/quality/last_active for many users in 4 queries
    total instead of 4 queries per user — list_team was doing that N+1 in a loop."""
    if not user_ids:
        return {}

    calls_by_user = dict(
        db.query(AudioCall.telecaller_id, func.count(AudioCall.call_id))
        .filter(AudioCall.telecaller_id.in_(user_ids))
        .group_by(AudioCall.telecaller_id)
        .all()
    )
    leads_by_user = dict(
        db.query(Lead.assigned_to, func.count(Lead.id))
        .filter(Lead.assigned_to.in_(user_ids))
        .group_by(Lead.assigned_to)
        .all()
    )
    last_active_by_user = dict(
        db.query(AudioCall.telecaller_id, func.max(AudioCall.timestamp))
        .filter(AudioCall.telecaller_id.in_(user_ids))
        .group_by(AudioCall.telecaller_id)
        .all()
    )

    debriefs_by_user: Dict[str, List[Dict[str, Any]]] = {}
    for telecaller_id, agent_debrief in (
        db.query(AudioCall.telecaller_id, LeadAnalysis.agent_debrief)
        .join(LeadAnalysis, LeadAnalysis.call_id == AudioCall.call_id)
        .filter(
            AudioCall.telecaller_id.in_(user_ids),
            LeadAnalysis.status == "completed",
            LeadAnalysis.agent_debrief.isnot(None),
        )
        .all()
    ):
        if isinstance(agent_debrief, dict):
            debriefs_by_user.setdefault(telecaller_id, []).append(agent_debrief)

    result = {}
    for uid in user_ids:
        # Same /110 composite the Performance and Comparison pages use
        # (5 skill dims * 20 + punctuality * 10), via the shared helper — this
        # page used to average the raw 0-100 agent_debrief.total_score, so the
        # same telecaller showed a different, lower quality number here. None
        # when there are no scored calls yet, so the UI shows "No calls yet"
        # rather than a misleading 0/110.
        debriefs = debriefs_by_user.get(uid, [])
        dims = averaged_debrief_dimensions(debriefs)
        result[uid] = {
            "calls": calls_by_user.get(uid, 0),
            "leads": leads_by_user.get(uid, 0),
            "quality": round(sum(dims.values())) if debriefs else None,
            "last_active": last_active_by_user.get(uid),
        }
    return result


def _to_member_response(user: User, metrics: Dict[str, object]) -> TeamMemberResponse:
    return TeamMemberResponse(
        id=user.id,
        name=user.name,
        email=user.email,
        phone=user.phone,
        role=user.role,
        status="Active" if user.is_active else "Inactive",
        calls=metrics["calls"],
        leads=metrics["leads"],
        quality=metrics["quality"],
        last_active=metrics["last_active"],
    )


@router.get("", response_model=List[TeamMemberResponse])
async def list_team(
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    members = db.query(User).filter(User.org_id == current_user.org_id).all()
    metrics_by_user = _member_metrics_batch(db, [m.id for m in members])
    return [_to_member_response(m, metrics_by_user[m.id]) for m in members]


@router.post("/invite", response_model=InviteMemberResponse, status_code=status.HTTP_201_CREATED)
async def invite_member(
    body: InviteMemberRequest,
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    """Telecallers log into the mobile app with this email + the returned
    temp_password (email/password, not phone/OTP — that's parked for later).
    They don't get a web account; phone is stored as contact info for now."""
    if body.role not in VALID_ROLES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role must be one of {sorted(VALID_ROLES)}")
    if body.role == "founder" and current_user.role != "founder":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a founder can grant the founder role")
    email = body.email.lower()
    existing_by_email = db.query(User).filter(func.lower(User.email) == email).first()
    if existing_by_email is not None:
        if existing_by_email.org_id == current_user.org_id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"{existing_by_email.name} is already on your team with this email",
            )
        raise HTTPException(status.HTTP_409_CONFLICT, detail="This email is already registered to another account")
    if body.phone:
        existing_by_phone = db.query(User).filter(User.phone == body.phone).first()
        if existing_by_phone is not None:
            if existing_by_phone.org_id == current_user.org_id:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    detail=f"{existing_by_phone.name} is already on your team with this phone number",
                )
            raise HTTPException(status.HTTP_409_CONFLICT, detail="This phone number is already registered to another account")

    temp_password = _generate_temp_password()
    user = User(
        id=str(uuid.uuid4()),
        org_id=current_user.org_id,
        email=email,
        phone=body.phone,
        hashed_password=hash_password(temp_password),
        name=body.name,
        role=body.role,
        must_reset_password=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # A brand-new invite has no calls/leads/scores yet — skip the metrics queries.
    empty_metrics = {"calls": 0, "leads": 0, "quality": None, "last_active": None}
    return InviteMemberResponse(member=_to_member_response(user, empty_metrics), temp_password=temp_password)


@router.patch("/{user_id}", response_model=TeamMemberResponse)
async def update_member(
    user_id: str,
    body: UpdateMemberRequest,
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id, User.org_id == current_user.org_id).first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team member not found")
    if user.role == "founder" and current_user.role != "founder" and (body.role is not None or body.is_active is not None):
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a founder can modify a founder's account")

    if body.role is not None:
        if body.role not in VALID_ROLES:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"role must be one of {sorted(VALID_ROLES)}")
        if body.role == "founder" and current_user.role != "founder":
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a founder can grant the founder role")
        user.role = body.role
    if body.is_active is not None:
        if user.id == current_user.id and body.is_active is False:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Cannot deactivate your own account")
        user.is_active = body.is_active

    db.commit()
    db.refresh(user)
    metrics = _member_metrics_batch(db, [user.id])[user.id]
    return _to_member_response(user, metrics)


@router.post("/{user_id}/reset-password", response_model=InviteMemberResponse)
async def reset_member_password(
    user_id: str,
    body: SetPasswordRequest = SetPasswordRequest(),
    current_user: User = Depends(require_role("founder", "admin")),
    db: Session = Depends(get_db),
):
    """Sets a team member's password when their temp password is lost, needs
    rotating, or the founder wants it to be something specific — same
    generation/response shape as invite_member, since the founder shares this
    new one manually the same way either way. body.new_password omitted/empty
    -> a random temp password is generated (the original behaviour); provided
    -> that exact value is used instead. Either way must_reset_password is set
    so the member still confirms it by logging in with it once."""
    user = db.query(User).filter(User.id == user_id, User.org_id == current_user.org_id).first()
    if user is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Team member not found")
    if user.role == "founder" and current_user.role != "founder":
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Only a founder can reset a founder's password")

    new_password = body.new_password or _generate_temp_password()
    user.hashed_password = hash_password(new_password)
    user.must_reset_password = True
    db.commit()
    db.refresh(user)

    metrics = _member_metrics_batch(db, [user.id])[user.id]
    return InviteMemberResponse(member=_to_member_response(user, metrics), temp_password=new_password)

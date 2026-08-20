"""Regression cover: telecallers used to have no way to hear about
server-side events at all — no push notification was ever sent for a new
lead assignment, a founder-driven stage change, or a password reset. These
tests verify each of those call sites actually invokes
send_push_to_user with the right target user, and that the opposite case
(a telecaller acting on their own lead) correctly does NOT push to
themselves. send_push_to_user itself talks to Firebase and is mocked here —
its own fail-soft/token-lookup behavior is push_notifications.py's own
concern, not this module's call sites.
"""

import uuid
from unittest.mock import patch

from app.models import User
from app.utils.security import create_access_token, hash_password


def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _add_telecaller(db_session, org_id, name="TC"):
    user = User(
        id=str(uuid.uuid4()),
        org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("Pass1234!"),
        name=name,
        role="telecaller",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.id, "org_id": org_id, "role": "telecaller"})
    return user, {"Authorization": f"Bearer {token}"}


def test_founder_creating_a_lead_pushes_the_auto_assigned_telecaller(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    telecaller, _ = _add_telecaller(db_session, org_id)

    with patch("app.api.calls.send_push_to_user") as mock_push:
        res = client.post(
            "/api/leads",
            json={"name": "Neha Gupta", "phone": "9000000001"},
            headers=founder_headers,
        )
    assert res.status_code == 201, res.text
    mock_push.assert_called_once()
    assert mock_push.call_args.args[1] == telecaller.id


def test_telecaller_creating_their_own_lead_does_not_push_to_themselves(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    _telecaller, tc_headers = _add_telecaller(db_session, org_id)

    with patch("app.api.calls.send_push_to_user") as mock_push:
        res = client.post(
            "/api/leads",
            json={"name": "Rahul Verma", "phone": "9000000002"},
            headers=tc_headers,
        )
    assert res.status_code == 201, res.text
    mock_push.assert_not_called()


def test_reassigning_a_lead_pushes_the_new_owner(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    tc_a, _ = _add_telecaller(db_session, org_id, "Priya")
    tc_b, _ = _add_telecaller(db_session, org_id, "Rakesh")

    created = client.post(
        "/api/leads",
        json={"name": "Sana Iyer", "phone": "9000000003"},
        headers=founder_headers,
    )
    lead_id = client.get("/api/leads/board", headers=founder_headers).json()["leads"][0]["id"]

    with patch("app.api.dashboard.send_push_to_user") as mock_push:
        res = client.patch(
            f"/api/leads/{lead_id}/details",
            json={"assigned_to": tc_b.id},
            headers=founder_headers,
        )
    assert res.status_code == 200, res.text
    mock_push.assert_called_once()
    assert mock_push.call_args.args[1] == tc_b.id


def test_founder_moving_a_leads_stage_pushes_its_owner(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    tc, _ = _add_telecaller(db_session, org_id)

    client.post("/api/leads", json={"name": "Vikram Singh", "phone": "9000000004"}, headers=founder_headers)
    lead_id = client.get("/api/leads/board", headers=founder_headers).json()["leads"][0]["id"]
    # Auto-assignment already put it on `tc` (the only telecaller in the org).

    with patch("app.api.dashboard.send_push_to_user") as mock_push:
        res = client.patch(
            f"/api/leads/{lead_id}/stage",
            json={"stage": "Assigned"},
            headers=founder_headers,
        )
    assert res.status_code == 200, res.text
    mock_push.assert_called_once()
    assert mock_push.call_args.args[1] == tc.id


def test_telecaller_moving_their_own_leads_stage_does_not_push(client, db_session):
    """update_lead_stage_by_contact (the mobile path) has no push call at all
    — a telecaller changing their own lead's stage shouldn't notify anyone."""
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    _tc, tc_headers = _add_telecaller(db_session, org_id)

    client.post("/api/leads", json={"name": "Meera Joshi", "phone": "9000000005"}, headers=tc_headers)
    contact_key = client.get("/api/leads/board", headers=founder_headers).json()["leads"][0]["phone"]

    with patch("app.api.dashboard.send_push_to_user") as mock_push:
        res = client.patch(
            f"/api/leads/by-contact/{contact_key}/stage",
            json={"stage": "Assigned"},
            headers=tc_headers,
        )
    assert res.status_code == 200, res.text
    mock_push.assert_not_called()


def test_resetting_a_members_password_pushes_them(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    tc, _ = _add_telecaller(db_session, org_id)

    with patch("app.api.team.send_push_to_user") as mock_push:
        res = client.post(f"/api/team/{tc.id}/reset-password", json={}, headers=founder_headers)
    assert res.status_code == 200, res.text
    mock_push.assert_called_once()
    assert mock_push.call_args.args[1] == tc.id


def test_deactivating_a_member_pushes_them_but_reactivating_does_not_double_push(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    founder_headers = {"Authorization": f"Bearer {founder['access_token']}"}
    tc, _ = _add_telecaller(db_session, org_id)

    with patch("app.api.team.send_push_to_user") as mock_push:
        res = client.patch(f"/api/team/{tc.id}", json={"is_active": False}, headers=founder_headers)
    assert res.status_code == 200, res.text
    mock_push.assert_called_once()

    # Already inactive -> setting is_active=False again is a no-op, not a
    # fresh deactivation, so it must not push again.
    with patch("app.api.team.send_push_to_user") as mock_push_again:
        res2 = client.patch(f"/api/team/{tc.id}", json={"is_active": False}, headers=founder_headers)
    assert res2.status_code == 200, res2.text
    mock_push_again.assert_not_called()


def test_fcm_token_registration_persists_the_token(client, db_session):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    res = client.post("/api/auth/fcm-token", json={"token": "test-device-token-123"}, headers=headers)
    assert res.status_code == 204, res.text

    user = db_session.query(User).filter(User.id == founder["user"]["id"]).first()
    assert user.fcm_token == "test-device-token-123"

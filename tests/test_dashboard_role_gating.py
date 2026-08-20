"""Regression cover: several founder-web-only analytics/edit endpoints in
dashboard.py were gated by plain get_current_user (any valid token) instead
of require_role("founder", "admin") — a telecaller's own JWT could reach
org-wide lead/telecaller data, and PATCH /leads/{id}/details let a
telecaller reassign or edit any lead in the org with no ownership check and
no audit trail."""

import uuid

from app.models import User
from app.utils.security import create_access_token, hash_password


def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _make_telecaller(db_session, org_id):
    user = User(
        id=str(uuid.uuid4()), org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@example.com",
        hashed_password=hash_password("TelecallerPass123!"), name="TC", role="telecaller", is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.id, "org_id": org_id, "role": "telecaller"})
    return user, token


GATED_GET_ENDPOINTS = [
    "/api/leads/board",
    "/api/leads/quality",
    "/api/leads/score-distribution",
    "/api/leads/ageing",
    "/api/leads/wastage",
    "/api/leads/zombie",
    "/api/dashboard/snapshot",
    "/api/dashboard/activity",
]


def test_telecaller_gets_403_on_founder_only_analytics(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    _, tc_token = _make_telecaller(db_session, org_id)
    headers = {"Authorization": f"Bearer {tc_token}"}

    for path in GATED_GET_ENDPOINTS:
        res = client.get(path, headers=headers)
        assert res.status_code == 403, f"{path} should be founder/admin-only, got {res.status_code}: {res.text}"


def test_founder_still_reaches_the_same_endpoints(client, db_session):
    founder = _register_founder(client)
    token = founder["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    for path in GATED_GET_ENDPOINTS:
        res = client.get(path, headers=headers)
        assert res.status_code == 200, f"{path} should still work for a founder, got {res.status_code}: {res.text}"


def test_telecaller_cannot_reassign_or_edit_another_lead(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    token = founder["access_token"]
    _, tc_token = _make_telecaller(db_session, org_id)

    create = client.post(
        "/api/leads", json={"name": "Some Lead", "phone": "9876543210", "reason": "inbound", "source": "meta"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert create.status_code == 201, create.text
    lead_id = client.get("/api/leads/board", headers={"Authorization": f"Bearer {token}"}).json()["leads"][0]["id"]

    res = client.patch(
        f"/api/leads/{lead_id}/details",
        json={"deal_value": 999999},
        headers={"Authorization": f"Bearer {tc_token}"},
    )
    assert res.status_code == 403, res.text


def test_founder_editing_deal_value_still_works(client, db_session):
    founder = _register_founder(client)
    token = founder["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    client.post(
        "/api/leads", json={"name": "Some Lead", "phone": "9876543211", "reason": "inbound", "source": "meta"},
        headers=headers,
    )
    lead_id = client.get("/api/leads/board", headers=headers).json()["leads"][0]["id"]

    res = client.patch(f"/api/leads/{lead_id}/details", json={"deal_value": 50000}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.json()["deal_value"] == 50000

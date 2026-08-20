"""Regression cover: create_follow_up had no idempotency key — every POST
unconditionally inserted a fresh row. A client retry after a timeout where
the first request actually succeeded (network blip, app-level retry logic)
produced two follow-ups for the same lead/telecaller/due_at, directly
polluting the founder dashboard's missed-follow-up leakage metric this
module exists to feed. Fixed with a short (30s) dedupe window on
(org, telecaller, lead_id, due_at) — long enough to catch a retry, short
enough not to merge two follow-ups deliberately scheduled minutes apart."""

from datetime import datetime, timedelta, timezone


def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_a_retried_identical_request_does_not_duplicate(client):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    first = client.post("/api/follow-ups", headers=headers, json={"note": "Call back", "due_at": due})
    assert first.status_code == 201, first.text

    # Same request retried, as if the client never saw the first response.
    second = client.post("/api/follow-ups", headers=headers, json={"note": "Call back", "due_at": due})
    assert second.status_code == 201, second.text
    assert second.json()["id"] == first.json()["id"], "a retry must return the same row, not create a second one"

    listed = client.get("/api/follow-ups", headers=headers).json()["follow_ups"]
    assert len(listed) == 1


def test_two_deliberately_different_follow_ups_are_not_merged(client):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}
    due1 = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()
    due2 = (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()

    client.post("/api/follow-ups", headers=headers, json={"note": "Call back", "due_at": due1})
    client.post("/api/follow-ups", headers=headers, json={"note": "Call back", "due_at": due2})

    listed = client.get("/api/follow-ups", headers=headers).json()["follow_ups"]
    assert len(listed) == 2, "different due_at values must not be treated as a duplicate"


def test_two_telecallers_scheduling_the_same_lead_at_the_same_time_both_get_their_own(client, db_session):
    import uuid

    from app.models import User
    from app.utils.security import create_access_token, hash_password

    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]

    def _telecaller():
        u = User(
            id=str(uuid.uuid4()), org_id=org_id, email=f"{uuid.uuid4().hex[:8]}@example.com",
            hashed_password=hash_password("Pass1234!"), name="TC", role="telecaller", is_active=True,
        )
        db_session.add(u)
        db_session.commit()
        token = create_access_token({"sub": u.id, "org_id": org_id, "role": "telecaller"})
        return {"Authorization": f"Bearer {token}"}

    headers_a, headers_b = _telecaller(), _telecaller()
    due = (datetime.now(timezone.utc) + timedelta(days=1)).isoformat()

    res_a = client.post("/api/follow-ups", headers=headers_a, json={"note": "Call back", "due_at": due})
    res_b = client.post("/api/follow-ups", headers=headers_b, json={"note": "Call back", "due_at": due})
    assert res_a.status_code == 201 and res_b.status_code == 201
    assert res_a.json()["id"] != res_b.json()["id"], "the dedupe window must be per-telecaller, not global"

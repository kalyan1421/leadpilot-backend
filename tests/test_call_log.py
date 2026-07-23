"""Covers app/api/call_log.py: idempotent sync (re-syncing the same device
call never duplicates it) and direction/date-range filtering on the list."""

from datetime import datetime, timedelta, timezone


def _register_founder(client, email="founder@example.com", org_name="Acme"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": org_name, "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _auth_headers(client):
    token = _register_founder(client)["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _entry(device_call_id, phone="+919876543210", direction="outbound", duration=42, called_at=None):
    return {
        "device_call_id": device_call_id,
        "phone": phone,
        "direction": direction,
        "duration_seconds": duration,
        "called_at": (called_at or datetime.now(timezone.utc)).isoformat(),
    }


def test_sync_creates_new_entries(client):
    headers = _auth_headers(client)
    res = client.post(
        "/api/call-log/sync",
        headers=headers,
        json={"entries": [_entry("dev-1"), _entry("dev-2", direction="inbound")]},
    )
    assert res.status_code == 200, res.text
    assert res.json()["synced"] == 2

    listed = client.get("/api/call-log", headers=headers)
    assert listed.status_code == 200
    assert listed.json()["total"] == 2


def test_resync_same_device_call_id_upserts_not_duplicates(client):
    headers = _auth_headers(client)
    client.post("/api/call-log/sync", headers=headers, json={"entries": [_entry("dev-1", duration=10)]})
    # Re-sync the same device call, e.g. after the call finally connected.
    res = client.post(
        "/api/call-log/sync",
        headers=headers,
        json={"entries": [_entry("dev-1", duration=90)]},
    )
    assert res.status_code == 200
    listed = client.get("/api/call-log", headers=headers).json()
    assert listed["total"] == 1
    assert listed["calls"][0]["duration_seconds"] == 90


def test_list_filters_by_direction(client):
    headers = _auth_headers(client)
    client.post(
        "/api/call-log/sync",
        headers=headers,
        json={
            "entries": [
                _entry("dev-out", direction="outbound"),
                _entry("dev-in", direction="inbound"),
                _entry("dev-missed", direction="missed"),
            ]
        },
    )
    inbound = client.get("/api/call-log", headers=headers, params={"direction": "inbound"}).json()
    assert inbound["total"] == 1
    assert inbound["calls"][0]["direction"] == "inbound"


def test_list_filters_by_date_range(client):
    headers = _auth_headers(client)
    old = datetime.now(timezone.utc) - timedelta(days=10)
    recent = datetime.now(timezone.utc)
    client.post(
        "/api/call-log/sync",
        headers=headers,
        json={"entries": [_entry("dev-old", called_at=old), _entry("dev-new", called_at=recent)]},
    )
    start = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
    res = client.get("/api/call-log", headers=headers, params={"start_date": start}).json()
    assert res["total"] == 1


def test_call_log_is_scoped_to_own_telecaller_and_org(client):
    headers_a = _auth_headers(client)
    # Second, unrelated org/user.
    res = client.post(
        "/api/auth/register",
        json={
            "org_name": "OtherCo",
            "name": "Other Founder",
            "email": "other@example.com",
            "password": "OtherPass123!",
        },
    )
    headers_b = {"Authorization": f"Bearer {res.json()['access_token']}"}

    client.post("/api/call-log/sync", headers=headers_a, json={"entries": [_entry("dev-a")]})
    client.post("/api/call-log/sync", headers=headers_b, json={"entries": [_entry("dev-b")]})

    assert client.get("/api/call-log", headers=headers_a).json()["total"] == 1
    assert client.get("/api/call-log", headers=headers_b).json()["total"] == 1


def test_sync_resolves_lead_id_by_phone_match(client):
    headers = _auth_headers(client)
    lead_res = client.post(
        "/api/leads",
        headers=headers,
        json={"name": "Priya", "phone": "+919999999999", "source": "organic", "reason": "Interested"},
    )
    assert lead_res.status_code in (200, 201), lead_res.text

    client.post(
        "/api/call-log/sync",
        headers=headers,
        json={"entries": [_entry("dev-lead", phone="+919999999999")]},
    )
    listed = client.get("/api/call-log", headers=headers).json()
    assert listed["calls"][0]["lead_id"] is not None

"""Regression cover: create_lead used to key new leads by slugify_contact(name)
only, never checking phone. Two different display names for the same real
person (a typo, "Rakesh S." vs "Rakesh Sharma", a later name edit) slugify to
two different contact_keys — a manually re-entered lead for a phone that
already had one silently spawned a second, orphaned Lead/history split
instead of reusing the existing row, the exact class of bug
upload_recording's own stale-override phone fallback already guards against
on the call-upload side."""


def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_same_phone_different_name_reuses_the_existing_lead(client):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    first = client.post(
        "/api/leads",
        json={"name": "Rakesh Sharma", "phone": "9876543210", "reason": "inbound", "source": "meta"},
        headers=headers,
    )
    assert first.status_code == 201, first.text
    assert first.json()["created"] is True

    second = client.post(
        "/api/leads",
        # A typo'd/edited name for the SAME phone number.
        json={"name": "Rakesh S.", "phone": "9876543210", "reason": "inbound", "source": "meta"},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["created"] is False, "must reuse the existing lead, not spawn a second one"
    assert second.json()["contact_key"] == first.json()["contact_key"]

    board = client.get("/api/leads/board", headers=headers)
    assert len(board.json()["leads"]) == 1, "only one lead should exist for this phone number"


def test_different_phone_same_name_still_creates_a_second_lead(client):
    """Sanity check the fix isn't over-broad — two genuinely different
    people who happen to share a name must not be merged."""
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    client.post(
        "/api/leads",
        json={"name": "Amit Kumar", "phone": "9111111111", "reason": "inbound", "source": "meta"},
        headers=headers,
    )
    second = client.post(
        "/api/leads",
        json={"name": "Amit Kumar", "phone": "9222222222", "reason": "inbound", "source": "meta"},
        headers=headers,
    )
    assert second.status_code == 201, second.text
    assert second.json()["created"] is True

    board = client.get("/api/leads/board", headers=headers)
    assert len(board.json()["leads"]) == 2


def test_no_phone_still_falls_back_to_name_deduplication(client):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    first = client.post(
        "/api/leads", json={"name": "Priya Verma", "reason": "inbound", "source": "meta"}, headers=headers
    )
    assert first.status_code == 201

    second = client.post(
        "/api/leads", json={"name": "Priya Verma", "reason": "inbound", "source": "meta"}, headers=headers
    )
    assert second.status_code == 201
    assert second.json()["created"] is False

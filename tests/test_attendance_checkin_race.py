"""Regression cover: two concurrent check-ins (double-tap, retry-after-
timeout, two devices) can both read `record is None` for today before
either commits, then both try to INSERT a row for the same
(user_id, date) — the second commit hit uq_attendance_user_date and raised
an unhandled IntegrityError -> 500, instead of the 409 the mobile client's
"harmless race, just refresh" retry logic expects. Simulates the race by
making the *first* commit itself raise IntegrityError (equivalent to losing
a real race against a concurrent insert), since genuinely interleaving two
requests isn't reproducible through a single synchronous TestClient."""

from unittest.mock import patch

from sqlalchemy.exc import IntegrityError


def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def test_concurrent_checkin_race_returns_409_not_500(client, db_session):
    founder = _register_founder(client)
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    real_commit = db_session.commit
    call_count = 0

    def _commit_that_races_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IntegrityError("insert", {}, Exception("uq_attendance_user_date"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=_commit_that_races_once):
        res = client.post("/api/attendance/check-in", headers=headers)

    assert res.status_code == 409, res.text
    assert "already checked in" in res.json()["detail"].lower()

    # The session must still be usable afterward (rollback happened) —
    # a normal check-in right after should work cleanly.
    res2 = client.post("/api/attendance/check-in", headers=headers)
    assert res2.status_code in (200, 409), res2.text

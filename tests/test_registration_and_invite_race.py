"""Regression cover: register() and invite_member() both pre-check email/
phone uniqueness with a SELECT, then INSERT and commit — a TOCTOU race
where two concurrent requests for the same email/phone can both pass the
pre-check before either commits. The loser's IntegrityError used to
propagate as an unhandled 500 instead of the same 409 the pre-check
already returns for the non-racing case. Simulated the same way as the
attendance check-in race test: making the commit itself raise
IntegrityError, equivalent to losing a real race against a concurrent
insert."""

from unittest.mock import patch

from sqlalchemy.exc import IntegrityError


def _register_founder(client, email="founder@example.com"):
    return client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )


def test_register_race_returns_409_not_500(client, db_session):
    real_commit = db_session.commit
    call_count = 0

    def _commit_that_races_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IntegrityError("insert", {}, Exception("uq users_email"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=_commit_that_races_once):
        res = _register_founder(client, email="race@example.com")

    assert res.status_code == 409, res.text


def test_invite_race_returns_409_not_500(client, db_session):
    founder = _register_founder(client).json()
    headers = {"Authorization": f"Bearer {founder['access_token']}"}

    real_commit = db_session.commit
    call_count = 0

    def _commit_that_races_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise IntegrityError("insert", {}, Exception("uq users_email"))
        return real_commit()

    with patch.object(db_session, "commit", side_effect=_commit_that_races_once):
        res = client.post(
            "/api/team/invite",
            headers=headers,
            json={"email": "newtc@example.com", "name": "TC", "role": "telecaller"},
        )

    assert res.status_code == 409, res.text

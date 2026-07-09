"""Covers the password reset/change flow added across app/api/auth.py and
app/api/team.py: register vs. invite must_reset_password defaults, the
self-service change-password endpoint, the founder/admin reset-password
endpoint, role gating, and case-insensitive email login."""


def _register_founder(client, email="founder@example.com", org_name="Acme"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": org_name, "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _invite(client, token, email="telecaller@example.com", role="telecaller"):
    res = client.post(
        "/api/team/invite",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": email, "name": "Telecaller", "role": role},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _login(client, email, password):
    return client.post("/api/auth/login", json={"email": email, "password": password})


def test_register_sets_must_reset_password_false(client):
    body = _register_founder(client)
    assert body["user"]["must_reset_password"] is False


def test_invite_creates_user_requiring_password_reset(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    assert invite["temp_password"]

    login = _login(client, "telecaller@example.com", invite["temp_password"])
    assert login.status_code == 200
    assert login.json()["user"]["must_reset_password"] is True


def test_change_password_wrong_current_password_is_rejected(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    token = _login(client, "telecaller@example.com", invite["temp_password"]).json()["access_token"]

    res = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": "definitely-wrong", "new_password": "BrandNewPass456!"},
    )
    assert res.status_code == 401


def test_change_password_success_rotates_credential_and_clears_flag(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    temp_password = invite["temp_password"]
    token = _login(client, "telecaller@example.com", temp_password).json()["access_token"]

    res = client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": temp_password, "new_password": "BrandNewPass456!"},
    )
    assert res.status_code == 200
    assert res.json()["must_reset_password"] is False

    # Old temp password no longer works.
    assert _login(client, "telecaller@example.com", temp_password).status_code == 401
    # New password does.
    relogin = _login(client, "telecaller@example.com", "BrandNewPass456!")
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_reset_password"] is False


def test_reset_password_requires_founder_or_admin_role(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    telecaller_token = _login(client, "telecaller@example.com", invite["temp_password"]).json()["access_token"]

    res = client.post(
        f"/api/team/{invite['member']['id']}/reset-password",
        headers={"Authorization": f"Bearer {telecaller_token}"},
    )
    assert res.status_code == 403


def test_reset_password_regenerates_temp_password_and_forces_reset_again(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    old_temp_password = invite["temp_password"]
    user_id = invite["member"]["id"]

    # Telecaller changes their own password first, clearing the flag.
    token = _login(client, "telecaller@example.com", old_temp_password).json()["access_token"]
    client.post(
        "/api/auth/change-password",
        headers={"Authorization": f"Bearer {token}"},
        json={"current_password": old_temp_password, "new_password": "BrandNewPass456!"},
    )

    # Founder resets it — a fresh temp password comes back, and must_reset_password
    # flips back to true, and the just-set password stops working.
    reset = client.post(
        f"/api/team/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {founder['access_token']}"},
    )
    assert reset.status_code == 200
    new_temp_password = reset.json()["temp_password"]
    assert new_temp_password != old_temp_password

    assert _login(client, "telecaller@example.com", "BrandNewPass456!").status_code == 401
    relogin = _login(client, "telecaller@example.com", new_temp_password)
    assert relogin.status_code == 200
    assert relogin.json()["user"]["must_reset_password"] is True


def test_reset_password_with_explicit_value_uses_founders_chosen_password(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    user_id = invite["member"]["id"]

    res = client.post(
        f"/api/team/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {founder['access_token']}"},
        json={"new_password": "FoundersChoice789!"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["temp_password"] == "FoundersChoice789!"

    login = _login(client, "telecaller@example.com", "FoundersChoice789!")
    assert login.status_code == 200
    assert login.json()["user"]["must_reset_password"] is True

    # Old temp password from the invite no longer works.
    assert _login(client, "telecaller@example.com", invite["temp_password"]).status_code == 401


def test_reset_password_rejects_a_too_short_explicit_password(client):
    founder = _register_founder(client)
    invite = _invite(client, founder["access_token"])
    user_id = invite["member"]["id"]

    res = client.post(
        f"/api/team/{user_id}/reset-password",
        headers={"Authorization": f"Bearer {founder['access_token']}"},
        json={"new_password": "short"},
    )
    assert res.status_code == 422


def test_login_email_is_case_insensitive(client):
    _register_founder(client, email="Mixed.Case@Example.com")
    res = _login(client, "mixed.case@EXAMPLE.com", "FounderPass123!")
    assert res.status_code == 200


def test_register_duplicate_email_case_insensitive_is_rejected(client):
    _register_founder(client, email="dupe@example.com")
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Other Org", "name": "Someone Else", "email": "DUPE@example.com", "password": "AnotherPass123!"},
    )
    assert res.status_code == 409

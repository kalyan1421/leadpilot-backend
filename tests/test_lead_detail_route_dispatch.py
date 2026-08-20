"""Regression cover for a live routing bug: dashboard.py's founder-web
GET /api/leads/{lead_id} and calls.py's mobile GET /api/leads/{contact_key}
used to be two INDEPENDENT routes at the identical path shape
(`/api/leads/{single-segment}`). FastAPI/Starlette resolves an overlapping
path shape by registration order across the whole app, not path
specificity — since dashboard_router was included before calls.py's
intel_router, the founder-web handler silently shadowed the mobile one in
production. That broke the mobile app's entire Lead Detail screen AND
`/api/leads/dedupe` (also swallowed by the same wildcard), confirmed live via
TestClient before the fix. Fixed by merging into one dispatching handler
(dashboard.py's get_lead_detail), keyed on the caller's role, plus
reordering router inclusion so intel_router's remaining STATIC routes
(`/leads/dedupe`, `POST /leads`) aren't shadowed either.
"""

import uuid

from app.models import AudioCall, LeadAnalysis, User
from app.utils.security import create_access_token, hash_password


def _register_founder(client, email="founder@example.com", org_name="Acme"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": org_name, "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _make_telecaller(db_session, org_id, name="Priya TC"):
    user = User(
        id=str(uuid.uuid4()),
        org_id=org_id,
        email=f"{uuid.uuid4().hex[:8]}@example.com",
        phone=None,
        hashed_password=hash_password("TelecallerPass123!"),
        name=name,
        role="telecaller",
        is_active=True,
    )
    db_session.add(user)
    db_session.commit()
    token = create_access_token({"sub": user.id, "org_id": org_id, "role": "telecaller"})
    return user, token


def test_dedupe_endpoint_is_not_shadowed_by_the_lead_detail_wildcard(client):
    founder = _register_founder(client)
    token = founder["access_token"]

    res = client.get(
        "/api/leads/dedupe", params={"phone": "9876543210"}, headers={"Authorization": f"Bearer {token}"}
    )
    assert res.status_code == 200, res.text
    assert res.json() == {"duplicate": False}


def test_founder_gets_the_touchpoints_shaped_response(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    token = founder["access_token"]

    call_id = f"call_priya_{uuid.uuid4().hex[:8]}"
    db_session.add(AudioCall(
        call_id=call_id, org_id=org_id,
        transcript={"turns": [{"role": "AGENT", "content": "hi", "timestamp": "0:01"}]},
        audio_file_url="local://x.mp3",
    ))
    db_session.add(LeadAnalysis(
        id=str(uuid.uuid4()), call_id=call_id, org_id=org_id, status="completed",
        bant_score=60, lead_verdict="Warm",
        agent_debrief={"total_score": 70, "opening_score": 15, "discovery_score": 15,
                       "pitch_score": 15, "objection_handling_score": 15,
                       "closing_score": 5, "punctuality_score": 5},
    ))
    db_session.commit()

    res = client.get("/api/leads/priya", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "touchpoints" in body, "founder must get dashboard.py's shape, not the mobile one"
    assert "calls" not in body
    assert len(body["touchpoints"]) == 1


def test_telecaller_gets_the_mobile_calls_shaped_response(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    _, tc_token = _make_telecaller(db_session, org_id)

    call_id = f"call_amit_{uuid.uuid4().hex[:8]}"
    db_session.add(AudioCall(
        call_id=call_id, org_id=org_id,
        transcript={"turns": [{"role": "AGENT", "content": "hi", "timestamp": "0:01"}]},
        audio_file_url="local://x.mp3",
    ))
    db_session.add(LeadAnalysis(
        id=str(uuid.uuid4()), call_id=call_id, org_id=org_id, status="completed",
        bant_score=60, lead_verdict="Warm",
        agent_debrief={"total_score": 70, "opening_score": 15, "discovery_score": 15,
                       "pitch_score": 15, "objection_handling_score": 15,
                       "closing_score": 5, "punctuality_score": 5},
    ))
    db_session.commit()

    res = client.get("/api/leads/amit", headers={"Authorization": f"Bearer {tc_token}"})
    assert res.status_code == 200, res.text
    body = res.json()
    assert "calls" in body, "a telecaller must get the mobile app's shape, not the founder-web one"
    assert "touchpoints" not in body
    assert len(body["calls"]) == 1


def test_leads_board_and_leads_quality_are_unaffected_by_the_reordered_routers(client):
    """Guards against reintroducing the exact collision this fix resolves,
    just shifted onto a different pair of routes."""
    founder = _register_founder(client)
    token = founder["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    board = client.get("/api/leads/board", headers=headers)
    assert board.status_code == 200, board.text
    assert "leads" in board.json()

    quality = client.get("/api/leads/quality", headers=headers)
    assert quality.status_code == 200, quality.text

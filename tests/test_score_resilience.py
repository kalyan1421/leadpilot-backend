"""The Score tab must always render — even when automatic analysis FAILED.

Regression cover for the pipeline-hardening change: a failed LeadAnalysis now
persists a fully-shaped, all-zero agent_debrief (see empty_analysis /
_process_uploaded_recording), and GET /api/calls/{id}/score returns 200 with all
6 dimensions (greyed at 0) plus analysis_status='failed' + an error, instead of
404-ing on a blank tab. This is what guarantees "the score of a recording shows
for all 5 things" no matter how the pipeline half-failed.
"""

import uuid

from app.models import AudioCall, LeadAnalysis
from app.utils.lead_analyzer import empty_analysis


def _register_founder(client, email="founder@example.com", org_name="Acme"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": org_name, "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _seed_failed_call(db_session, org_id):
    call_id = f"call_9876543210_{uuid.uuid4().hex[:8]}"
    call = AudioCall(
        call_id=call_id,
        org_id=org_id,
        transcript={"turns": [{"role": "AGENT", "content": "Hello", "timestamp": "0:01"}]},
        audio_file_url="local://x.mp3",
    )
    db_session.add(call)

    analysis = empty_analysis("Automatic analysis failed — tap retry to re-run")
    la = LeadAnalysis(
        id=str(uuid.uuid4()),
        call_id=call_id,
        org_id=org_id,
        status="failed",
        error="Automatic analysis failed",
        bant_score=analysis["bant_score"],
        agent_debrief=analysis["agent_debrief"],
        lead_verdict=analysis["lead_verdict"],
        sentiment_arc=analysis["sentiment_arc"],
    )
    db_session.add(la)
    db_session.commit()
    return call_id


def test_score_renders_all_dimensions_for_a_failed_analysis(client, db_session):
    founder = _register_founder(client)
    token = founder["access_token"]
    org_id = founder["user"]["org_id"]

    call_id = _seed_failed_call(db_session, org_id)

    res = client.get(
        f"/api/calls/{call_id}/score",
        headers={"Authorization": f"Bearer {token}"},
    )
    # Previously this 404'd (status not in completed/not_relevant) → blank Score tab.
    assert res.status_code == 200, res.text
    body = res.json()

    assert body["analysis_status"] == "failed"
    assert body["analysis_error"]

    keys = {row["key"] for row in body["breakdown"]}
    assert keys == {"opening", "discovery", "pitch", "objection_handling", "closing", "punctuality"}, keys
    # All greyed at 0, but present — the tab renders instead of erroring.
    assert all(row["score"] == 0 for row in body["breakdown"])
    assert body["script_compliance"] and len(body["script_compliance"]) == 5


def _seed_completed_call(db_session, org_id, telecaller_id, contact, total_score):
    call_id = f"call_{contact}_{uuid.uuid4().hex[:8]}"
    db_session.add(AudioCall(
        call_id=call_id, org_id=org_id, telecaller_id=telecaller_id,
        transcript={"turns": [{"role": "AGENT", "content": "hi", "timestamp": "0:01"}]},
        audio_file_url="local://x.mp3",
    ))
    db_session.add(LeadAnalysis(
        id=str(uuid.uuid4()), call_id=call_id, org_id=org_id, status="completed",
        bant_score=60, lead_verdict="Warm",
        agent_debrief={"total_score": total_score,
                       "opening_score": 15, "discovery_score": 15, "pitch_score": 15,
                       "objection_handling_score": 15, "closing_score": total_score - 60,
                       "punctuality_score": 5},
    ))
    db_session.commit()


def test_telecaller_score_defaults_to_own_calls_only(client, db_session):
    """A telecaller's Score tab shows only their own calls (scope=me default),
    not the whole org's — regression for the org-wide-aggregate gap."""
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    ftoken = founder["access_token"]

    # Two telecallers in the org.
    inv_a = client.post("/api/team/invite", headers={"Authorization": f"Bearer {ftoken}"},
                        json={"email": "a@ex.com", "name": "Ava", "role": "telecaller"}).json()
    inv_b = client.post("/api/team/invite", headers={"Authorization": f"Bearer {ftoken}"},
                        json={"email": "b@ex.com", "name": "Ben", "role": "telecaller"}).json()
    tok_a = client.post("/api/auth/login",
                        json={"email": "a@ex.com", "password": inv_a["temp_password"]}).json()["access_token"]
    id_a, id_b = inv_a["member"]["id"], inv_b["member"]["id"]

    # Ava has one strong call; Ben has one weak call.
    _seed_completed_call(db_session, org_id, id_a, "ava_lead", total_score=95)
    _seed_completed_call(db_session, org_id, id_b, "ben_lead", total_score=40)

    me = client.get("/api/telecaller/score",
                    headers={"Authorization": f"Bearer {tok_a}"}).json()
    team = client.get("/api/telecaller/score?scope=team",
                      headers={"Authorization": f"Bearer {tok_a}"}).json()

    # "me" reflects only Ava's call count; "team" sees both telecallers' calls.
    assert me["calls"] == 1, me
    assert team["calls"] == 2, team


def test_score_still_404s_when_no_analysis_row_exists(client, db_session):
    """Guard: a call with NO analysis row at all still 404s (nothing to show)."""
    founder = _register_founder(client)
    token = founder["access_token"]
    org_id = founder["user"]["org_id"]

    call = AudioCall(
        call_id="call_nobody_deadbeef",
        org_id=org_id,
        transcript={"turns": []},
        audio_file_url="local://y.mp3",
    )
    db_session.add(call)
    db_session.commit()

    res = client.get(
        "/api/calls/call_nobody_deadbeef/score",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert res.status_code == 404

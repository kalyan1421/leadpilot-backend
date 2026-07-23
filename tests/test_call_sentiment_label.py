"""Covers call_sentiment_label() — the real per-call positive/neutral/negative
classification that replaced the mobile app's "Positive Calls" stat's old
score-proxy (call score >= 60)."""

import uuid

from app.models import AudioCall, LeadAnalysis
from app.utils.lead_intelligence import call_sentiment_label, sentiment_score


def _arc(*scores, role="USER"):
    return [{"role": role, "score": s} for s in scores]


def test_no_sentiment_signal_returns_none():
    assert call_sentiment_label([]) is None
    assert call_sentiment_label(None) is None


def test_clearly_positive_average_labeled_positive():
    assert call_sentiment_label(_arc(0.4, 0.6, 0.5)) == "positive"


def test_clearly_negative_average_labeled_negative():
    assert call_sentiment_label(_arc(-0.5, -0.6, -0.4)) == "negative"


def test_middling_average_labeled_neutral():
    assert call_sentiment_label(_arc(0.05, -0.05, 0.02)) == "neutral"


def test_boundary_values_are_neutral_not_positive_or_negative():
    # Strictly > 0.1 / < -0.1 required — exactly at the boundary is neutral.
    assert call_sentiment_label(_arc(0.1)) == "neutral"
    assert call_sentiment_label(_arc(-0.1)) == "neutral"


def test_prefers_prospect_turns_over_agent_turns():
    arc = [
        {"role": "AGENT", "score": -0.9},
        {"role": "USER", "score": 0.5},
        {"role": "USER", "score": 0.5},
    ]
    assert call_sentiment_label(arc) == "positive"


def test_falls_back_to_all_turns_when_no_role_present():
    arc = [{"score": 0.5}, {"score": 0.5}]
    assert call_sentiment_label(arc) == "positive"


def test_label_and_ring_agree_on_which_turns_they_average():
    # Same underlying average (_avg_sentiment) feeds both — a clearly positive
    # arc should be both a high ring value and "positive".
    arc = _arc(0.6, 0.7, 0.5)
    assert sentiment_score(arc) > 60
    assert call_sentiment_label(arc) == "positive"


# ── Over HTTP: GET /api/leads/{contact_key} surfaces the real label ────────

def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _seed_call_with_sentiment(db_session, org_id, contact, sentiment_arc):
    call_id = f"call_{contact}_{uuid.uuid4().hex[:8]}"
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
        sentiment_arc=sentiment_arc,
    ))
    db_session.commit()


def test_lead_detail_call_history_includes_real_sentiment_label(client, db_session):
    founder = _register_founder(client)
    org_id = founder["user"]["org_id"]
    token = founder["access_token"]

    _seed_call_with_sentiment(
        db_session, org_id, "priya",
        [{"role": "USER", "score": 0.6}, {"role": "USER", "score": 0.5}],
    )

    res = client.get("/api/leads/priya", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    calls = res.json()["calls"]
    assert len(calls) == 1
    assert calls[0]["sentiment"] == "positive"


def test_lead_detail_call_with_no_sentiment_data_is_none_not_neutral(client, db_session):
    founder = _register_founder(client, "founder2@example.com")
    org_id = founder["user"]["org_id"]
    token = founder["access_token"]

    _seed_call_with_sentiment(db_session, org_id, "amit", [])

    res = client.get("/api/leads/amit", headers={"Authorization": f"Bearer {token}"})
    assert res.status_code == 200, res.text
    assert res.json()["calls"][0]["sentiment"] is None

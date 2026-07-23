"""Unit tests for the 2026-07-15 fixes:
  - forward-only pipeline enforcement in dashboard._apply_stage_update
  - lead_card now emitting last_call_at + created_at for the mobile inbox tile.

Plus the 2026-07-24 change: a backward move is no longer flatly rejected — it
requires a non-empty `note` instead (see dashboard._apply_stage_update).

Both targets are pure enough to test without a DB/auth round-trip: the stage
helper only reads/writes attributes on a detached Lead instance, and lead_card
is a plain function.
"""

from datetime import datetime, timezone

import pytest
from fastapi import HTTPException

from app.api.dashboard import _apply_stage_update
from app.models import Lead
from app.utils.lead_intelligence import lead_card


def _lead(stage: str) -> Lead:
    return Lead(id="1", contact_key="k", pipeline_stage=stage)


# ── Forward-only pipeline ───────────────────────────────────────────────────

def test_stage_forward_move_allowed():
    lead = _lead("New")
    _apply_stage_update(lead, {"stage": "Assigned"})
    assert lead.pipeline_stage == "Assigned"


def test_stage_backward_move_without_note_rejected_400():
    lead = _lead("Assigned")
    with pytest.raises(HTTPException) as exc:
        _apply_stage_update(lead, {"stage": "New"})
    assert exc.value.status_code == 400
    # And the lead must not have been mutated.
    assert lead.pipeline_stage == "Assigned"


def test_stage_backward_move_with_blank_note_rejected_400():
    lead = _lead("Assigned")
    with pytest.raises(HTTPException) as exc:
        _apply_stage_update(lead, {"stage": "New", "note": "   "})
    assert exc.value.status_code == 400
    assert lead.pipeline_stage == "Assigned"


def test_stage_backward_move_with_note_allowed():
    lead = _lead("Negotiation")
    _apply_stage_update(lead, {"stage": "Contacted", "note": "Telecaller jumped the gun — client isn't ready yet"})
    assert lead.pipeline_stage == "Contacted"


def test_stage_reopen_closed_lost_with_note_allowed():
    lead = _lead("Closed Lost")
    _apply_stage_update(lead, {"stage": "Negotiation", "note": "Client called back, still interested"})
    assert lead.pipeline_stage == "Negotiation"


def test_stage_same_stage_is_allowed_noop():
    lead = _lead("Contacted")
    _apply_stage_update(lead, {"stage": "Contacted"})
    assert lead.pipeline_stage == "Contacted"


def test_stage_terminal_from_active_allowed():
    lead = _lead("Negotiation")
    _apply_stage_update(lead, {"stage": "Closed Lost"})
    assert lead.pipeline_stage == "Closed Lost"


def test_unknown_stage_rejected_422():
    lead = _lead("New")
    with pytest.raises(HTTPException) as exc:
        _apply_stage_update(lead, {"stage": "Bogus"})
    assert exc.value.status_code == 422


def test_closed_won_sets_deal_value_and_closed_at():
    lead = _lead("Negotiation")
    _apply_stage_update(lead, {"stage": "Closed Won", "deal_value": 85000})
    assert lead.pipeline_stage == "Closed Won"
    assert lead.deal_value == 85000
    assert lead.closed_at is not None


# ── Stage-change history (over HTTP — needs the endpoint, not just the pure
# helper, since logging happens in the two PATCH handlers) ──────────────────

def _register_founder(client, email="founder@example.com"):
    res = client.post(
        "/api/auth/register",
        json={"org_name": "Acme", "name": "Founder", "email": email, "password": "FounderPass123!"},
    )
    assert res.status_code == 201, res.text
    return res.json()


def _create_lead(client, headers, phone):
    res = client.post(
        "/api/leads",
        headers=headers,
        json={"name": "Priya", "phone": phone, "source": "organic", "reason": "Interested"},
    )
    assert res.status_code == 201, res.text
    return res.json()["contact_key"]


def test_backward_stage_move_persists_note_in_history(client, db_session):
    from app.models import LeadStageChange

    headers = {"Authorization": f"Bearer {_register_founder(client)['access_token']}"}
    contact_key = _create_lead(client, headers, "+919999999901")

    assert client.patch(
        f"/api/leads/by-contact/{contact_key}/stage", headers=headers, json={"stage": "Assigned"},
    ).status_code == 200

    res = client.patch(
        f"/api/leads/by-contact/{contact_key}/stage",
        headers=headers,
        json={"stage": "New", "note": "Wrong lead — reassigning"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pipeline_stage"] == "New"

    changes = db_session.query(LeadStageChange).order_by(LeadStageChange.created_at).all()
    assert len(changes) == 2
    assert (changes[0].from_stage, changes[0].to_stage) == ("New", "Assigned")
    assert (changes[1].from_stage, changes[1].to_stage) == ("Assigned", "New")
    assert changes[1].note == "Wrong lead — reassigning"


def test_backward_stage_move_without_note_rejected_over_http(client):
    headers = {"Authorization": f"Bearer {_register_founder(client, 'founder2@example.com')['access_token']}"}
    contact_key = _create_lead(client, headers, "+919999999902")

    client.patch(f"/api/leads/by-contact/{contact_key}/stage", headers=headers, json={"stage": "Assigned"})
    res = client.patch(
        f"/api/leads/by-contact/{contact_key}/stage", headers=headers, json={"stage": "New"},
    )
    assert res.status_code == 400


def test_reopening_closed_lost_lead_with_note_allowed_over_http(client):
    headers = {"Authorization": f"Bearer {_register_founder(client, 'founder3@example.com')['access_token']}"}
    contact_key = _create_lead(client, headers, "+919999999903")

    client.patch(
        f"/api/leads/by-contact/{contact_key}/stage", headers=headers, json={"stage": "Closed Lost"},
    )
    res = client.patch(
        f"/api/leads/by-contact/{contact_key}/stage",
        headers=headers,
        json={"stage": "Negotiation", "note": "Client called back, still interested"},
    )
    assert res.status_code == 200, res.text
    assert res.json()["pipeline_stage"] == "Negotiation"


# ── Inbox card timestamps ───────────────────────────────────────────────────

def test_lead_card_emits_created_at_and_latest_call_time():
    created = datetime(2026, 7, 10, tzinfo=timezone.utc)
    analyses = [
        {"timestamp": "2026-07-12T09:00:00+00:00", "lead_verdict": "warm"},
        {"timestamp": "2026-07-14T15:30:00+00:00", "lead_verdict": "hot"},
    ]
    card = lead_card("priya", analyses, name="Priya", created_at=created)
    assert card["created_at"] == created.isoformat()
    # last_call_at is the most recent call, not the first.
    assert card["last_call_at"] == datetime(
        2026, 7, 14, 15, 30, tzinfo=timezone.utc
    ).isoformat()


def test_lead_card_never_called_lead_has_created_at_but_no_last_call():
    created = datetime(2026, 7, 10, tzinfo=timezone.utc)
    card = lead_card("x", [], created_at=created)
    assert card["last_call_at"] is None
    assert card["created_at"] == created.isoformat()


def test_lead_card_without_created_at_is_still_valid():
    card = lead_card("x", [])
    assert card["created_at"] is None
    assert card["last_call_at"] is None

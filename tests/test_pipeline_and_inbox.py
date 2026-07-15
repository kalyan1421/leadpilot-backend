"""Unit tests for the 2026-07-15 fixes:
  - forward-only pipeline enforcement in dashboard._apply_stage_update
  - lead_card now emitting last_call_at + created_at for the mobile inbox tile.

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


def test_stage_backward_move_rejected_409():
    lead = _lead("Assigned")
    with pytest.raises(HTTPException) as exc:
        _apply_stage_update(lead, {"stage": "New"})
    assert exc.value.status_code == 409
    # And the lead must not have been mutated.
    assert lead.pipeline_stage == "Assigned"


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

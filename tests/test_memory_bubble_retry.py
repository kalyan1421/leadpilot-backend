"""Regression cover: MemoryBubbleBuilder.build() used to have no retry
(unlike lead_analyzer.py/precall_brief.py) and returned a minimal-but-truthy
"empty bubble" on any failure — since every caller in app/api/calls.py
guards on `if not bubble: return None` before writing to the DB, that
truthy empty dict sailed past the guard and overwrote a contact's real,
multi-call memory with "no history yet" on a single transient Sarvam error.
Fixed to retry like the other two AI-calling modules, and to return None
(not an empty bubble) so callers correctly skip the write on failure."""

from unittest.mock import patch

import app.utils.memory_bubble as memory_bubble


def _calls(n=1):
    return [
        {"call_id": f"c{i}", "timestamp": "2026-01-01T00:00:00Z", "analysis": {"lead_verdict": "Warm", "bant_score": 60}}
        for i in range(n)
    ]


def test_no_calls_returns_none_not_an_empty_bubble():
    builder = memory_bubble.MemoryBubbleBuilder()
    assert builder.build("priya", []) is None


def test_persistent_failure_retries_then_returns_none():
    call_count = 0

    def _always_fails(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        raise RuntimeError("provider down")

    builder = memory_bubble.MemoryBubbleBuilder()
    with patch.object(memory_bubble, "sarvam_extract", side_effect=_always_fails), \
         patch.object(memory_bubble.time, "sleep"):
        result = builder.build("priya", _calls(3))

    assert result is None
    assert call_count == memory_bubble._MAX_RETRIES + 1, "must retry before giving up, not fail on the first error"


def test_succeeds_after_a_transient_failure():
    call_count = 0
    good_response = {
        "facts": [{"category": "budget", "text": "80L confirmed"}],
        "running_verdict": "Hot",
        "headline": "Ready to close",
    }

    def _fails_once_then_succeeds(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("transient timeout")
        return dict(good_response)

    builder = memory_bubble.MemoryBubbleBuilder()
    with patch.object(memory_bubble, "sarvam_extract", side_effect=_fails_once_then_succeeds), \
         patch.object(memory_bubble.time, "sleep"):
        result = builder.build("priya", _calls(2))

    assert call_count == 2
    assert result is not None
    assert result["contact_key"] == "priya"
    assert result["headline"] == "Ready to close"


def test_a_falsy_but_non_exception_response_also_returns_none():
    """sarvam_extract can return None/empty without raising (e.g. the model
    declined to call the tool) — build() must not crash trying to treat that
    as a real bubble, and must not silently write an empty one either."""
    builder = memory_bubble.MemoryBubbleBuilder()
    with patch.object(memory_bubble, "sarvam_extract", return_value=None), \
         patch.object(memory_bubble.time, "sleep"):
        result = builder.build("priya", _calls(1))
    assert result is None

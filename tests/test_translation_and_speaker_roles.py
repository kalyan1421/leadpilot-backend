import json

from app.utils import sarvam
from app.utils.transcription import SarvamTranscriber
from app.utils.translation import translate_strings, translate_turns


def test_telugu_turns_use_dedicated_translation_and_preserve_roles(monkeypatch):
    calls = []

    def fake_translate(text, *, source_language_code, target_language_code, mode="modern-colloquial"):
        calls.append((text, source_language_code, target_language_code, mode))
        return {"నమస్కారం": "Hello", "అవును": "Yes"}[text]

    monkeypatch.setattr(sarvam, "sarvam_translate_text", fake_translate)
    turns = [
        {"role": "USER", "speaker_id": "speaker-a", "content": "నమస్కారం"},
        {"role": "AGENT", "speaker_id": "speaker-b", "content": "అవును"},
    ]

    translated = translate_turns(turns, "te", "en")

    assert [turn["content_translated"] for turn in translated] == ["Hello", "Yes"]
    assert [turn["role"] for turn in translated] == ["USER", "AGENT"]
    assert [turn["speaker_id"] for turn in translated] == ["speaker-a", "speaker-b"]
    assert all(call[1:3] == ("te-IN", "en-IN") for call in calls)


def test_translation_batch_leaves_existing_english_unchanged(monkeypatch):
    monkeypatch.setattr(
        sarvam,
        "sarvam_translate_text",
        lambda text, **_: "Please call tomorrow" if text == "రేపు కాల్ చేయండి" else "unexpected",
    )

    assert translate_strings(["రేపు కాల్ చేయండి", "Already English"]) == [
        "Please call tomorrow",
        "Already English",
    ]


def test_diarization_uses_semantic_role_not_first_speaker(monkeypatch, tmp_path):
    payload = {
        "language_code": "te-IN",
        "diarized_transcript": {
            "entries": [
                {
                    "speaker_id": "lead-voice",
                    "transcript": "హలో, ఎవరు మాట్లాడుతున్నారు?",
                    "start_time_seconds": 0,
                },
                {
                    "speaker_id": "agent-voice",
                    "transcript": "నేను ఆసన్ కంపెనీ నుండి మాట్లాడుతున్నాను.",
                    "start_time_seconds": 2,
                },
                {
                    "speaker_id": "lead-voice",
                    "transcript": "సరే, వివరాలు చెప్పండి.",
                    "start_time_seconds": 5,
                },
            ]
        },
    }
    (tmp_path / "result.json").write_text(json.dumps(payload), encoding="utf-8")

    def fake_extract(_messages, *, schema, **_kwargs):
        assert schema["properties"]["agent_speaker"]["enum"] == [
            "SPEAKER_1",
            "SPEAKER_2",
        ]
        return {"agent_speaker": "SPEAKER_2", "confidence": "high"}

    monkeypatch.setattr(sarvam, "sarvam_extract", fake_extract)

    transcript = sarvam._parse_diarized(str(tmp_path))

    assert [turn["role"] for turn in transcript["turns"]] == [
        "USER",
        "AGENT",
        "USER",
    ]
    assert transcript["language"] == "te"
    assert transcript["role_mapping"] == "semantic_v1"


def test_stored_transcript_role_repair_preserves_every_turn(monkeypatch):
    turns = [
        {"speaker_id": "lead", "role": "AGENT", "content": "హలో"},
        {"speaker_id": "rep", "role": "USER", "content": "ఆసన్ నుండి మాట్లాడుతున్నాను"},
        {"speaker_id": "lead", "role": "AGENT", "content": "చెప్పండి"},
    ]
    monkeypatch.setattr(sarvam, "_infer_agent_speaker", lambda _entries, _ids: "rep")

    repaired = sarvam.reclassify_transcript_roles(turns)

    assert [turn["role"] for turn in repaired] == ["USER", "AGENT", "USER"]
    assert [turn["content"] for turn in repaired] == [
        "హలో",
        "ఆసన్ నుండి మాట్లాడుతున్నాను",
        "చెప్పండి",
    ]


def test_canonical_transcript_is_always_original_language(monkeypatch):
    received = {}

    def fake_transcribe_file(_path, **kwargs):
        received.update(kwargs)
        return {"turns": [], "language": "te"}

    monkeypatch.setattr(sarvam, "transcribe_file", fake_transcribe_file)

    result = SarvamTranscriber().transcribe("call.m4a")

    assert result["language"] == "te"
    assert received["mode"] == "transcribe"
    assert received["language_code"] == "unknown"
    assert received["with_diarization"] is True

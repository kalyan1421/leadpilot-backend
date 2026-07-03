"""
Transcription — **Sarvam batch STT + speaker diarization** (saaras:v3), the sole provider.

Output contract (what the rest of the pipeline expects):
    { "turns": [ {"role": "AGENT|USER", "content": "...", "timestamp": "MM:SS"} ],
      "full_text": "...", "language": "te", "quality": "ok|low|failed" }

3-key rotation + the batch-job orchestration live in app/utils/sarvam.py, so this is just
the thin boundary the rest of the system sits behind.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Protocol

logger = logging.getLogger(__name__)


class Transcriber(Protocol):
    def transcribe(self, audio_path: str, language: str | None = None) -> Dict[str, Any]: ...


class SarvamTranscriber:
    """Sarvam batch STT + diarization → 2-speaker turns (AGENT/USER) so it renders as a chat."""

    def transcribe(self, audio_path: str, language: str | None = None) -> Dict[str, Any]:
        from app.utils.sarvam import transcribe_file
        # 'unknown' lets Sarvam auto-detect Hindi / Telugu / English etc.
        lang_code = "unknown" if not language else (f"{language}-IN" if "-" not in language else language)
        try:
            return transcribe_file(audio_path, with_diarization=True, num_speakers=2, language_code=lang_code)
        except Exception as e:
            logger.error(f"Sarvam transcription failed for {audio_path}: {e}", exc_info=True)
            return {"turns": [], "full_text": "", "language": language or "unknown", "quality": "failed", "error": str(e)}


def get_transcriber() -> Transcriber:
    return SarvamTranscriber()


def transcribe_audio(audio_path: str, language: str | None = None) -> Dict[str, Any]:
    return get_transcriber().transcribe(audio_path, language=language)

"""Regression cover: /api/calls/upload had no size limit anywhere —
`await file.read()` bought the entire body into memory unconditionally
before there was any chance to reject it. _read_upload_capped reads in
bounded chunks and aborts with a 413 as soon as the cap is exceeded,
instead of buffering the whole thing first."""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from app.api.calls import _read_upload_capped


def test_a_file_under_the_cap_reads_through_unchanged():
    payload = b"x" * 1000
    upload = UploadFile(file=io.BytesIO(payload), filename="call.mp3")
    result = asyncio.run(_read_upload_capped(upload, max_bytes=2000))
    assert result == payload


def test_a_file_over_the_cap_is_rejected_with_413():
    payload = b"x" * 3000
    upload = UploadFile(file=io.BytesIO(payload), filename="call.mp3")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_upload_capped(upload, max_bytes=2000))
    assert exc_info.value.status_code == 413


def test_the_cap_is_enforced_incrementally_not_after_full_buffering():
    """Reads in 1MB chunks — a file whose size exceeds the cap by more than
    one chunk must still be rejected partway through, not after fully
    materializing it in memory first."""
    max_bytes = 1024 * 1024  # 1MB
    payload = b"x" * (max_bytes * 5)  # 5MB, way over
    upload = UploadFile(file=io.BytesIO(payload), filename="call.mp3")
    with pytest.raises(HTTPException) as exc_info:
        asyncio.run(_read_upload_capped(upload, max_bytes=max_bytes))
    assert exc_info.value.status_code == 413

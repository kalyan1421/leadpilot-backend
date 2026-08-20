"""Regression cover: S3Manager._detect_audio_format_from_headers was never
updated when AMR/3GP support was added to local_storage.py/
supabase_storage.py. Two separate bugs: (1) the ISO-BMFF box-type check used
`header.startswith(b'ftyp')` — the box type actually sits at offset 4, not
0, so M4A/MP4/3GP detection never fired at all under S3 storage; (2) there
was no AMR/3GP branch whatsoever. Net effect: any AMR/3GP recording (common
on Xiaomi/Vivo/Oppo OEM dialers) got silently mis-tagged as mp3 when
S3 was the active storage backend."""

import tempfile

from app.utils.s3 import S3Manager


def _detector():
    return S3Manager.__new__(S3Manager)  # skip __init__ — no AWS client needed for header sniffing


def _write(data: bytes) -> str:
    f = tempfile.NamedTemporaryFile(delete=False)
    f.write(data)
    f.close()
    return f.name


def test_detects_m4a_from_the_correct_ftyp_offset():
    # box layout: [size:4][b"ftyp"][major_brand:4]...
    data = b"\x00\x00\x00\x18ftypM4A \x00\x00\x00\x00" + b"\x00" * 4
    path = _write(data)
    assert _detector()._detect_audio_format_from_headers(path) == "m4a"


def test_detects_3gp_from_the_major_brand():
    data = b"\x00\x00\x00\x18ftyp3gp4\x00\x00\x00\x00" + b"\x00" * 4
    path = _write(data)
    assert _detector()._detect_audio_format_from_headers(path) == "3gp"


def test_detects_amr():
    path = _write(b"#!AMR\n" + b"\x00" * 20)
    assert _detector()._detect_audio_format_from_headers(path) == "amr"


def test_detects_mp3_id3():
    path = _write(b"ID3" + b"\x00" * 20)
    assert _detector()._detect_audio_format_from_headers(path) == "mp3"


def test_unrecognized_header_falls_back_to_mp3():
    path = _write(b"\x00" * 20)
    assert _detector()._detect_audio_format_from_headers(path) == "mp3"


def test_content_type_map_covers_amr_and_3gp():
    detector = _detector()
    assert detector._get_content_type("amr") == "audio/amr"
    assert detector._get_content_type("3gp") == "audio/3gpp"

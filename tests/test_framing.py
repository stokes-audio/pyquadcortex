"""Tests for the HID frame codec (pyquadcortex.framing).

Pure, no-device tests against the CONFIRMED envelope (Windows USBPcap capture
of Cortex Control 4.0.1, 2026-07-22; docs/protocol.md "Confirmed
framing"). The golden fixtures under fixtures/frames/ are REAL captured frames:
host-encoded fixtures are exercised in both directions; device-emitted ones are
decode-only (the device fills trailer bytes the host leaves zero).
"""

import json
from pathlib import Path

import pytest

from pyquadcortex import framing
from pyquadcortex.proto import ProductionAutomation_pb2 as pa

FIXTURES = Path(__file__).parent / "fixtures" / "frames"
# (filename, encode_round_trip) - device-emitted fixtures are decode-only.
GOLDEN = [
    ("version_read.json", True),
    ("resetcomms_create.json", True),
    ("scene_update.json", True),
    ("version_reply_multi.json", False),
]


def _load(name):
    fixture = json.loads((FIXTURES / name).read_text())
    frames = [
        bytes.fromhex(h)
        for h in fixture.get("frames_hex", [fixture.get("frame_hex")])
    ]
    return fixture, frames


# -- golden captured frames ---------------------------------------------------


@pytest.mark.parametrize("name,_encode", GOLDEN)
def test_decode_matches_captured_fixture(name, _encode):
    fixture, frames = _load(name)
    msg_type, payload = framing.decode_reports(frames)
    assert pa.CortexMessageType.Enum.Name(msg_type) == fixture["message_type"]
    assert payload.hex() == fixture["payload_hex"]


@pytest.mark.parametrize(
    "name", [n for n, encode in GOLDEN if encode]
)
def test_encode_matches_captured_fixture(name):
    fixture, frames = _load(name)
    msg_type = pa.CortexMessageType.Enum.Value(fixture["message_type"])
    encoded = framing.encode_message(msg_type, bytes.fromhex(fixture["payload_hex"]))
    assert [r.hex() for r in encoded] == [r.hex() for r in frames]


def test_multi_report_fixture_completes_only_on_last_flag():
    _fixture, frames = _load("version_reply_multi.json")
    assert framing.is_complete(frames[:1]) is False
    assert framing.is_complete(frames[:2]) is False
    assert framing.is_complete(frames) is True


# -- round trips --------------------------------------------------------------


def test_round_trip_single_report():
    reports = framing.encode_message(10, b"\x08\x03")
    assert len(reports) == 1
    report = reports[0]
    assert len(report) == 129
    assert report[0] == framing.OUT_REPORT_ID
    assert report[1] == 2 + framing.TRAILER_SIZE  # len: pb + trailer
    assert report[2] == framing.FLAG_FIRST | framing.FLAG_LAST
    msg_type, payload = framing.decode_reports(reports)
    assert (msg_type, payload) == (10, b"\x08\x03")


def test_round_trip_multi_report():
    payload = bytes(range(256)) * 3  # 768 bytes, forces chunking
    reports = framing.encode_message(15, payload)
    assert len(reports) > 1
    for report in reports:
        assert len(report) == 129
    assert reports[0][2] == framing.FLAG_FIRST
    for report in reports[1:-1]:
        assert report[2] == 0x00
        assert report[1] == framing.CHUNK_SIZE
    assert reports[-1][2] == framing.FLAG_LAST
    msg_type, decoded = framing.decode_reports(reports)
    assert msg_type == 15
    assert decoded == payload


def test_round_trip_empty_payload():
    reports = framing.encode_message(10, b"")
    assert len(reports) == 1
    assert len(reports[0]) == 129
    assert framing.decode_reports(reports) == (10, b"")


def test_round_trip_payload_exactly_fills_first_report():
    # 126-byte chunk capacity minus the 8-byte trailer = 118 payload bytes.
    payload = bytes(range(118))
    reports = framing.encode_message(10, payload)
    assert len(reports) == 1
    assert framing.decode_reports(reports) == (10, payload)


def test_round_trip_payload_one_byte_over_first_report():
    # One byte past single-report capacity forces a second report.
    payload = bytes(range(119))
    reports = framing.encode_message(10, payload)
    assert len(reports) == 2
    assert framing.decode_reports(reports) == (10, payload)


def test_round_trip_message_type_above_one_byte():
    # The type tag is u16 LE in the trailer; exercise a value needing both bytes.
    reports = framing.encode_message(0x1234, b"\x01\x02")
    assert framing.decode_reports(reports) == (0x1234, b"\x01\x02")


# -- is_complete --------------------------------------------------------------


def test_is_complete_single_report_message():
    reports = framing.encode_message(10, b"\x08\x03")
    assert framing.is_complete(reports) is True


def test_is_complete_reports_empty_list_not_complete():
    assert framing.is_complete([]) is False


def test_is_complete_partial_then_full_multi_report():
    payload = bytes(range(256)) * 3
    reports = framing.encode_message(15, payload)
    assert len(reports) > 1
    for n in range(1, len(reports)):
        assert framing.is_complete(reports[:n]) is False, f"prefix of {n} reports"
    assert framing.is_complete(reports) is True


# -- error handling -----------------------------------------------------------


def test_decode_strips_report_id_regardless_of_value():
    # Device input reports use ID 0x01; decode must not assert the ID value.
    _fixture, frames = _load("version_reply_multi.json")
    assert all(f[0] == framing.IN_REPORT_ID for f in frames)
    msg_type, _payload = framing.decode_reports(frames)
    assert msg_type == pa.CortexMessageType.Enum.Value("Version")


def test_decode_rejects_missing_first_flag():
    _fixture, frames = _load("version_reply_multi.json")
    with pytest.raises(ValueError, match="FIRST"):
        framing.decode_reports(frames[1:])  # starts with a middle fragment


def test_decode_rejects_missing_last_flag():
    _fixture, frames = _load("version_reply_multi.json")
    with pytest.raises(ValueError, match="LAST"):
        framing.decode_reports(frames[:2])  # ends before the last fragment


def test_decode_rejects_empty_list():
    with pytest.raises(ValueError):
        framing.decode_reports([])


def test_decode_rejects_body_shorter_than_trailer():
    # A complete-flagged report whose len declares less than the 8-byte trailer.
    body = bytes([4, 0xC0, 0xDE, 0xAD, 0xBE, 0xEF])
    report = bytes([framing.IN_REPORT_ID]) + body + bytes(129 - 1 - len(body))
    with pytest.raises(ValueError, match="trailer"):
        framing.decode_reports([report])

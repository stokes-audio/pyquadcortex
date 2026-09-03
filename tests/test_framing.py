"""Tests for the HID frame codec (pyquadcortex.protocol.framing).

Pure, no-device tests against the CONFIRMED envelope (Windows USBPcap capture
of Cortex Control 4.0.1; docs/protocol.md "Confirmed
framing"). The golden fixtures under fixtures/frames/ are REAL captured frames:
host-encoded fixtures are exercised in both directions; device-emitted ones are
decode-only (the device fills trailer bytes the host leaves zero).
"""

import json
from pathlib import Path

import pytest
from google.protobuf.message import DecodeError

from pyquadcortex.protocol import framing
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

FIXTURES = Path(__file__).parent / "fixtures" / "frames"
# (filename, encode_round_trip) - device-emitted fixtures are decode-only.
GOLDEN = [
    ("version_read.json", True),
    ("resetcomms_create.json", True),
    ("scene_update.json", True),
    ("version_reply_multi.json", False),
    ("license_read.json", True),
    ("license_reply_encrypted.json", False),
    ("file_reply_plain.json", False),
    ("file_reply_gzip.json", False),
]
GOLDEN_NAMES = [name for name, _encode in GOLDEN]


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
    frame = framing.decode_reports(frames)
    assert pa.CortexMessageType.Enum.Name(frame.message_type) == fixture["message_type"]
    assert frame.payload.hex() == fixture["payload_hex"]


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
    frame = framing.decode_reports(reports)
    assert (frame.message_type, frame.payload) == (10, b"\x08\x03")


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
    frame = framing.decode_reports(reports)
    assert frame.message_type == 15
    assert frame.payload == payload


def test_round_trip_empty_payload():
    reports = framing.encode_message(10, b"")
    assert len(reports) == 1
    assert len(reports[0]) == 129
    frame = framing.decode_reports(reports)
    assert (frame.message_type, frame.payload) == (10, b"")


def test_round_trip_payload_exactly_fills_first_report():
    # 126-byte chunk capacity minus the 8-byte trailer = 118 payload bytes.
    payload = bytes(range(118))
    reports = framing.encode_message(10, payload)
    assert len(reports) == 1
    frame = framing.decode_reports(reports)
    assert (frame.message_type, frame.payload) == (10, payload)


def test_round_trip_payload_one_byte_over_first_report():
    # One byte past single-report capacity forces a second report.
    payload = bytes(range(119))
    reports = framing.encode_message(10, payload)
    assert len(reports) == 2
    frame = framing.decode_reports(reports)
    assert (frame.message_type, frame.payload) == (10, payload)


def test_round_trip_message_type_above_one_byte():
    # The type tag is u32 LE in the trailer; exercise a value needing two bytes.
    # Every real type is under 256, so the high half is zero on the wire and the
    # width is not observable - see docs/protocol.md section 2.3.
    reports = framing.encode_message(0x1234, b"\x01\x02")
    frame = framing.decode_reports(reports)
    assert (frame.message_type, frame.payload) == (0x1234, b"\x01\x02")


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
    frame = framing.decode_reports(frames)
    assert frame.message_type == pa.CortexMessageType.Enum.Value("Version")


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


# -- the trailer's named fields -----------------------------------------------
#
# Confirmed 2026-09-03 against three USBPcap captures of Cortex Control 4.0.1
# (quad-cortex research/captures/windows-session-0{1,2,3}-nonaudio.pcapng,
# CorOS 4.0.1, 15,675 logical messages both directions). ENCRYPTED was set on 13
# of them and only on License/CloudLogin; COMPRESSED on 39 and agreed with the
# gzip magic bytes 15675/15675 times. Reproduced live on the unit the same day
# through this library's own client, 1,000 messages, same firmware.
# See docs/protocol.md section 2.3.

@pytest.mark.parametrize("name", GOLDEN_NAMES)
def test_decode_reports_trailer_fields_match_fixture(name):
    """Every fixture is decoded to the exact trailer bytes it recorded.

    The fixture stores the raw 8-byte trailer as well as the fields read out of
    it, so this checks the decode against the captured bytes and not against
    itself.
    """
    fixture, frames = _load(name)
    frame = framing.decode_reports(frames)
    trailer = bytes.fromhex(fixture["trailer_hex"])

    assert len(trailer) == framing.TRAILER_SIZE
    assert frame.message_type == int.from_bytes(trailer[framing.TRAILER_TYPE], "little")
    assert frame.encrypted is bool(trailer[framing.TRAILER_ENCRYPTED])
    assert frame.compressed is bool(trailer[framing.TRAILER_COMPRESSED])
    assert frame.device_bytes == trailer[framing.TRAILER_DEVICE]
    # ... and the fixture's own plain-language record of the same three facts.
    assert (frame.encrypted, frame.compressed, frame.device_bytes.hex()) == (
        fixture["encrypted"],
        fixture["compressed"],
        fixture["device_bytes_hex"],
    )


def test_encrypted_is_per_frame_not_per_message_type():
    """The SAME message type, one frame flagged and one not.

    A License READ leaves the host plain; the device's reply to it is flagged.
    Anything keyed on the message type instead of the frame reads one of these
    two wrong, which is what makes this pair the test and not just an example.
    """
    request = framing.decode_reports(_load("license_read.json")[1])
    reply = framing.decode_reports(_load("license_reply_encrypted.json")[1])

    assert request.message_type == reply.message_type
    assert request.encrypted is False
    assert reply.encrypted is True


def test_compressed_is_per_frame_not_per_message_type():
    """The same pairing for COMPRESSED: two File replies, one gzipped."""
    plain = framing.decode_reports(_load("file_reply_plain.json")[1])
    gzipped = framing.decode_reports(_load("file_reply_gzip.json")[1])

    assert plain.message_type == gzipped.message_type
    assert plain.compressed is False
    assert gzipped.compressed is True


def test_compressed_flag_agrees_with_the_gzip_magic_bytes():
    """The flag and the magic bytes say the same thing.

    The library detects compression by the magic bytes, not by this flag
    (docs/protocol.md 2.4). The flag being informational is exactly why it needs
    a test that would notice it drifting.
    """
    plain = framing.decode_reports(_load("file_reply_plain.json")[1])
    gzipped = framing.decode_reports(_load("file_reply_gzip.json")[1])

    assert gzipped.compressed is True
    assert gzipped.payload[:2] == b"\x1f\x8b"
    assert plain.compressed is False
    assert plain.payload[:2] != b"\x1f\x8b"


def test_encrypted_payload_is_returned_undecrypted_and_unparsed():
    """decode_reports hands back the flagged bytes as they arrived.

    The codec labels an encrypted payload; it does not decrypt one, and nothing
    in this library does. The 17 bytes below are not protobuf.
    """
    frame = framing.decode_reports(_load("license_reply_encrypted.json")[1])
    fixture, _ = _load("license_reply_encrypted.json")

    assert frame.encrypted is True
    assert frame.payload.hex() == fixture["payload_hex"]
    with pytest.raises(DecodeError):
        pa.LicenseMessage().ParseFromString(frame.payload)


def test_flags_are_read_from_their_own_trailer_bytes():
    """Pin the two offsets separately, so swapping them fails.

    Built by hand rather than captured: the captures have no frame with both
    flags set, so only a synthetic one can show the bytes are read independently.
    """
    for encrypted, compressed in ((0, 0), (1, 0), (0, 1), (1, 1)):
        trailer = (bytes([10, 0, 0, 0, encrypted, compressed]) + b"\x00\x00")
        body = b"\x08\x03" + trailer
        report = (bytes([framing.IN_REPORT_ID, len(body), 0xC0]) + body
                  + bytes(129 - 3 - len(body)))
        frame = framing.decode_reports([report])
        assert frame.encrypted is bool(encrypted), (encrypted, compressed)
        assert frame.compressed is bool(compressed), (encrypted, compressed)
        assert frame.message_type == 10


def test_device_bytes_are_reported_not_swallowed():
    """The two bytes at n+6 still have no known meaning, so we hand them over.

    The device leaves them zero on most frames and fills them on some; the host
    always sends zeros. Nothing in the library reads them.
    """
    reply = framing.decode_reports(_load("license_reply_encrypted.json")[1])
    request = framing.decode_reports(_load("license_read.json")[1])

    assert reply.device_bytes == bytes.fromhex("a2b6")
    assert request.device_bytes == b"\x00\x00"


def test_host_encoded_frames_carry_both_flags_clear():
    """We never send a flagged frame: no compression, no encryption, ever."""
    frame = framing.decode_reports(framing.encode_message(10, b"\x08\x03"))
    assert frame.encrypted is False
    assert frame.compressed is False
    assert frame.device_bytes == b"\x00\x00"

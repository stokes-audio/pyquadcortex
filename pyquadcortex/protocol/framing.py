"""HID frame codec for the Quad Cortex USB-HID protobuf transport.

This module converts between logical messages and the raw 129-byte HID reports
exchanged with the device. It deals ONLY in bytes and integers: no hidapi, no
protobuf. ``message_type`` is just an int.

Confirmed wire format (USBPcap capture of Cortex Control 4.0.1; see
docs/protocol.md):

  Report (129 bytes over hidapi, report-ID byte + 128-byte body)::

      [report_id][len u8][flags u8][data ... up to 126 bytes, zero padded]

  * OUTPUT reports (host->device) use report ID 0x02, INPUT 0x01.
  * ``flags``: bit 0x40 = FIRST fragment, bit 0x80 = LAST fragment.
    A complete single-report message is 0xC0; middle fragments are 0x00.
  * ``len`` counts the VALID data bytes in this report, excluding the
    report-id/len/flags bytes themselves. Non-final fragments always carry
    a full 126 (= 128 - 2) bytes; the final fragment's ``len`` says how many
    of its data bytes are meaningful (the rest is padding/stale buffer).
  * The reassembled logical body is ``protobuf ++ trailer(8)``, and the trailer
    is ``[message_type u32 LE][encrypted u8][compressed u8][2 device bytes]``.
    The message type tag lives in the TRAILER, not a header.

There is no total-length field: reassembly is driven purely by the flags.

The trailer's two flag bytes
----------------------------

Confirmed 2026-09-03 twice over. Offline across three USBPcap captures of
Cortex Control 4.0.1 (CorOS 4.0.1), 15,675 logical messages in both directions,
and live on the unit through this library's own client, 1,000 messages, which
reproduced every result below. Same firmware both times, so this is evidence
about two clients rather than two CorOS versions. What was measured:

* ``encrypted`` was set on 13 messages and only ever on ``License`` and
  ``CloudLogin``. Every one of those 13 payloads fails to parse as protobuf,
  and every one of the other 15,662 does not fail for that reason. **This
  library labels such a payload and hands it back untouched; it does not
  decrypt one, and nothing in it will.**
* ``compressed`` was set on 39 messages, and agreed with the payload's gzip
  magic bytes 15675 times out of 15675 (1000 out of 1000 live). The library
  still detects compression by the magic bytes rather than by this flag - see
  docs/protocol.md 2.4 for why. The flag never decides what happens to a
  payload; the transport does compare the two and logs a line if they ever
  disagree, since that agreement was only ever measured on one firmware.

Both flags describe the FRAME, not the message type: a ``License`` READ from
the host is unflagged while the device's reply to it is encrypted, and a
``File`` reply appears both compressed and not. Anything keyed on the message
type would get one of those wrong.

The four bytes at trailer offset 0 are read as a uint32. Bytes 2 and 3 were
zero in all 15,675 messages, and every message type is under 256, so a uint16
type plus two reserved zero bytes fits the evidence equally well. The width is
not observable; the flag positions that follow it are.
"""

from dataclasses import dataclass

# --- Confirmed HID-layer constants -------------------------------------------
REPORT_SIZE = 128  # body bytes per report (report-ID byte is separate)
OUT_REPORT_ID = 0x02  # host -> device (OUTPUT reports)
IN_REPORT_ID = 0x01  # device -> host (INPUT reports)

# --- Confirmed envelope constants --------------------------------------------
FLAG_FIRST = 0x40
FLAG_LAST = 0x80
# Per-report data capacity: the 128-byte body minus the [len][flags] prefix.
CHUNK_SIZE = REPORT_SIZE - 2  # 126
# Trailer appended to the protobuf payload before chunking. Offsets are from the
# start of the trailer, i.e. from ``n`` where the payload is ``n`` bytes long.
TRAILER_SIZE = 8
TRAILER_TYPE = slice(0, 4)  # message type, uint32 LE
TRAILER_ENCRYPTED = 4  # 1 = payload is not protobuf and we cannot read it
TRAILER_COMPRESSED = 5  # 1 = payload is a gzip stream
TRAILER_DEVICE = slice(6, 8)  # device-filled, meaning still unknown


@dataclass(frozen=True)
class Frame:
    """One reassembled logical message, with its envelope read out.

    ``payload`` is exactly what the trailer wrapped: still gzipped if
    ``compressed``, still encrypted if ``encrypted``. Decompression belongs to
    the caller (``transport._handle_message`` does it by magic bytes), and
    decryption belongs to nobody - see the module docstring.

    ``device_bytes`` are the two trailer bytes at offset 6, which have no known
    meaning. The host sends zeros; the device fills them on some frames. They
    are reported here rather than dropped so that nothing has to re-read the
    frame to see them, but no code in this library depends on their value.
    """

    message_type: int
    payload: bytes
    encrypted: bool
    compressed: bool
    device_bytes: bytes


def encode_message(message_type: int, payload: bytes) -> list[bytes]:
    """Frame one logical message into one or more 129-byte HID output reports.

    Appends the 8-byte trailer to ``payload``, splits the result into 126-byte
    chunks, and wraps each chunk as ``[OUT_REPORT_ID][len][flags][chunk, zero
    padded to 126]``. The first chunk's flags carry FLAG_FIRST, the last
    FLAG_LAST (a single-report message carries both, 0xC0).

    The host never sets the ENCRYPTED or COMPRESSED flags and never fills the
    two device bytes, so everything after the type is zero. That is what
    Cortex Control 4.0.1 sends too, in all 1,136 host frames measured.
    """
    trailer = bytearray(TRAILER_SIZE)
    trailer[TRAILER_TYPE] = message_type.to_bytes(4, "little")
    body = payload + bytes(trailer)

    chunks = [body[i : i + CHUNK_SIZE] for i in range(0, len(body), CHUNK_SIZE)]

    reports = []
    for i, chunk in enumerate(chunks):
        flags = (FLAG_FIRST if i == 0 else 0) | (
            FLAG_LAST if i == len(chunks) - 1 else 0
        )
        prefix = bytes([len(chunk), flags])
        padded = chunk + bytes(CHUNK_SIZE - len(chunk))
        reports.append(bytes([OUT_REPORT_ID]) + prefix + padded)
    return reports


def decode_reports(reports: list[bytes]) -> Frame:
    """Reassemble one logical message from one or more HID reports.

    Strips each report's leading report-ID byte (WITHOUT asserting its value),
    concatenates each report's ``len`` valid data bytes, then splits the
    reassembled body into a :class:`Frame` by reading the 8-byte trailer. Raises
    ``ValueError`` on structurally invalid input (no FIRST flag on the first
    report, no LAST on the final one, or a body too short to hold the trailer).
    """
    if not reports:
        raise ValueError("decode_reports requires at least one report")

    first_flags = _flags(reports[0])
    if not first_flags & FLAG_FIRST:
        raise ValueError(f"first report lacks FIRST flag (flags=0x{first_flags:02x})")
    last_flags = _flags(reports[-1])
    if not last_flags & FLAG_LAST:
        raise ValueError(f"final report lacks LAST flag (flags=0x{last_flags:02x})")

    body = bytearray()
    for report in reports:
        data = report[1:]  # strip report-ID byte
        length = data[0]
        body += data[2 : 2 + length]

    if len(body) < TRAILER_SIZE:
        raise ValueError(
            f"reassembled body is {len(body)} bytes, shorter than the "
            f"{TRAILER_SIZE}-byte trailer"
        )
    payload, trailer = bytes(body[:-TRAILER_SIZE]), bytes(body[-TRAILER_SIZE:])
    return Frame(
        message_type=int.from_bytes(trailer[TRAILER_TYPE], "little"),
        payload=payload,
        encrypted=bool(trailer[TRAILER_ENCRYPTED]),
        compressed=bool(trailer[TRAILER_COMPRESSED]),
        device_bytes=trailer[TRAILER_DEVICE],
    )


def is_complete(reports: list[bytes]) -> bool:
    """Report whether ``reports`` hold a full logical message yet.

    Completion is flag-driven: the message is complete exactly when the most
    recent report carries FLAG_LAST. An empty list is never complete.
    """
    if not reports:
        return False
    return bool(_flags(reports[-1]) & FLAG_LAST)


def _flags(report: bytes) -> int:
    """Return the flags byte of ``report`` (body offset 1, after the report ID)."""
    if len(report) < 3:
        raise ValueError(f"report is only {len(report)} bytes; need id+len+flags")
    return report[2]

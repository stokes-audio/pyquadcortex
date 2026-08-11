"""HID frame codec for the Quad Cortex USB-HID protobuf transport.

This module converts between logical messages ``(message_type, protobuf_bytes)``
and the raw 129-byte HID reports exchanged with the device. It deals ONLY in
bytes and integers: no hidapi, no protobuf. ``message_type`` is just an int.

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
  * The reassembled logical body is ``protobuf ++ trailer(8)`` where
    ``trailer = [message_type u16 LE][4 zero bytes][2 bytes: zeros from the
    host; the device fills values whose meaning is unknown - ignore them]``.
    The message type tag lives in the TRAILER, not a header.

There is no total-length field: reassembly is driven purely by the flags.
"""

# --- Confirmed HID-layer constants -------------------------------------------
REPORT_SIZE = 128  # body bytes per report (report-ID byte is separate)
OUT_REPORT_ID = 0x02  # host -> device (OUTPUT reports)
IN_REPORT_ID = 0x01  # device -> host (INPUT reports)

# --- Confirmed envelope constants --------------------------------------------
FLAG_FIRST = 0x40
FLAG_LAST = 0x80
# Per-report data capacity: the 128-byte body minus the [len][flags] prefix.
CHUNK_SIZE = REPORT_SIZE - 2  # 126
# Trailer appended to the protobuf payload before chunking:
# [message_type u16 LE][6 bytes; host sends zeros].
TRAILER_SIZE = 8


def encode_message(message_type: int, payload: bytes) -> list[bytes]:
    """Frame one logical message into one or more 129-byte HID output reports.

    Appends the 8-byte trailer (``[message_type u16 LE]`` + six zero bytes) to
    ``payload``, splits the result into 126-byte chunks, and wraps each chunk as
    ``[OUT_REPORT_ID][len][flags][chunk, zero padded to 126]``. The first
    chunk's flags carry FLAG_FIRST, the last FLAG_LAST (a single-report message
    carries both, 0xC0).
    """
    body = payload + message_type.to_bytes(2, "little") + bytes(TRAILER_SIZE - 2)

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


def decode_reports(reports: list[bytes]) -> tuple[int, bytes]:
    """Reassemble one logical message from one or more HID reports.

    Strips each report's leading report-ID byte (WITHOUT asserting its value),
    concatenates each report's ``len`` valid data bytes, then splits the
    reassembled body into ``(message_type, protobuf_bytes)`` by reading the
    8-byte trailer. Raises ``ValueError`` on structurally invalid input (no
    FIRST flag on the first report, no LAST on the final one, or a body too
    short to hold the trailer).
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
    payload, trailer = bytes(body[:-TRAILER_SIZE]), body[-TRAILER_SIZE:]
    message_type = int.from_bytes(trailer[0:2], "little")
    # trailer[2:6] is always zero on the wire; trailer[6:8] is zero from the
    # host but carries unknown nonzero values from the device. Both ignored.
    return message_type, payload


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

"""Read-only hardware coverage for the CorOS 4.1 remote screen."""

import struct

import pytest


def test_capture_screen_returns_the_observed_complete_framebuffer(qc):
    """Capture does not tap or otherwise mutate the device UI."""
    factory_count = len(qc.catalog.factory_models())
    if factory_count == 412:
        pytest.skip("RemoteControl is unavailable in the CorOS 4.0.1 schema")
    assert factory_count == 420, (
        f"unidentified catalog with {factory_count} factory models; do not infer "
        "RemoteControl support"
    )
    png = qc.capture_screen()

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert png[12:16] == b"IHDR"
    assert struct.unpack(">II", png[16:24]) == (800, 480)
    assert png[-12:] == b"\x00\x00\x00\x00IEND\xaeB`\x82"

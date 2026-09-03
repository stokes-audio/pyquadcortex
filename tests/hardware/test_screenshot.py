"""Read-only hardware coverage for preset screenshots."""

import struct


def test_current_preset_screenshot_has_the_observed_png_dimensions(qc):
    """The request is addressed from live state and does not change that state."""
    before = qc.loaded_position()
    folders = qc.list_folders(seconds=20.0)
    folder = next(
        item for item in folders
        if item.key.rstrip("/") == before.folder_key.rstrip("/")
    )

    png = qc.preset_screenshot(folder.name, before.position, before.is_factory)

    assert png[:8] == b"\x89PNG\r\n\x1a\n"
    assert struct.unpack(">II", png[16:24]) == (800, 384)
    after = qc.loaded_position()
    assert after.folder_key == before.folder_key
    assert after.position == before.position
    assert after.is_factory == before.is_factory

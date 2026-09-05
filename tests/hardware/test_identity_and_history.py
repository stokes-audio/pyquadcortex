"""Reversible device-name and edit-history checks against a real unit."""

import time

import pytest

from pyquadcortex import protocol
from pyquadcortex.protocol.enums import Setlist


SETTLE = 1.0


def test_device_name_round_trips_and_is_restored(qc):
    identity = qc.version()
    if not identity.HasField("custom_name"):
        pytest.skip("the unit has no device name to restore without inventing one")
    before = identity.custom_name
    probe = "pyquadcortex probe"
    if before == probe:
        probe = "pyquadcortex probe 2"

    try:
        qc.set_device_name(probe)
        time.sleep(SETTLE)
        assert qc.version().custom_name == probe
    finally:
        qc.set_device_name(before)
        time.sleep(SETTLE)

    assert qc.version().custom_name == before


def test_undo_and_redo_reverse_and_reapply_a_scratch_edit(qc, restores):
    """Create, edit, and remove a test-owned copy of the loaded preset."""
    original = qc.loaded_position()
    for field in ("folder_key", "position", "is_factory"):
        assert original.HasField(field), f"loaded position omitted {field}"
    original_scene = int(qc.active_scene())

    scratch_name = "pyquadcortex undo probe"
    slots = qc.list_presets(Setlist.USER, include_empty=True)
    assert not any(e.HasField("name") and e.name == scratch_name for e in slots), (
        f"delete the existing {scratch_name!r} preset before running this test"
    )
    empty = next((e for e in slots
                  if e.HasField("index")
                  and (not e.HasField("name") or not e.name)), None)
    if empty is None:
        pytest.skip("My Presets has no empty slot for the disposable copy")

    def delete_scratch():
        qc.delete_preset(Setlist.USER, scratch_name)
        qc.wait_for_listing(
            Setlist.USER,
            until=lambda entries: not any(
                e.HasField("name") and e.name == scratch_name for e in entries
            ),
            timeout=30.0,
        )

    restores("delete the undo-test scratch preset", delete_scratch)
    restores("restore the original active scene",
             lambda: qc.switch_scene(original_scene))
    restores("recall the originally loaded preset", lambda: qc.read_preset(
        original.folder_key, original.position,
        is_factory=original.is_factory, timeout=30.0,
    ))

    stored = qc.save_current_preset(
        Setlist.USER, empty.index, scratch_name,
        confirm=True, confirm_timeout=30.0,
    )
    assert stored == scratch_name, "the disposable copy was not confirmed"
    before_preset = qc.read_preset(Setlist.USER, empty.index, timeout=30.0)
    occupied = protocol.blocks(before_preset)
    if not occupied:
        pytest.skip("the loaded preset has no block whose bypass can be edited")
    target = occupied[0]
    before_scene = int(qc.active_scene())
    before = protocol.bypass_state(before_preset, target).scenes[before_scene]

    qc.set_bypass(target, not before)
    time.sleep(SETTLE)
    assert protocol.bypass_state(
        qc.read_current_preset(), target
    ).scenes[before_scene] is not before

    qc.undo()
    time.sleep(SETTLE)
    assert protocol.bypass_state(
        qc.read_current_preset(), target
    ).scenes[before_scene] is before

    qc.redo()
    time.sleep(SETTLE)
    assert protocol.bypass_state(
        qc.read_current_preset(), target
    ).scenes[before_scene] is not before

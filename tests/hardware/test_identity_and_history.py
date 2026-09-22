"""Reversible device-name and edit-history checks against a real unit."""

import time

import pytest

from pyquadcortex import protocol
SETTLE = 1.0


@pytest.mark.verifies("set_device_name")
def test_device_name_round_trips_and_is_restored(qc):
    identity = qc.version()
    if not identity.HasField("custom_name"):
        pytest.skip("the unit has no device name to restore without inventing one")
    before = identity.custom_name
    probe = "pyquadcortex probe"
    if before == probe:
        probe = "pyquadcortex probe 2"

    def read_name():
        reply = qc.version()
        assert reply.HasField("custom_name"), (
            "the Version reply omitted custom_name, so the name cannot be "
            "confirmed or restored")
        return reply.custom_name

    try:
        qc.set_device_name(probe)
        time.sleep(SETTLE)
        assert read_name() == probe
    finally:
        qc.set_device_name(before)
        time.sleep(SETTLE)

    assert read_name() == before


@pytest.mark.verifies("undo", "redo")
def test_undo_and_redo_reverse_and_reapply_a_scratch_edit(qc, scratch_preset):
    """Create, edit, and remove a test-owned copy of the loaded preset."""
    setlist, position, _name = scratch_preset
    before_preset = qc.read_preset(setlist, position, timeout=30.0)
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

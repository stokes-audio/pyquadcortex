"""Reversible device-name and edit-history checks against a real unit."""

import os
import time

import pytest

from pyquadcortex import protocol
from pyquadcortex.protocol import Block


SETTLE = 1.0


def test_device_name_round_trips_and_is_restored(qc):
    before = qc.version().custom_name
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


def test_undo_and_redo_reverse_and_reapply_a_scratch_edit(qc):
    """Opt-in twice: --hardware plus the exact disposable slot in the env."""
    configured = os.environ.get("PYQUADCORTEX_UNDO_SCRATCH_SLOT")
    if configured is None:
        pytest.skip("set PYQUADCORTEX_UNDO_SCRATCH_SLOT to a disposable loaded slot")

    position = qc.loaded_position()
    expected = int(configured)
    assert position.position == expected, (
        f"scratch slot {expected} is configured, but slot {position.position} is loaded"
    )
    assert qc.preset_dirty() is False, (
        "save or reload the scratch preset first"
    )

    target = Block(0, 6)
    before_preset = qc.read_current_preset()
    before_scene = int(qc.active_scene())
    before = protocol.bypass_state(before_preset, target).scenes[before_scene]
    name = before_preset.name

    try:
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
    finally:
        now = protocol.bypass_state(
            qc.read_current_preset(), target
        ).scenes[before_scene]
        if now is not before:
            qc.set_bypass(target, before)
            time.sleep(SETTLE)
        qc.save_current_preset(position.folder_key, position.position, name,
                               confirm=True, confirm_timeout=30.0)

    assert protocol.bypass_state(
        qc.read_current_preset(), target
    ).scenes[before_scene] is before
    assert qc.preset_dirty() is False

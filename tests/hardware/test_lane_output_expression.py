"""Assigning an expression pedal to a Lane Output Control, on the real unit.

The Lane Output Control has no column - it lives in ``chain.output_control[]``
rather than ``chain.models[]`` - so `set_expression` cannot reach it, which is
what `set_lane_output_expression` exists for.

The load-bearing fact here is an ASYMMETRY the device never announces: it
accepts a host expression assignment on a CONTINUOUS lane parameter and
silently drops one on a SWITCH parameter, in both directions. Measured with
four message shapes on MUTE, including the byte-identical message VOLUME
accepted in the same session. The unit's own touchscreen writes the very same
field, so the control is understood and not drivable, and the library refuses
it out loud (ADR-0007).

Every test snapshots what it touches and registers a restore before writing, so
the run is state-neutral whether it passes or fails (ADR-0005).
"""
import time

import pytest

from pyquadcortex.protocol.client import (UNITY_LEVEL, ControlNotDrivable,
                                          db_to_lane_level)

#: A read straight after a write returns the PREVIOUS value on this firmware.
#: This trap has produced two wrong conclusions in this project, one of which
#: stood as a documented fact for several releases, so every read-back here
#: settles first rather than trusting an immediate reply.
SETTLE = 3.0

#: The lane whose Lane Output Control these tests drive. Chosen by the same rule
#: the rest of the suite uses: touch as little as possible. Any row carries one.
ROW = 1


def _lane(qc, row=ROW):
    return qc.read_current_preset().chains[row].output_control[0]


def _volume(qc, row=ROW):
    return _lane(qc, row).params[0]


def _snapshot(qc, row=ROW):
    """Everything about the lane VOLUME a test here can disturb."""
    p = _volume(qc, row)
    return {
        "expression": p.expression,
        "minimum": p.expression_min,
        "maximum": p.expression_max,
        "value": p.param_values[0].float_value if p.param_values else UNITY_LEVEL,
    }


def _restore(qc, was, row=ROW):
    if was["expression"]:
        qc.set_lane_output_expression(
            row=row, param="VOLUME", pedal=was["expression"],
            minimum=was["minimum"], maximum=was["maximum"])
    else:
        qc.clear_lane_output_expression(row=row, param="VOLUME")
    time.sleep(0.3)
    qc.set_lane_output(row=row, param="VOLUME", value=was["value"])


def test_a_pedal_assigns_to_the_lane_volume_and_clears_again(qc, restores):
    """The whole round trip: assign, read back, clear, read back."""
    was = _snapshot(qc)
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    qc.set_lane_output_expression(row=ROW, param="VOLUME", pedal=1,
                                  minimum=0.0, maximum=0.6)
    time.sleep(SETTLE)
    p = _volume(qc)
    assert p.expression == 1, "the pedal did not take"
    assert p.expression_min == pytest.approx(0.0, abs=1e-4)
    assert p.expression_max == pytest.approx(0.6, abs=1e-4)

    qc.clear_lane_output_expression(row=ROW, param="VOLUME")
    time.sleep(SETTLE)
    p = _volume(qc)
    assert p.expression == 0, "the assignment did not clear"
    assert p.expression_max == pytest.approx(1.0, abs=1e-4)


def test_the_assignment_leaves_scene_mode_alone(qc, restores):
    """The unit does not promote a parameter to scene-following when IT assigns.

    An early probe carried ``scene_mode: true`` and worked, which made the flag
    look required. It is not: assigning on the touchscreen and reading back
    showed the unit leaves it untouched, and the manual excludes an
    expression-assigned parameter from Scene data anyway.
    """
    was = _snapshot(qc)
    before = _volume(qc).scene_mode
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    qc.set_lane_output_expression(row=ROW, param="VOLUME", pedal=1)
    time.sleep(SETTLE)
    assert _volume(qc).scene_mode == before, "the assignment moved scene_mode"


@pytest.mark.parametrize("param", ["MUTE", "SOLO"])
def test_the_two_unassignable_parameters_refuse_rather_than_failing_silently(qc, param):
    """The device drops these without a word. The library must not.

    Nothing is written, so this needs no restore - which is the point: a refusal
    that reached the wire would be the bug.
    """
    with pytest.raises(ControlNotDrivable):
        qc.set_lane_output_expression(row=ROW, param=param)
    with pytest.raises(ControlNotDrivable):
        qc.clear_lane_output_expression(row=ROW, param=param)


def test_the_lane_volume_speaks_dB_through_real(qc, restores):
    """`real=` converts through the MEASURED -40..+12 dB span.

    The catalog publishes this parameter as ``0..1 "dB"`` - a placeholder - so
    the conversion cannot come from there. It is the one placeholder parameter
    whose true span has been measured at both ends; every other one still
    refuses `real=`.
    """
    was = _snapshot(qc)
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    qc.set_lane_output(row=ROW, param="VOLUME", real=-3.1)
    time.sleep(SETTLE)
    assert _volume(qc).param_values[0].float_value == pytest.approx(
        db_to_lane_level(-3.1), abs=2e-4)

    qc.set_lane_output(row=ROW, param="VOLUME", real=0.0)
    time.sleep(SETTLE)
    assert _volume(qc).param_values[0].float_value == pytest.approx(
        UNITY_LEVEL, abs=2e-4), "0 dB is not unity"

"""Reading back an expression assignment, against the real unit.

The offline tests read committed fixtures, which prove the reader against bytes
the device once produced. This proves the loop: write an assignment with
`set_expression`, read it back with `expression_assignments`, and get the same
thing - which is the one claim a fixture cannot make, because a fixture cannot
notice the wire shape changing under us.

State-neutral per ADR-0005. Note what that costs here and why: assigning a
pedal to a lane VOLUME drives that volume to wherever the physical pedal is
sitting, so the SNAPSHOT has to carry the value as well as the assignment.
`test_expression_targets.py` learned that first and this file follows it rather
than inventing a thinner version.
"""

import time

import pytest

from pyquadcortex import protocol
from pyquadcortex.protocol.targets import LaneOutput
from pyquadcortex.protocol.units import UNITY_LEVEL
from pyquadcortex.protocol.values import Encoded

#: Three seconds, matching `test_expression_targets.py`. A read straight after
#: a write returns the PREVIOUS value on this firmware, and that trap has
#: produced two wrong conclusions in this project - one of which stood as a
#: documented fact for several releases. This file has never been run, so
#: shaving the margin the suite learned the hard way would be the worst place
#: to save two seconds.
SETTLE = 3.0

#: Row 1, the row `test_expression_targets.py` already disturbs, on the same
#: "touch as little as possible" grounds.
ROW = 1
PARAM = "VOLUME"


def _lane(qc):
    return qc.read_current_preset().chains[ROW].output_control[0]


def _snapshot(qc):
    """Everything about the lane VOLUME these tests can disturb."""
    prm = _lane(qc).params[0]
    return {
        "expression": prm.expression,
        "minimum": prm.expression_min,
        "maximum": prm.expression_max,
        # The pedal MOVES the volume, so the assignment alone is not the state.
        "value": prm.param_values[0].float_value if prm.param_values
                 else UNITY_LEVEL,
    }


def _restore(qc, was):
    if was["expression"]:
        qc.set_expression(LaneOutput(ROW), PARAM, pedal=was["expression"],
                          minimum=Encoded(was["minimum"]),
                          maximum=Encoded(was["maximum"]))
    else:
        qc.clear_expression(LaneOutput(ROW), PARAM)
    qc.set_param(LaneOutput(ROW), PARAM, Encoded(was["value"]))


def _assignment(qc):
    """What the reader reports for this lane's VOLUME, or ``None``.

    Filtered on the target rather than on a parameter index, deliberately. The
    catalog's index is a WIRE index and the reader hands back a POSITION, and
    this whole change exists because those are not the same question - so
    matching one against the other here would quietly assume what the reader
    denies. A lane output has one VOLUME; the target is enough to find it.
    """
    found = [a for a in protocol.expression_assignments(qc.read_current_preset())
             if isinstance(a.target, LaneOutput) and a.target.row == ROW]
    return found[0] if found else None


def test_an_assignment_written_reads_back_the_same(qc, restores):
    was = _snapshot(qc)
    restores(f"row {ROW} lane {PARAM} expression", lambda: _restore(qc, was))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=2,
                      minimum=Encoded(0.15), maximum=Encoded(0.85))
    time.sleep(SETTLE)

    now = _assignment(qc)
    assert now is not None, "the assignment was written and did not read back"
    assert now.pedal == 2
    assert float(now.minimum) == pytest.approx(0.15, abs=1e-4)
    assert float(now.maximum) == pytest.approx(0.85, abs=1e-4)
    assert not now.reversed


def test_a_reversed_sweep_survives_the_round_trip(qc, restores):
    """The pair is not ordered - min above max inverts the pedal, and the unit
    stores it that way round. A reader that sorted would lose the setting."""
    was = _snapshot(qc)
    restores(f"row {ROW} lane {PARAM} expression", lambda: _restore(qc, was))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=1,
                      minimum=Encoded(0.9), maximum=Encoded(0.1))
    time.sleep(SETTLE)

    now = _assignment(qc)
    assert now is not None
    assert now.reversed, "the unit stored the sweep and the reader sorted it"


def test_clearing_makes_the_assignment_absent_rather_than_pedal_zero(qc, restores):
    """`expression: 0` is what a clear writes, and the device SENDS it - so the
    reader has to read a present zero as "no assignment" rather than as a pedal
    named zero."""
    was = _snapshot(qc)
    restores(f"row {ROW} lane {PARAM} expression", lambda: _restore(qc, was))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=1)
    time.sleep(SETTLE)
    assert _assignment(qc) is not None

    qc.clear_expression(LaneOutput(ROW), PARAM)
    time.sleep(SETTLE)
    assert _assignment(qc) is None


def test_the_off_detent_reads_as_off_rather_than_minus_forty(qc, restores):
    """The bug the offline suite could not see until its catalog grew knobs.

    A full sweep's heel is wire 0.0, which on a lane VOLUME is the OFF detent
    and NOT -40 dB - the law's bottom, a value `to_normalized` refuses. This is
    the same claim against the real catalog the unit hands over.
    """
    from pyquadcortex.device.translate import grid as translate_grid

    was = _snapshot(qc)
    restores(f"row {ROW} lane {PARAM} expression", lambda: _restore(qc, was))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=1,
                      minimum=Encoded(0.0), maximum=Encoded(1.0))
    time.sleep(SETTLE)

    reported = translate_grid.expression_assignments(
        qc.read_current_preset(), qc.catalog)
    mine = [r for r in reported if r[0] == ROW + 1 and r[1] is None]
    assert mine, "the lane assignment did not come back through the model"
    _row, _slot, name, _pedal, minimum, maximum, units, min_off, max_off, real = mine[0]
    assert name == PARAM and units == "dB"
    assert min_off, "wire 0.0 on a lane VOLUME is OFF, not -40 dB"
    assert not max_off and float(maximum) == pytest.approx(12.0)
    assert not real, "an OFF end is not a value in the knob's units"

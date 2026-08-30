"""Reading back an expression assignment, against the real unit.

The offline tests read committed fixtures, which prove the reader against bytes
the device once produced. This proves the loop: write an assignment with
`set_expression`, read it back with `expression_assignments`, and get the same
thing - which is the one claim a fixture cannot make, because a fixture cannot
notice if the wire shape changes under us.

State-neutral per ADR-0005: the assignment is snapshotted and restored.
"""

import time

import pytest

from pyquadcortex import protocol
from pyquadcortex.protocol.targets import LaneOutput
from pyquadcortex.protocol.values import Encoded

SETTLE = 2.0
ROW = 0
PARAM = "VOLUME"


def _assignment(qc, row, param_index):
    """What the unit currently reports for one lane output parameter."""
    found = [a for a in protocol.expression_assignments(qc.read_current_preset())
             if isinstance(a.target, LaneOutput)
             and a.target.row == row and a.param_index == param_index]
    return found[0] if found else None


def test_an_assignment_written_reads_back_the_same(qc, restores):
    index = qc.catalog[LaneOutput(ROW).model_id].parameter(PARAM).index
    before = _assignment(qc, ROW, index)

    def put_back():
        if before is None:
            qc.clear_expression(LaneOutput(ROW), index)
        else:
            qc.set_expression(LaneOutput(ROW), index, pedal=before.pedal,
                              minimum=Encoded(before.minimum),
                              maximum=Encoded(before.maximum))

    restores(f"row {ROW} lane {PARAM} expression", put_back)

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=2,
                      minimum=Encoded(0.15), maximum=Encoded(0.85))
    time.sleep(SETTLE)

    now = _assignment(qc, ROW, index)
    assert now is not None, "the assignment was written and did not read back"
    assert now.pedal == 2
    assert float(now.minimum) == pytest.approx(0.15, abs=1e-4)
    assert float(now.maximum) == pytest.approx(0.85, abs=1e-4)
    assert not now.reversed


def test_a_reversed_sweep_survives_the_round_trip(qc, restores):
    """The pair is not ordered - min above max inverts the pedal, and the unit
    stores it that way round. A reader that sorted would lose the setting."""
    index = qc.catalog[LaneOutput(ROW).model_id].parameter(PARAM).index
    before = _assignment(qc, ROW, index)
    restores(
        f"row {ROW} lane {PARAM} expression",
        lambda: (qc.clear_expression(LaneOutput(ROW), index) if before is None
                 else qc.set_expression(LaneOutput(ROW), index,
                                        pedal=before.pedal,
                                        minimum=Encoded(before.minimum),
                                        maximum=Encoded(before.maximum))))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=1,
                      minimum=Encoded(0.9), maximum=Encoded(0.1))
    time.sleep(SETTLE)

    now = _assignment(qc, ROW, index)
    assert now is not None
    assert now.reversed, "the unit stored the sweep and the reader sorted it"


def test_clearing_makes_the_assignment_absent_rather_than_pedal_zero(qc, restores):
    """`expression: 0` is what a clear writes, and the reader must read that as
    "no assignment" rather than reporting a pedal named zero."""
    index = qc.catalog[LaneOutput(ROW).model_id].parameter(PARAM).index
    before = _assignment(qc, ROW, index)
    restores(
        f"row {ROW} lane {PARAM} expression",
        lambda: (qc.clear_expression(LaneOutput(ROW), index) if before is None
                 else qc.set_expression(LaneOutput(ROW), index,
                                        pedal=before.pedal,
                                        minimum=Encoded(before.minimum),
                                        maximum=Encoded(before.maximum))))

    qc.set_expression(LaneOutput(ROW), PARAM, pedal=1)
    time.sleep(SETTLE)
    assert _assignment(qc, ROW, index) is not None

    qc.clear_expression(LaneOutput(ROW), PARAM)
    time.sleep(SETTLE)
    assert _assignment(qc, ROW, index) is None

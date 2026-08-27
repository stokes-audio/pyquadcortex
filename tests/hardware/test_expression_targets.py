"""Assigning an expression pedal to each kind of target, on the real unit.

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

from pyquadcortex.protocol.client import blocks
from pyquadcortex.protocol.errors import ControlNotDrivable
from pyquadcortex.protocol.targets import (Block, LaneInput, LaneOutput,
                                           Mixer, Splitter)
from pyquadcortex.protocol.units import UNITY_LEVEL, db_to_lane_level

#: A read straight after a write returns the PREVIOUS value on this firmware.
#: This trap has produced two wrong conclusions in this project, one of which
#: stood as a documented fact for several releases, so every read-back here
#: settles first rather than trusting an immediate reply.
SETTLE = 3.0

#: The lane whose Lane Output Control these tests drive. Chosen by the same rule
#: the rest of the suite uses: touch as little as possible. Any row carries one.
#:
#: A preset READ returns chains POSITIONALLY - `chain.row` is absent from every
#: one of them - so this index is also the wire row the writes below are keyed
#: to. That equivalence is observed, not guaranteed by the schema: writes keyed
#: `row=N` throughout this session appeared at `chains[N]`. `_reads_and_writes_
#: agree_on_which_row` pins it, because if it ever stopped holding these tests
#: would read one lane and restore another, and still pass.
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
        qc.set_expression(
            row=row, param="VOLUME", pedal=was["expression"],
            minimum=was["minimum"], maximum=was["maximum"])
    else:
        qc.clear_expression(LaneOutput(row), param="VOLUME")
    time.sleep(0.3)
    qc.set_param(LaneOutput(row), param="VOLUME", value=was["value"])


def test_reads_and_writes_agree_on_which_row(qc, restores):
    """The chain INDEX a read returns is the row number a write is keyed to.

    Every other test here reads `chains[ROW]` and writes `row=ROW`. If those ever
    named different lanes the suite would read one and restore another, pass
    cleanly, and leave the unit edited - the exact failure ADR-0005 exists to
    stop. So prove it once, with a value no other lane shares.
    """
    was = _snapshot(qc)
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    landmark = 0.3125                       # not a default, not unity, not 0.71
    qc.set_param(LaneOutput(ROW), param="VOLUME", value=landmark)
    time.sleep(SETTLE)

    chains = qc.read_current_preset().chains
    hits = [n for n, c in enumerate(chains)
            if c.output_control
            and c.output_control[0].params[0].param_values
            and abs(c.output_control[0].params[0].param_values[0].float_value
                    - landmark) < 1e-4]
    assert hits == [ROW], (
        f"a write keyed row={ROW} landed at chain index(es) {hits}; reads and "
        f"writes disagree about which lane is which")


def test_a_pedal_assigns_to_the_lane_volume_and_clears_again(qc, restores):
    """The whole round trip: assign, read back, clear, read back."""
    was = _snapshot(qc)
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    qc.set_expression(LaneOutput(ROW), param="VOLUME", pedal=1,
                                  minimum=0.0, maximum=0.6)
    time.sleep(SETTLE)
    p = _volume(qc)
    assert p.expression == 1, "the pedal did not take"
    assert p.expression_min == pytest.approx(0.0, abs=1e-4)
    assert p.expression_max == pytest.approx(0.6, abs=1e-4)

    qc.clear_expression(LaneOutput(ROW), param="VOLUME")
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

    qc.set_expression(LaneOutput(ROW), param="VOLUME", pedal=1)
    time.sleep(SETTLE)
    assert _volume(qc).scene_mode == before, "the assignment moved scene_mode"


@pytest.mark.parametrize("param", ["MUTE", "SOLO"])
def test_the_two_unassignable_parameters_refuse_rather_than_failing_silently(qc, param):
    """The device drops these without a word. The library must not.

    Nothing is written, so this needs no restore - which is the point: a refusal
    that reached the wire would be the bug.
    """
    with pytest.raises(ControlNotDrivable):
        qc.set_expression(LaneOutput(ROW), param=param)
    with pytest.raises(ControlNotDrivable):
        qc.clear_expression(LaneOutput(ROW), param=param)


def test_the_lane_volume_speaks_dB_through_real(qc, restores):
    """`real=` converts through the MEASURED -40..+12 dB span.

    The catalog publishes this parameter as ``0..1 "dB"`` - a placeholder - so
    the conversion cannot come from there. It is the one placeholder parameter
    whose true span has been measured at both ends; every other one still
    refuses `real=`.
    """
    was = _snapshot(qc)
    restores(f"row {ROW} lane VOLUME", lambda: _restore(qc, was))

    qc.set_param(LaneOutput(ROW), param="VOLUME", real=-3.1)
    time.sleep(SETTLE)
    assert _volume(qc).param_values[0].float_value == pytest.approx(
        db_to_lane_level(-3.1), abs=2e-4)

    qc.set_param(LaneOutput(ROW), param="VOLUME", real=0.0)
    time.sleep(SETTLE)
    assert _volume(qc).param_values[0].float_value == pytest.approx(
        UNITY_LEVEL, abs=2e-4), "0 dB is not unity"


# -- every target takes a pedal, and only two parameters refuse ---------------
#
# Measured one write per target and read back: blocks, the input gate, the
# mixer, the splitter and the lane output all accept an assignment, on float
# AND switch-typed parameters. Parameter type is irrelevant - the manual gives
# every assignable parameter a MIN/MAX sweep. These cases keep that true.
#
# Row 1 is the scratch lane. The mixer and splitter live on even rows only and
# are dormant in any serial preset, so writing their parameters is inaudible.

ASSIGNABLE = [
    ("lane input, float", LaneInput(1), "NOISE REDUCTION"),
    ("lane input, switch", LaneInput(1), "BYPASS"),
    ("lane output, float", LaneOutput(1), "VOLUME"),
    ("mixer, float", Mixer(0), "LEVEL A"),
    ("mixer, switch", Mixer(0), "PHASE"),
    ("splitter, float", Splitter(0), "LEVEL TO A"),
    ("splitter, switch", Splitter(0), "TYPE"),
]


def _params(qc, target):
    """The target's parameters as the unit currently reports them."""
    chain = qc.read_current_preset().chains[target.row]
    return getattr(chain, target.collection)[0].params


@pytest.mark.parametrize("label,target,name", ASSIGNABLE,
                         ids=[c[0] for c in ASSIGNABLE])
def test_every_target_takes_an_expression_pedal(qc, restores, label, target, name):
    index = qc.catalog[target.model_id].parameter(name).index
    was = _params(qc, target)[index]
    before = (was.expression, was.expression_min, was.expression_max)

    def restore():
        if before[0]:
            qc.set_expression(target, index, pedal=before[0],
                              minimum=before[1], maximum=before[2])
        else:
            qc.clear_expression(target, index)

    restores(f"{label} {name} expression", restore)

    qc.set_expression(target, name, pedal=1, minimum=0.15, maximum=0.85)
    time.sleep(SETTLE)
    now = _params(qc, target)[index]
    assert now.expression == 1, f"{label}: {name} did not take the pedal"
    assert now.expression_max == pytest.approx(0.85, abs=1e-3)

    qc.clear_expression(target, name)
    time.sleep(SETTLE)
    assert _params(qc, target)[index].expression == 0, (
        f"{label}: {name} did not clear")


def test_a_block_switch_parameter_takes_one_too(qc, restores):
    """The case that disproved "switch parameters are refused".

    Finds a real block with a `switch`-typed parameter rather than naming one,
    because which blocks are on the grid depends on the loaded preset.
    """
    grid = qc.read_current_preset()
    found = None
    for block in blocks(grid):
        spec = next((p for p in qc.catalog[block.model_id].parameters
                     if p.type == "switch"), None)
        if spec is not None:
            found = (block, spec)
            break
    if found is None:
        pytest.skip("no block on this preset has a switch-typed parameter")
    block, spec = found

    was = qc.read_current_preset().chains[block.row].models[block.column] \
            .params[spec.index]
    before = was.expression
    restores(f"{block.describe()} {spec.name} expression",
             lambda: qc.clear_expression(block, spec.index))

    assert before == 0, "pick a preset where this parameter is unassigned"
    qc.set_expression(block, spec.name, pedal=1, minimum=0.2, maximum=0.7)
    time.sleep(SETTLE)
    now = qc.read_current_preset().chains[block.row].models[block.column] \
            .params[spec.index]
    assert now.expression == 1, (
        f"{spec.name} is a {spec.type} and the device took the assignment - "
        f"being a switch is NOT what makes a parameter unassignable")


# -- expAssignable is the unit's OWN rule, not one the host is held to ---------


def test_a_parameter_the_catalog_calls_unassignable_still_takes_a_host_pedal(
        qc, restores):
    """The catalog marks 14 parameters ``expAssignable="false"``. It is advice.

    ADR-0010's differential capture, run 2026-08-26 on a Pattern Tremolo placed
    for the purpose. ``STEPS`` (index 10) is one of the 14; ``DEPTH`` (index 4)
    is not, and sits in the same block, so a refusal would show as a DIFFERENCE
    between two writes in one session rather than as an absence.

    Both took the pedal, identically, and both survived a disconnect and a fresh
    read: (1, 0.15, 0.85). So the flag does not govern a host write, and it is
    published as information rather than turned into a refusal.

    What it most likely governs is the unit's own assignment UI - which knobs
    the touchscreen offers - and that is a guess, which is exactly why nothing
    in the library acts on it. Whether the unit ACTS on the stored assignment is
    also unknown: proving that needs audio, not a wire read.

    This test exists so the next person does not spend a session rediscovering
    that the obvious refusal is not there.
    """
    cell = Block(1, 1, 7040)          # Pattern Tremolo on the scratch lane
    was = [b for b in blocks(qc.read_current_preset())
           if b.row == 1 and b.column == 1]
    restores("Pattern Tremolo scratch block",
             lambda: qc.set_block(was[0], verify=False) if was
             else qc.remove_block(Block(1, 1)))

    qc.set_block(cell, verify=False)
    time.sleep(SETTLE)

    steps = qc.catalog[7040].parameters[10]
    depth = qc.catalog[7040].parameters[4]
    assert (steps.name, steps.exp_assignable) == ("STEPS", False)
    assert (depth.name, depth.exp_assignable) == ("DEPTH", True)

    for index in (4, 10):
        qc.set_expression(cell, index, pedal=1, minimum=0.15, maximum=0.85)
    time.sleep(SETTLE)

    got = _block_params(qc, 1, 1)
    for index, spec in ((4, depth), (10, steps)):
        assert got[index].expression == 1, (
            f"{spec.name} did not take the pedal, which would make "
            f"expAssignable enforceable after all - update ADR-0007")
        assert got[index].expression_max == pytest.approx(0.85, abs=1e-3)


def _block_params(qc, row, column):
    """A grid block's parameters as the unit currently reports them."""
    preset = qc.read_current_preset()
    for i, chain in enumerate(preset.chains):
        if i != row:
            continue
        for j, model in enumerate(chain.models):
            if j == column:
                return model.params
    raise AssertionError(f"no block at row {row} column {column}")

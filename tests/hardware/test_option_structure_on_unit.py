"""The catalog predicts the WIRE for every option, on everything reachable.

This is the evidence the audit policy rests on. `CLAUDE.md` says structural
facts about an option list - how many choices, in what order, and which wire
value selects each - are taken from the catalog without a screen reading, while
anything about what a PERSON SEES needs eyes. That split is only defensible
while the structural half keeps being right.

So this drives every position of every fixed list the loaded preset can reach
and asserts the unit stores ``index / (count - 1)``. It found 0 mismatches in
279 positions on 2026-09-15, and the option audit drove about 40 more before
that. A failure here is a finding about the device and it invalidates the
policy, not just this test.

It proves the WIRE mapping and nothing else. The ORDER the unit draws a list's
choices in is presentational, this reads no screen, and `docs/domain-model.md`
records three lists that came back reversed when somebody transcribed them - so
a green run here never licenses skipping a reading.

It is batched: every parameter is set to position k, then ONE preset read
verifies them all, so the cost is one read per position rather than one per
parameter per position. On a full preset that is about 75 seconds.

State-neutral by recall: nothing here saves, so the teardown reloads the preset
and the grid returns to what the owner had. It refuses to start on a preset that
already has unsaved edits, because a recall would discard the owner's own work.
"""
import time

import pytest

import pyquadcortex.protocol as pq
from pyquadcortex.protocol import catalog as catalog_module
from pyquadcortex.protocol import values

# The one spelling of "the restore did not finish" (ADR-0005). Imported rather
# than re-worded, because it is an instruction to the owner about their own
# unit and `tests/test_hardware_report.py` counts the spellings.
from tests.hardware.conftest import _unrestored

#: How long to let a batch of writes settle before reading the preset back.
SETTLE = 0.8

#: Between individual writes. The unit accepts them faster than this, but a
#: whole preset's worth back to back is not a shape anything else exercises.
BETWEEN = 0.12


def _targets(preset, catalog):
    """Every FIXED list parameter on the loaded preset, with its labels."""
    found = []
    for block in pq.blocks(preset):
        model = catalog[block.model_id]
        for index, spec in enumerate(model.parameters):
            # Two or more: `index / (count - 1)` has nothing to say about a
            # one-option list, and it would divide by zero. None exists on
            # CorOS 4.0.1, but this parses whichever catalog is attached.
            if len(spec.options) > 1 and not spec.dynamic:
                found.append((block, index, tuple(spec.options),
                              model.name, spec.name))
    return found


@pytest.fixture
def restored(qc):
    """Reload the preset afterwards, which undoes every write this made.

    A recall DISCARDS unsaved edits, so this refuses to run at all on a preset
    that already has some - they would be the owner's, and nothing here could
    put them back.
    """
    assert qc.preset_dirty(timeout=15.0) is False, (
        "the loaded preset already has unsaved edits. This test restores by "
        "recalling, which would throw them away - save or reload the preset on "
        "the unit and run again.")
    before = qc.loaded_position()
    yield
    failed = []
    try:
        # Recalling the SAME slot does not reload it: the unit sees no change
        # and does nothing. So this recalls a DIFFERENT slot first and comes
        # back. `position` is a linear slot index, not an offset, so the other
        # slot is 0 or 1 rather than a neighbour - which is fine, any slot the
        # unit actually loads will do, and the check below is what proves one
        # did rather than the comment claiming it.
        other = 1 if before.position != 1 else 0
        qc.recall_preset(before.folder_key, other, is_factory=before.is_factory)
        time.sleep(6.0)
        qc.recall_preset(before.folder_key, before.position,
                         is_factory=before.is_factory)
        time.sleep(8.0)
        now = qc.loaded_position()
        if now.position != before.position:
            failed.append(f"the unit is on slot {now.position}, not "
                          f"{before.position} where it started")
        elif qc.preset_dirty(timeout=15.0):
            # Back on the right slot and still dirty means the reload was a
            # no-op - the neighbour slot was probably empty - and every write
            # this test made is still on the owner's grid.
            failed.append(f"slot {before.position} is still showing unsaved "
                          f"edits, so the reload did not take and this test's "
                          f"writes are still on the grid")
    except Exception as exc:                       # noqa: BLE001 - reported
        failed.append(f"the restore raised: {exc}")
    if failed:
        raise _unrestored(failed)


@pytest.mark.verifies("set_param", "read_current_preset")
def test_every_option_position_lands_where_the_catalog_says(qc, restored):
    """`index / (count - 1)`, driven on every fixed list this preset reaches."""
    live = catalog_module.parse_model_repo(qc._fetch_model_repo())

    preset = qc.read_current_preset()
    targets = _targets(preset, live)
    assert targets, "the loaded preset has no fixed-list parameters to drive"

    # set_param without `scene=` writes the ACTIVE scene, so the read has to
    # look at the same slot rather than assuming scene A.
    active = int(qc.active_scene())
    longest = max(len(labels) for _, _, labels, _, _ in targets)
    checked = 0
    wrong = []
    for position in range(longest):
        wrote = []
        for block, index, labels, model_name, param_name in targets:
            if position >= len(labels):
                continue
            # Encoded: a list position is a wire index, not a screen value.
            qc.set_param(block, index,
                         values.Encoded(position / (len(labels) - 1)))
            wrote.append((block, index, labels, model_name, param_name))
            time.sleep(BETWEEN)
        if not wrote:
            continue
        time.sleep(SETTLE)
        now = qc.read_current_preset()
        for block, index, labels, model_name, param_name in wrote:
            state = pq.param_state(now, block, index)
            # The write went to the ACTIVE scene, so read that slot - slot 0 is
            # scene A and is the wrong answer on any other scene for a parameter
            # that follows scenes. A slot can also hold None, or the NaN factory
            # content leaves in unmaintained slots, and float()/round() raise on
            # those rather than reporting a mismatch.
            slot = active if state.scene_mode else 0
            stored = state.values[slot] if slot < len(state.values) else None
            if stored is None or stored != stored:
                wrong.append(f"{model_name} {param_name}: scene slot {slot} "
                             f"holds {stored!r}, so nothing can be compared")
                checked += 1
                continue
            got = round(float(stored) * (len(labels) - 1))
            checked += 1
            if got != position:
                wrong.append(f"{model_name} {param_name}: asked {position} of "
                             f"{len(labels)}, unit stored {got} (wire {stored})")

    # The vacuity floor is judged FIRST. A thin run that also found a mismatch
    # would otherwise report the mismatch and never say the run proved little.
    assert checked >= 100, (
        f"only {checked} positions were driven; this preset reaches too little "
        f"to say anything about the catalog. Load one with more blocks. "
        + (f"({len(wrong)} of them also mismatched: {wrong[:3]})" if wrong else ""))
    assert not wrong, (
        f"{len(wrong)} of {checked} option positions did not land where the "
        f"catalog says: {wrong[:5]}. This is the claim CLAUDE.md's rule rests "
        f"on - that a choice's WIRE INDEX comes from the catalog without a "
        f"screen reading - so a failure here reopens the rule, not just this "
        f"test.")
    print(f"\n  {checked} option positions driven across {len(targets)} "
          f"parameters, all landed where the catalog says")

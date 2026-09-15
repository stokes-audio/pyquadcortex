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

It is batched: every parameter is set to position k, then ONE preset read
verifies them all, so the cost is one read per position rather than one per
parameter per position. On a full preset that is about 90 seconds.

State-neutral by recall: nothing here saves, so the teardown reloads the preset
and the grid returns to what the owner had.
"""
import time

import pytest

import pyquadcortex.protocol as pq
from pyquadcortex.protocol import values

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
            if spec.options and not spec.dynamic:
                found.append((block, index, tuple(spec.options),
                              model.name, spec.name))
    return found


@pytest.fixture
def restored(qc):
    """Reload the preset afterwards, which undoes every write this made."""
    yield
    position = qc.loaded_position()
    folder = position.folder_key
    # Recalling the SAME slot does not reload it - the unit sees no change - so
    # this bounces off a neighbour and comes back.
    qc.recall_preset(folder, 1 if position.position != 1 else 2,
                     is_factory=position.is_factory)
    time.sleep(6.0)
    qc.recall_preset(folder, position.position, is_factory=position.is_factory)
    time.sleep(8.0)


@pytest.mark.verifies("set_param", "read_current_preset")
def test_every_option_position_lands_where_the_catalog_says(qc, restored):
    """`index / (count - 1)`, driven on every fixed list this preset reaches."""
    from pyquadcortex.protocol import catalog as catalog_module
    live = catalog_module.parse_model_repo(qc._fetch_model_repo())

    preset = qc.read_current_preset()
    targets = _targets(preset, live)
    assert targets, "the loaded preset has no fixed-list parameters to drive"

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
            stored = float(state.values[0]) if state.values else None
            got = (round(stored * (len(labels) - 1))
                   if stored is not None else None)
            checked += 1
            if got != position:
                wrong.append(f"{model_name} {param_name}: asked {position} of "
                             f"{len(labels)}, unit stored {got} (wire {stored})")

    assert not wrong, (
        f"{len(wrong)} of {checked} option positions did not land where the "
        f"catalog says: {wrong[:5]}. This is the claim CLAUDE.md's audit policy "
        f"rests on - that structure comes from the catalog without a screen "
        f"reading - so a failure here reopens the policy, not just this test.")
    # A run that drove almost nothing would pass while proving almost nothing.
    assert checked >= 100, (
        f"only {checked} positions were driven; this preset reaches too little "
        f"to say anything about the catalog. Load one with more blocks.")
    print(f"\n  {checked} option positions driven across {len(targets)} "
          f"parameters, all landed where the catalog says")

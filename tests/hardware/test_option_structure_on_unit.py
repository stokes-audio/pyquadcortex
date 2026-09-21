"""The catalog predicts the WIRE for every option, on everything reachable.

This is the evidence the rule rests on. `CLAUDE.md` says a structural fact about
an option list - how many choices it has and WHICH WIRE INDEX each one sits at -
is taken from the catalog without a screen reading, while anything about what a
PERSON SEES needs eyes. That split is only defensible while the structural half
keeps being right.

So this drives every position of every fixed list the loaded preset can reach
and asserts the unit stores ``index / (count - 1)``. 156 positions across 29
parameters on the preset of the 2026-09-15 run, zero mismatches. A failure here
is a finding about the device and it invalidates the rule, not just this test.

It proves the WIRE mapping and nothing else. The ORDER the unit draws a list's
choices in is presentational, this reads no screen, and `docs/domain-model.md`
records three lists that came back reversed when somebody transcribed them - so
a green run here never licenses skipping a reading.

It is batched: every parameter is set to position k, then ONE preset read
verifies them all, so the cost is one read per position rather than one per
parameter per position. On a full preset that is about 73 seconds.

State-neutral by recall: nothing here saves, so the teardown reloads the preset
and the grid returns to what the owner had. It refuses to start on a preset that
already has unsaved edits, because a recall would discard the owner's own work.
"""
import time

import pytest

import pyquadcortex.protocol as pq
from pyquadcortex.protocol import catalog as catalog_module
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
            # Two or more: `index / (count - 1)` has nothing to say about a
            # one-option list, and it would divide by zero. None exists on
            # CorOS 4.0.1, but this parses whichever catalog is attached.
            if len(spec.options) > 1 and not spec.dynamic:
                found.append((block, index, tuple(spec.options),
                              model.name, spec.name))
    return found


@pytest.fixture(scope="module")
def live_catalog(qc):
    """One ModelRepo fetch for this module.

    It is ~47 KB over USB and a full XML parse. `test_scales_on_unit.py` records
    why a redundant one matters: the traffic lands in front of the connect-burst
    tests, which time how quickly the handshake fills the cache, and two of them
    began failing in a full run while passing alone as that grew.
    """
    return catalog_module.parse_model_repo(qc._fetch_model_repo())


@pytest.fixture
def restored(restores, preset_dirty_at_start, reload_the_loaded_preset):
    """Reload the preset afterwards, which undoes every write this made.

    Goes through the `restores` fixture rather than doing its own teardown: that
    is where ADR-0005's one spelling of "COULD NOT RESTORE THE UNIT" lives, and
    a second copy of it here would be a second one to keep right. An earlier
    version imported `_unrestored` from conftest directly, which loads conftest
    a SECOND time under another module name - harmless for a pure function and a
    trap for anything stateful beside it.

    A recall DISCARDS unsaved edits, so this asks WHOSE they are rather than
    whether there are any. Edits present before the session started are the
    owner's and nothing here could put them back. Edits that appear during the
    run are this suite's own: the modules that write a grid value restore the
    value and leave the preset marked edited, and both of them sort ahead of
    this one, so asking `preset_dirty()` here refused every full-suite run.
    """
    assert preset_dirty_at_start is False, (
        "the loaded preset already had unsaved edits when this session "
        "started. This test restores by recalling, which would throw them "
        "away - save or reload the preset on the unit and run again.")

    # One spelling of the recall-away-and-back dance, in conftest, because the
    # session teardown that puts the edited flag back needs the same one.
    restores("the loaded preset", reload_the_loaded_preset)
    yield


@pytest.mark.verifies("set_param", "read_current_preset")
def test_every_option_position_lands_where_the_catalog_says(qc, restored, live_catalog):
    """The position asked for is the position stored, on every list reachable.

    Asserted as "the stored wire rounds back to the index asked for" rather than
    as exact equality with ``index / (count - 1)``: the unit quantizes, and on a
    two-option list that tolerance is half the range. What this establishes is
    that the catalog's POSITION is the position you get, which is the claim the
    rule makes - not that the float is bit-exact.
    """
    preset = qc.read_current_preset()
    targets = _targets(preset, live_catalog)
    assert targets, "the loaded preset has no fixed-list parameters to drive"

    # set_param without `scene=` writes the ACTIVE scene, so the read has to
    # look at the same slot rather than assuming scene A.
    active = int(qc.active_scene())
    longest = max(len(labels) for _, _, labels, _, _ in targets)
    checked = 0
    wrong = []
    # Kept apart from `wrong` on purpose. A slot that cannot be read is not a
    # mismatch, and reporting it as one raises an alarm whose text says the
    # catalog's wire mapping is wrong - the same false-alarm shape as reading
    # the wrong scene slot did.
    unreadable = []
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
            # `param_state` can hand back a string (a capture file name, an IR
            # reference) as well as None and the NaN factory content leaves in
            # unmaintained slots. All three are "nothing to compare", and
            # float() would turn the last two into a traceback instead.
            if stored is None or isinstance(stored, str) or stored != stored:
                unreadable.append(f"{model_name} {param_name}: scene slot "
                                  f"{slot} holds {stored!r}")
                continue
            got = round(float(stored) * (len(labels) - 1))
            checked += 1
            if got != position:
                wrong.append(f"{model_name} {param_name}: asked {position} of "
                             f"{len(labels)}, unit stored {got} (wire {stored})")

    assert not unreadable, (
        f"{len(unreadable)} scene slot(s) held nothing comparable: "
        f"{unreadable[:5]}. That is not a mismatch and says nothing about the "
        f"catalog - it means this test could not read what it wrote, so fix "
        f"the reading before drawing any conclusion from the run.")
    # The vacuity floor is judged BEFORE the mismatch list. A thin run that also
    # found a mismatch would otherwise report the mismatch and never say the run
    # proved little.
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


def test_the_displayPos_counts_the_docs_quote_still_hold(live_catalog):
    """`display_pos` is published on two screen readings, so pin what is countable.

    The readings themselves cannot be re-taken without eyes. What CAN be checked
    is the population they were generalised over - and four triage passes on
    this change each found a number wrong somewhere, which is the argument for
    putting these under a test rather than in prose alone.

    Nothing offline can do it: the whole `ModelRepo.xml` is not committed, and
    `tests/fixtures/catalog/scales.json` carries raw attributes for only a few
    dozen parameters. So it happens here, against the catalog the unit is
    actually running. A failure is a finding about the device, and it means the
    numbers in `CLAUDE.md`, `docs/STEERING.md`, `docs/domain-model.md` and
    `changelog.md` need re-deriving before anything else is trusted.
    """
    placeable = [m for m in live_catalog
                 if not (m.hidden or m.internal or m.category_hidden)]

    def placed(params):
        return [p for p in params if p.display_pos is not None]

    def disagrees(params):
        # By INDEX, not by name. Parameter names repeat inside a model - an IR
        # loader's are why `scripts/generate_params.py` has a GROUPED table - so
        # comparing names would read a swap of two identically named controls as
        # agreement, and under-count the very figure this pins.
        put = placed(params)
        return ([p.index for p in put]
                != [p.index for p in sorted(put, key=lambda p: p.display_pos)])

    visible = {m.id: [p for p in m.parameters if not p.hidden]
               for m in placeable}
    every = {m.id: list(m.parameters) for m in placeable}

    counts = {
        "placeable": len(placeable),
        # the basis the docstring and CLAUDE.md quote
        "visible_placing_any": sum(1 for v in visible.values() if placed(v)),
        "visible_disagreeing": sum(1 for v in visible.values()
                                   if placed(v) and disagrees(v)),
        # the basis the changelog's sorting recipe operates on
        "all_placing_any": sum(1 for v in every.values() if placed(v)),
        "all_disagreeing": sum(1 for v in every.values()
                               if placed(v) and disagrees(v)),
        # These two are over the WHOLE catalog, not the placeable subset, which
        # is the denominator the docs quote for them ("331 of 533 models").
        # Pinning the total as well is the point: an earlier version pinned 331
        # and left `len(live)` free, so a catalog that grew while still having
        # 331 padded models would have passed green with 533 and the derived 202
        # going stale in four documents.
        "models": len(live_catalog),
        "with_resources": sum(1 for m in live_catalog if m.resources),
    }
    assert counts == {
        "placeable": 503,
        "visible_placing_any": 163,
        "visible_disagreeing": 142,
        "all_placing_any": 165,
        "all_disagreeing": 144,
        "models": 533,
        "with_resources": 331,
    }, (f"this unit's catalog gives {counts}, and the docs quote the values in "
        f"the assertion. Re-derive every display_pos and Padding figure in "
        f"CLAUDE.md, docs/STEERING.md, docs/domain-model.md and changelog.md.")

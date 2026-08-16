"""The model's preset surface, against a real unit.

The acceptance criterion this exists for: read a real preset through the model
and assert it matches what the protocol layer reports for the SAME preset -
blocks, positions, routing, splits and scene labels. Two accounts of one payload,
one in the wire's numbering and one in the screen's, held against each other.

The offline suite checks the same conversions against a fixture. What it cannot
check is that the fixture still looks like what the unit sends, which is the gap
this closes.

Read-only except for one test, which activates a scene and puts it back. Nothing
here saves anything, so a run that dies badly leaves nothing a preset recall does
not undo.
"""

import pytest

from pyquadcortex import protocol
from pyquadcortex.device import translate
from pyquadcortex.device.device import Device
from pyquadcortex.device.grid import SplittableRow


@pytest.fixture(scope="session")
def device(qc, model_cache):
    """The model, on the run's one connection and its already-warm cache.

    Not closed here. The cache belongs to the session fixture, and closing this
    would close it out from under every test that runs afterwards.
    """
    return Device(qc, _state=model_cache)


@pytest.fixture(scope="session")
def wire(qc):
    """The same preset, straight off the wire, read once."""
    return qc.read_current_preset()


# -- the model and the protocol layer agree about one real preset -------------


def test_the_model_reads_the_preset_the_unit_has_loaded(device, qc):
    assert device.preset is not None
    assert device.preset.name == qc.read_current_preset().name


def test_every_block_is_where_the_protocol_layer_says_it_is(device, wire):
    """Cell by cell, both ways. `blocks()` reports rows 0-3 and columns 0-7;
    the model reports rows 1-4 and slots 1-8. Nothing else may differ."""
    expected = {
        (translate.row_from_wire(b.row), translate.slot_from_wire(b.column)):
            b.model_id
        for b in protocol.blocks(wire)
    }
    assert expected, "the loaded preset holds no blocks - load one that does"

    found = {(block.row, block.slot): block.device.id
             for block in device.preset.blocks}
    assert found == expected


def test_the_grid_finds_the_same_blocks_cell_by_cell(device, wire):
    """The mapping above compared two collections. This asks the model for each
    cell by name, which is what a caller actually writes."""
    expected = {(translate.row_from_wire(b.row), translate.slot_from_wire(b.column))
                for b in protocol.blocks(wire)}
    grid = device.preset.blocks
    for row in translate.ROWS:
        for slot in translate.SLOTS:
            block = grid[row, slot]
            if (row, slot) in expected:
                assert block is not None, f"row {row} slot {slot} reads empty"
            else:
                assert block is None, f"row {row} slot {slot} reads occupied"


def test_every_row_reports_the_routing_the_protocol_layer_does(device, wire):
    for row in device.preset.rows:
        chain = wire.chains[translate.row_to_wire(row.number)]
        expected_in = (protocol.Input(chain.in_portid)
                       if protocol.field_present(chain, "in_portid") else None)
        expected_out = (protocol.Output(chain.out_portid)
                        if protocol.field_present(chain, "out_portid") else None)
        assert row.input.source == expected_in, f"row {row.number} input"
        assert row.output.destination == expected_out, f"row {row.number} output"


def test_a_row_routed_into_another_row_shows_no_lane_output(device, wire):
    """As on screen. Skipped rather than faked if this preset has no such row.

    Rows are compared by NUMBER. `Row` defines no equality and `preset.rows`
    rebuilds its objects on every access, so `row in some_list_of_rows` compares
    identity across two different `Rows` instances and is always False - which
    is how the first version of this test asserted the opposite of itself in its
    second loop and never noticed, because the preset it ran against took the
    skip above.
    """
    into_a_row = (protocol.Output.NEXT_ROW_3, protocol.Output.NEXT_ROW_4,
                  protocol.Output.NEXT_ROW_3_4)
    routed = {row.number for row in device.preset.rows
              if row.output.destination in into_a_row}
    if not routed:
        pytest.skip("the loaded preset routes no row into another row")
    for row in device.preset.rows:
        if row.number in routed:
            assert row.output.lane is None, f"row {row.number} feeds a row"
        elif row.output.destination is not None:
            assert row.output.lane is not None, f"row {row.number} feeds a jack"


def test_every_split_matches_the_protocol_layer(device, wire):
    expected = {
        translate.row_from_wire(split.row): (
            translate.slot_from_wire(split.split_column),
            translate.slot_from_wire(split.mix_column) if split.rejoins else None,
            translate.row_from_wire(split.lane_row),
        )
        for split in protocol.splits(wire)
    }
    for row in device.preset.rows:
        if not isinstance(row, SplittableRow):
            continue
        if row.number not in expected:
            assert row.splitter is None and row.mixer is None
            continue
        at, rejoins_at, path_b = expected[row.number]
        assert row.splitter is not None and row.splitter.slot == at
        if rejoins_at is None:
            assert row.mixer is None
        else:
            assert row.mixer is not None and row.mixer.slot == rejoins_at
        assert row.path_b.number == path_b


def test_the_split_coverage_is_not_vacuous(wire):
    """The test above passes trivially on a preset with no branches. This says
    out loud whether the loaded preset exercised it."""
    if not protocol.splits(wire):
        pytest.skip("the loaded preset has no branch, so the split assertions "
                    "above proved nothing - load one that branches to cover it")


def test_every_scene_label_matches(device, wire):
    for scene in device.preset.scenes:
        stored = wire.scene_labels[translate.scene_to_wire(scene.letter)]
        expected = "" if not stored.strip() else stored
        assert scene.name == expected, f"scene {scene.letter}"


def test_the_active_scene_matches_what_the_unit_reports(device, qc):
    assert device.preset.scenes.active.letter == \
        translate.scene_from_wire(qc.active_scene())


def test_bypass_matches_the_protocol_layer_in_every_scene(device, wire):
    for block in device.preset.blocks:
        stored = protocol.bypass_state(wire,
                                       translate.row_to_wire(block.row),
                                       translate.slot_to_wire(block.slot))
        for scene in device.preset.scenes:
            through = scene.blocks[block.row, block.slot]
            assert through.bypassed is \
                stored.scenes[translate.scene_to_wire(scene.letter)], \
                f"row {block.row} slot {block.slot} scene {scene.letter}"


# -- the two bindings cannot disagree -----------------------------------------


def test_the_active_scene_s_two_bindings_agree(device):
    preset = device.preset
    active = preset.scenes.active
    for block in preset.blocks:
        through_scene = active.blocks[block.row, block.slot]
        assert block == through_scene
        assert block.device == through_scene.device
        assert block.bypassed == through_scene.bypassed


def test_an_inactive_scene_refuses_writes_and_reads_fine(device):
    from pyquadcortex.device.errors import InactiveSceneError

    preset = device.preset
    inactive = next(scene for scene in preset.scenes if not scene.is_active)
    assert not inactive.blocks.writable
    with pytest.raises(InactiveSceneError, match=r"scene\.activate\(\)"):
        inactive.blocks.check_writable()
    assert len(list(inactive.blocks)) == len(list(preset.blocks))


# -- the connect burst leaves the cache warm ----------------------------------


def test_the_burst_delivered_every_entry_the_preset_surface_reads(burst_warmed):
    """Measured 2026-08-15: the burst carries RecallPreset, SetlistPosition,
    PresetDirty and Scene at about 10 s, inside ten milliseconds. Identity is
    NOT in it - the unit never announces its own firmware - so that one is
    expected to be empty and is asserted, to keep this from passing on a run
    where the burst delivered nothing at all."""
    for name in ("preset", "scene", "dirty", "loaded"):
        assert burst_warmed[name], f"the burst delivered nothing for {name}"
    assert not burst_warmed["identity"], (
        "identity arrived in the burst, which contradicts what the entry's "
        "docstring says the unit does")


def test_reading_the_preset_surface_costs_no_round_trip(device, model_cache):
    """The NFR: nothing the burst already delivered is re-read.

    Counted at the transport, wrapping all three ways the model can ask, and the
    counter is proved able to see a read at the end of this file.
    """
    with counting(device) as asked:
        # device.preset is INSIDE the block. It is the only path that reads the
        # loaded slot, and that entry is the one where warmth is a real
        # assumption rather than a measurement: SetlistPosition has seven
        # presence-bearing fields and the entry keeps three, so if the burst's
        # push carries any of the other four the entry is marked on arrival and
        # every device.preset costs a round trip. An earlier version built the
        # preset outside the block and could not have seen that.
        preset = device.preset
        preset.has_unsaved_changes
        preset.is_current
        preset.name
        preset.scenes.active
    assert asked == [], f"the model asked the unit for {asked}"


def test_the_burst_push_carries_only_fields_the_loaded_entry_keeps(handshake_burst,
                                                                  model_cache):
    """The assumption the test above rests on, checked directly.

    If the unit's unsolicited SetlistPosition carried `is_downloads` or any of
    the other fields `_LOADED` does not keep, the entry would be marked the
    moment it arrived and the warmth claim would be false - so this asserts the
    entry came out of the burst trusted rather than merely populated.
    """
    assert model_cache.cached("loaded"), "the burst delivered no loaded slot"
    assert not model_cache.needs_read("loaded"), (
        "the burst's SetlistPosition named a field the loaded entry does not "
        "keep, so device.preset costs a round trip on every connection")


def test_the_read_counter_can_see_a_read(device, model_cache):
    """Every "asked == []" above rests on this."""
    model_cache.mark_for_reread("dirty", "proving the counter works")
    with counting(device) as asked:
        device.preset.has_unsaved_changes
    assert asked, "the counter saw nothing where a read certainly happened"


class counting:
    """Records every message the model sends while the block runs."""

    def __init__(self, device):
        self._device = device
        self._asked = []

    def __enter__(self):
        client = self._device.client
        self._real = client._t
        asked = self._asked

        class Counting:
            def __init__(self, inner):
                self._inner = inner

            def __getattr__(self, name):
                return getattr(self._inner, name)

            def send(self, message):
                asked.append(type(message).__name__)
                return self._inner.send(message)

            def request(self, message, *args, **kwargs):
                asked.append(type(message).__name__)
                return self._inner.request(message, *args, **kwargs)

            def await_broadcast(self, cls, trigger, *args, **kwargs):
                asked.append(cls.__name__)
                return self._inner.await_broadcast(cls, trigger, *args, **kwargs)

        client._t = Counting(self._real)
        return self._asked

    def __exit__(self, *exc):
        self._device.client._t = self._real
        return False


# -- the one write, and it puts the unit back ---------------------------------


def test_activating_a_scene_lands_and_is_confirmed(device, qc, restores):
    """The model's first write. Audible: the unit changes scene and changes
    back."""
    from pyquadcortex.device.watch import WatchOutcome

    preset = device.preset
    was = preset.scenes.active.letter
    restores(f"the active scene ({was})", lambda: qc.switch_scene(
        translate.scene_to_wire(was)))

    target = next(scene for scene in preset.scenes if scene.letter != was)
    watch = target.activate()
    assert watch.settled(timeout=5.0), "the unit never echoed the scene switch"
    assert watch.outcome is WatchOutcome.CONFIRMED, watch.disagreement
    assert translate.scene_from_wire(qc.active_scene()) == target.letter
    assert preset.scenes.active.letter == target.letter
    assert target.blocks.writable, "the scene we just switched to reads read-only"

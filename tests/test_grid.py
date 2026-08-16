"""The grid as the screen shows it: rows 1 to 4, slots 1 to 8, blocks in cells.

The doubles here stand in for a `Preset` deliberately. A block reads exactly
three things - the wire preset it came from, which scene the grid it was reached
through means, and the unit's catalogue of virtual devices - so a test that had
to build a whole connected device to check a slot number would be testing the
wiring instead of the numbering.
"""
import pathlib

import pytest

from pyquadcortex import protocol
from pyquadcortex.device import blocks as block_module
from pyquadcortex.device import errors
from pyquadcortex.device import grid as grid_module
from pyquadcortex.device.translate import SceneLetter
from pyquadcortex.protocol.proto import Preset_pb2 as preset_pb

PRESETS = pathlib.Path(__file__).parent / "fixtures" / "presets"


def load(name):
    payload = preset_pb.BinaryPreset()
    payload.ParseFromString((PRESETS / name).read_bytes())
    return payload


class FakeCatalog:
    """The unit's model repository, as far as a block is concerned."""

    def __getitem__(self, model_id):
        if model_id == 999999:
            raise KeyError(f"no model with id {model_id} in this device's catalog")
        return protocol.Model(id=model_id, name=f"device-{model_id}",
                              category="AMP", category_id=1)


class FakePreset:
    """A preset as far as a grid is concerned: a wire payload, a catalogue, and
    which scene is active right now."""

    def __init__(self, wire, active=SceneLetter.A):
        self.wire = wire
        self.catalog = FakeCatalog()
        self.active_scene = active


@pytest.fixture
def structural():
    return load("structural_preset.bin")


@pytest.fixture
def split():
    return load("split_preset.bin")


def live(wire, active=SceneLetter.A):
    """`preset.blocks` - bound to whichever scene is active at read time."""
    return grid_module.BlockGrid(FakePreset(wire, active))


def rows_of(wire, active=SceneLetter.A):
    return grid_module.Rows(live(wire, active))


# -- blocks read their cell ---------------------------------------------------


def test_a_device_block_reads_its_screen_position(structural):
    block = live(structural)[1, 1]
    assert block.row == 1 and block.slot == 1


def test_a_device_block_names_the_virtual_device(structural):
    block = live(structural)[1, 1]
    assert block.device.name == "device-18010"
    assert block.device.id == 18010
    assert block.device.category == "AMP"


def test_a_device_the_catalogue_does_not_have_says_so(structural):
    """The catalogue comes FROM the unit, so a miss is a real anomaly rather
    than something to paper over with a placeholder name."""
    structural.chains[0].models[0].hash = 999999
    with pytest.raises(KeyError, match="999999"):
        live(structural)[1, 1].device


def test_bypass_reads_through_the_grid_s_scene(structural):
    """Same cell, two bindings, two answers - which is the whole point of a
    binding. The fixture stores the same flag in every scene, so drive them
    apart first or this passes against a reader that ignores the scene."""
    cell = structural.bypass[0].colBypass[0]
    cell.sceneMode = True
    cell.sceneBypass[0].bypass = True
    cell.sceneBypass[1].bypass = False
    preset = FakePreset(structural, active=SceneLetter.A)
    in_a = grid_module.BlockGrid(preset, scene=SceneLetter.A)
    in_b = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    assert in_a[1, 1].bypassed is True
    assert in_b[1, 1].bypassed is False


def test_an_input_block_reads_its_source(structural):
    row = rows_of(structural)[1]
    assert row.input.source == protocol.Input.INPUT_1


def test_an_input_block_sits_outside_the_eight_slots(structural):
    assert rows_of(structural)[1].input.slot is None


def test_an_output_routed_to_another_row_has_no_lane(structural):
    """As on screen: LANE OUTPUT CONTROL is not shown for a row feeding a row."""
    output = rows_of(structural)[1].output
    assert output.destination == protocol.Output.NEXT_ROW_3
    assert output.lane is None


def test_an_output_routed_to_a_jack_has_a_lane(structural):
    output = rows_of(structural)[3].output
    assert output.destination == protocol.Output.MULTIPLE
    assert output.lane is not None


def test_two_handles_on_the_same_cell_compare_equal(structural):
    preset = FakePreset(structural)
    a = grid_module.BlockGrid(preset)[1, 1]
    b = grid_module.BlockGrid(preset, scene=SceneLetter.A)[1, 1]
    assert a == b
    assert hash(a) == hash(b)


def test_handles_on_different_cells_do_not(structural):
    grid = live(structural)
    assert grid[1, 1] != grid[1, 2]


def test_a_block_says_where_it_is_in_its_repr(structural):
    text = repr(live(structural)[1, 1])
    assert "row 1" in text and "slot 1" in text


# -- rows and slots, numbered as the screen numbers them ----------------------


def test_there_are_four_rows(structural):
    rows = rows_of(structural)
    assert len(rows) == 4
    assert [r.number for r in rows] == [1, 2, 3, 4]


@pytest.mark.parametrize("row", [0, 5, -1])
def test_a_row_the_screen_does_not_show_is_refused(structural, row):
    with pytest.raises(ValueError, match="1 to 4"):
        rows_of(structural)[row]


def test_rows_1_and_3_can_start_a_branch(structural):
    rows = rows_of(structural)
    assert isinstance(rows[1], grid_module.SplittableRow)
    assert isinstance(rows[3], grid_module.SplittableRow)


def test_rows_2_and_4_cannot(structural):
    """The type carries the rule, so `rows[2].create_split()` is something an
    editor rejects rather than something that raises at run time."""
    rows = rows_of(structural)
    assert not isinstance(rows[2], grid_module.SplittableRow)
    assert not isinstance(rows[4], grid_module.SplittableRow)
    assert not hasattr(rows[2], "splitter")
    assert not hasattr(rows[4], "path_b")


def test_a_row_reports_all_eight_slots_whether_or_not_they_hold_anything(structural):
    row = rows_of(structural)[3]
    assert len(row.slots) == 8
    assert list(row.slots)[0] is None, "the fixture's row 3 slot 1 is empty"
    assert list(row.slots)[1] is not None


def test_a_slot_reads_the_block_in_it(structural):
    assert rows_of(structural)[1].slots[1].device.id == 18010
    assert rows_of(structural)[1].slots[1].slot == 1


@pytest.mark.parametrize("slot", [0, 9, -1])
def test_a_slot_the_screen_does_not_show_is_refused(structural, slot):
    with pytest.raises(ValueError, match="1 to 8"):
        rows_of(structural)[1].slots[slot]


def test_an_empty_row_still_has_eight_slots(structural):
    row = rows_of(structural)[2]
    assert len(row.slots) == 8
    assert list(row.slots) == [None] * 8


def test_a_row_reads_its_input_and_output(structural):
    row = rows_of(structural)[1]
    assert row.input.source == protocol.Input.INPUT_1
    assert row.output.destination == protocol.Output.NEXT_ROW_3


# -- branches -----------------------------------------------------------------


def test_a_branch_that_never_rejoins_has_no_mixer(split):
    row = rows_of(split)[1]
    assert row.splitter is not None
    assert row.splitter.slot == 3, "wire column 2 is slot 3"
    assert row.mixer is None
    assert row.path_b.number == 2


def test_a_branch_that_rejoins_has_a_mixer(split):
    row = rows_of(split)[3]
    assert row.splitter.slot == 4, "wire column 3 is slot 4"
    assert row.mixer is not None and row.mixer.slot == 5
    assert row.path_b.number == 4


def test_a_row_with_no_branch_has_neither(structural):
    row = rows_of(structural)[1]
    assert row.splitter is None and row.mixer is None


def test_path_b_is_a_plain_row(split):
    """Path B cannot itself branch, so it is not a SplittableRow."""
    assert not isinstance(rows_of(split)[1].path_b, grid_module.SplittableRow)


def test_path_b_is_the_row_below(split):
    assert rows_of(split)[1].path_b.number == 2
    assert rows_of(split)[3].path_b.number == 4


# -- the grid -----------------------------------------------------------------


def test_the_grid_is_keyed_by_row_and_slot(structural):
    grid = live(structural)
    assert grid[1, 1] is not None
    assert grid[2, 1] is None


def test_the_grid_refuses_a_key_that_is_not_a_cell(structural):
    grid = live(structural)
    with pytest.raises(TypeError, match=r"blocks\[1, 3\]"):
        grid[1]


def test_iterating_the_grid_yields_only_occupied_cells(structural):
    grid = live(structural)
    found = list(grid)
    assert len(found) == len(protocol.blocks(structural))
    assert all(block is not None for block in found)
    assert len(grid) == len(found)


def test_iterating_a_row_s_slots_yields_the_empty_ones_too(structural):
    """The two collections differ on purpose, and the acceptance criteria name
    both: a BlockGrid iterates occupied cells, `slots` reports all eight."""
    grid = live(structural)
    assert len(list(grid)) == 14
    assert sum(len(list(row.slots)) for row in grid_module.Rows(grid)) == 32


def test_the_same_cell_gives_the_same_handle_through_one_binding(structural):
    grid = live(structural)
    assert grid[1, 1] is grid[1, 1]


def test_two_bindings_on_the_active_scene_agree(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    live_grid = grid_module.BlockGrid(preset)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.A)
    assert live_grid[1, 1] == fixed[1, 1]
    assert live_grid[1, 1].device == fixed[1, 1].device
    assert live_grid[1, 1].bypassed == fixed[1, 1].bypassed


def test_a_live_binding_follows_the_active_scene(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    live_grid = grid_module.BlockGrid(preset)
    assert live_grid.scene == SceneLetter.A
    preset.active_scene = SceneLetter.C
    assert live_grid.scene == SceneLetter.C


def test_a_fixed_binding_does_not(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    preset.active_scene = SceneLetter.C
    assert fixed.scene == SceneLetter.B


def test_a_binding_refuses_a_bare_scene_number(structural):
    with pytest.raises(TypeError):
        grid_module.BlockGrid(FakePreset(structural), scene=1)


def test_the_handles_are_dropped_when_the_preset_is_re_read(structural):
    """The model re-reads the whole preset after an edit, so a handle memoized
    against the old payload would go on describing the block that used to be in
    that cell."""
    preset = FakePreset(structural)
    grid = grid_module.BlockGrid(preset)
    before = grid[1, 1]
    replacement = load("structural_preset.bin")
    replacement.chains[0].models[0].hash = 4
    preset.wire = replacement
    assert grid[1, 1] is not before
    assert grid[1, 1].device.id == 4


# -- writing through a scene you are not in ----------------------------------


def test_a_grid_on_the_active_scene_is_writable(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    assert grid_module.BlockGrid(preset).writable
    assert grid_module.BlockGrid(preset, scene=SceneLetter.A).writable


def test_a_grid_on_another_scene_is_not(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    assert not grid_module.BlockGrid(preset, scene=SceneLetter.B).writable


def test_a_live_grid_is_always_writable(structural):
    """It is bound to whichever scene is active, so it cannot be on the wrong
    one by construction."""
    preset = FakePreset(structural, active=SceneLetter.A)
    grid = grid_module.BlockGrid(preset)
    preset.active_scene = SceneLetter.G
    assert grid.writable


def test_the_refusal_names_the_step_to_take(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    with pytest.raises(errors.InactiveSceneError, match=r"scene\.activate\(\)"):
        fixed.check_writable()


def test_the_refusal_says_which_scene(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    with pytest.raises(errors.InactiveSceneError, match="B"):
        fixed.check_writable()


def test_reading_through_an_inactive_binding_works_fine(structural):
    preset = FakePreset(structural, active=SceneLetter.A)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    assert fixed[1, 1].device.id == 18010
    assert fixed[1, 1].bypassed in (True, False)
    assert len(list(fixed)) == 14


def test_activating_the_scene_makes_it_writable(structural):
    """The refusal is not permanent - it names a step, and taking that step
    works. Without this the message would be advice nobody had checked."""
    preset = FakePreset(structural, active=SceneLetter.A)
    fixed = grid_module.BlockGrid(preset, scene=SceneLetter.B)
    assert not fixed.writable
    with pytest.raises(errors.InactiveSceneError):
        fixed.check_writable()
    preset.active_scene = SceneLetter.B
    assert fixed.writable
    fixed.check_writable()

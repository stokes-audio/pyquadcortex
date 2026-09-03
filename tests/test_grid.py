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


#: A lane VOLUME, with the law and the MEASURED floor the real one carries.
#: The floor is the point: wire 0.0 is an OFF detent and -40 dB is the bottom
#: of the LAW, and a catalog without it cannot tell a reader the difference -
#: which is how the first version of `grid.pedals` came to report -40 dB for
#: the commonest assignment there is, with every test passing.
LANE_VOLUME = protocol.catalog.Parameter(
    index=0, name="VOLUME", minimum=-40.0, maximum=12.0, default=0.0,
    units="dB", type="float", floor_wire=0.01, floor_display=-39.5)

#: A knob with no unit and no detent, so every position is a number.
PLAIN_KNOB = protocol.catalog.Parameter(
    index=0, name="WAH", minimum=0.0, maximum=1.0, default=0.0,
    units="", type="float")


class FakeCatalog:
    """The unit's model repository, as far as a block is concerned.

    Models carry PARAMETERS now. They did not, and the cost was exact: every
    pedal test ran the branch where no spec resolves, so the whole conversion
    to real units - the feature's reason to exist - was never executed offline.
    """

    def __init__(self, parameters=()):
        self._parameters = tuple(parameters)

    def __getitem__(self, model_id):
        if model_id == 999999:
            raise KeyError(f"no model with id {model_id} in this device's catalog")
        return protocol.Model(id=model_id, name=f"device-{model_id}",
                              category="AMP", category_id=1,
                              parameters=self._parameters)


class FakePreset:
    """A preset as far as a grid is concerned: a wire payload, a catalogue, and
    which scene is active right now."""

    def __init__(self, wire, active=SceneLetter.A, parameters=(), catalog=...):
        self.wire = wire
        self.catalog = (FakeCatalog(parameters) if catalog is ... else catalog)
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


def test_a_row_whose_routing_the_preset_never_stated_refuses_to_guess(structural):
    """`None` from `lane` means "this row feeds another row, so the screen shows
    no lane output". A row whose out_portid the preset never carried is a
    different thing, and answering None for it would turn "we do not know" into
    a positive claim."""
    structural.chains[1].ClearField("out_portid")
    row = rows_of(structural)[2]
    assert row.output.destination is None
    with pytest.raises(RuntimeError, match="does not say where row 2 goes"):
        row.output.lane


# -- expression pedals, in the unit's own words -------------------------------


def test_the_grid_reports_the_pedal_the_fixture_carries(structural):
    """Row 1 slot 2, not the wire's row 0 column 1.

    The whole point of reading this through the model: the player's screen
    numbers rows and slots from 1, and the wire does not.
    """
    found = live(structural).pedals
    assert len(found) == 1
    one = found[0]
    assert (one.row, one.slot) == (1, 2)
    assert one.pedal == 1
    assert not one.reversed


def test_a_block_reports_only_its_own_pedals(structural):
    grid = live(structural)
    assert len(grid[1, 2].pedals) == 1
    assert grid[1, 1].pedals == ()
    assert grid[1, 3].pedals == ()


def test_without_a_catalog_the_sweep_stays_the_devices_own_scale(structural):
    """A preset can be read with nothing attached, and a knob's scale comes
    from the unit. Saying so beats converting against a guess."""
    grid = grid_module.BlockGrid(FakePreset(structural))
    one = grid.pedals[0]
    # FakeCatalog has no parameters, so no spec resolves.
    assert not one.in_real_units
    assert one.parameter is None
    assert one.units == ""
    assert isinstance(one.minimum, protocol.Encoded)


def test_a_reversed_sweep_is_reported_rather_than_sorted(structural):
    """`minimum` above `maximum` inverts the pedal, which is a SETTING - and
    ordering the pair would throw it away."""
    model = structural.chains[0].models[1]
    model.params[0].expression_min = 0.9
    model.params[0].expression_max = 0.1
    one = live(structural).pedals[0]
    assert one.reversed
    assert float(one.minimum) > float(one.maximum)


def test_the_repr_reads_like_the_screen(structural):
    text = repr(live(structural).pedals[0])
    assert "EXP 1" in text
    assert "row 1 slot 2" in text


def _with_params(wire, parameters, active=SceneLetter.A):
    """A grid whose catalog actually describes the knob under the pedal."""
    return grid_module.BlockGrid(FakePreset(wire, active, parameters=parameters))


def test_a_sweep_end_at_the_off_detent_is_reported_as_off(structural):
    """The bug this reader shipped with, and the reason it went unseen.

    A lane VOLUME's law runs to -40 dB and its lowest NUMERIC step is -39.5:
    wire 0.0 is an OFF detent, which is a word on the screen. Converting it
    reports -40 dB - a value `to_normalized` REFUSES if you hand it back, so
    the model was reporting something the library itself rejects. The fixture's
    one assignment is a full 0.0..1.0 sweep, so this is the common case.
    """
    one = _with_params(structural, (LANE_VOLUME,)).pedals[0]
    assert one.minimum_is_off, "wire 0.0 on this law is OFF, not -40 dB"
    assert not one.maximum_is_off
    assert float(one.maximum) == pytest.approx(12.0), "the top still converts"
    assert not one.in_real_units, "an OFF end is not a value in the knob's units"
    assert "Off to 12 dB" in repr(one)


def test_a_knob_with_no_detent_converts_both_ends(structural):
    """The other half: where every position IS a number, both ends convert and
    the assignment reads wholly in the knob's own units."""
    one = _with_params(structural, (PLAIN_KNOB,)).pedals[0]
    assert not one.minimum_is_off and not one.maximum_is_off
    assert one.in_real_units
    assert one.parameter == "WAH"
    assert (float(one.minimum), float(one.maximum)) == (0.0, 1.0)


def test_the_converted_sweep_reads_in_the_knobs_units(structural):
    """Above the detent the numbers are the screen's, not the wire's."""
    model = structural.chains[0].models[1]
    model.params[0].expression_min = 0.5
    model.params[0].expression_max = 1.0
    one = _with_params(structural, (LANE_VOLUME,)).pedals[0]
    assert one.in_real_units
    assert one.units == "dB"
    assert float(one.minimum) == pytest.approx(-14.0)
    assert "-14 dB to 12 dB" in repr(one)


def test_a_converted_end_is_a_value_the_library_would_take_back(structural):
    """The round trip that the -40 dB bug failed.

    Anything this reader reports as a real value has to be one `to_normalized`
    accepts - otherwise the model is handing out numbers its own writer
    refuses.
    """
    model = structural.chains[0].models[1]
    model.params[0].expression_min = 0.5
    one = _with_params(structural, (LANE_VOLUME,)).pedals[0]
    assert LANE_VOLUME.to_normalized(float(one.minimum)) == pytest.approx(0.5)
    assert LANE_VOLUME.to_normalized(float(one.maximum)) == pytest.approx(1.0)


def test_with_no_catalog_at_all_the_sweep_stays_the_wires(structural):
    """The case the old test NAMED and did not construct.

    It built a grid with a catalog that merely described nothing, which is a
    different branch. This is a preset read with no unit attached.
    """
    grid = grid_module.BlockGrid(FakePreset(structural, catalog=None))
    one = grid.pedals[0]
    assert not one.in_real_units
    assert one.parameter is None and one.units == ""
    assert isinstance(one.minimum, protocol.Encoded)

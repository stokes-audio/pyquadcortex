"""Tests against a REAL preset payload read off a device.

The rest of the suite checks the messages this library builds. That cannot catch
being wrong about what the device sends back, which is how two findings escaped:
``HasField`` raising on a field without presence, and every grid row reporting
eight model slots whether or not any are occupied. Both are obvious the moment a
real payload is involved.

``fixtures/presets/structural_preset.bin`` is a serialized ``BinaryPreset`` read
from a Quad Cortex over USB (firmware d14e / CorOS 4.0.1). Its STRUCTURE is
verbatim - chain and slot padding, presence flags, scene-mode flags, routing - but
every parameter value has been flattened to 0.5 and the names replaced, so it
carries the device's data shape without republishing a vendor tone design.
"""

import pathlib

import pytest

from pyquadcortex import blocks, field_present
from pyquadcortex.enums import Input
from pyquadcortex.proto import Preset_pb2 as preset_pb

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "presets" / "structural_preset.bin"


@pytest.fixture(scope="module")
def real_preset():
    p = preset_pb.BinaryPreset()
    p.ParseFromString(FIXTURE.read_bytes())
    return p


def test_the_fixture_looks_like_a_real_preset(real_preset):
    assert len(real_preset.chains) == 4, "the grid is four rows"
    assert len(real_preset.scene_labels) == 8
    assert len(real_preset.bypass) == 4, "one bypass group per row"


# -- finding 8: padding means len() is not a count ----------------------------


def test_every_row_reports_eight_model_slots_even_when_empty(real_preset):
    # This is the trap: a row-occupancy survey built on len(chain.models) reports
    # every row of every preset as occupied, which is silently wrong.
    for i, chain in enumerate(real_preset.chains):
        assert len(chain.models) == 8, f"row {i} should report all 8 slots"

    occupied_per_row = {}
    for b in blocks(real_preset):
        occupied_per_row.setdefault(b.row, []).append(b.column)
    # The captured preset has two populated rows and two entirely empty ones, so
    # the padding and the real count genuinely disagree.
    assert len(occupied_per_row) < len(real_preset.chains), (
        "the fixture must contain at least one empty row, or it cannot prove "
        "that padding differs from occupancy"
    )
    for row, columns in occupied_per_row.items():
        assert 0 < len(columns) <= 8
        assert len(set(columns)) == len(columns), "no duplicate columns"


def test_in_portid_empty_is_not_an_occupancy_signal(real_preset):
    # EMPTY means "not fed from a physical jack", which is the normal state of any
    # row that is not an input row - occupied or not. Using it to find free rows
    # is wrong, and this fixture proves it: a row with blocks reports EMPTY.
    occupied_rows = {b.row for b in blocks(real_preset)}
    empty_port_rows = {
        i for i, c in enumerate(real_preset.chains)
        if not field_present(c, "in_portid") or c.in_portid == Input.EMPTY
    }
    assert occupied_rows & empty_port_rows, (
        "expected at least one occupied row whose in_portid is EMPTY"
    )


def test_blocks_returns_only_occupied_cells(real_preset):
    found = blocks(real_preset)
    assert found, "the fixture has blocks"
    total_slots = sum(len(c.models) for c in real_preset.chains)
    assert len(found) < total_slots, "must be fewer than the padded slot count"
    for b in found:
        assert b.model_id, "a Block must never carry a zero model id"
        assert 0 <= b.row < 4
        assert 0 <= b.column < 8


# -- finding 3: presence is not universal -------------------------------------


def test_hasfield_raises_on_scene_bypass_but_field_present_does_not(real_preset):
    entry = real_preset.bypass[0].colBypass[0].sceneBypass[0]
    with pytest.raises(ValueError, match="does not have presence"):
        entry.HasField("bypass")
    assert field_present(entry, "bypass") in (True, False)


def test_walking_every_scene_bypass_does_not_crash(real_preset):
    # The docs invite callers to use HasField throughout, so the obvious way to
    # walk per-scene bypass crashed on the first real preset read. Doing it with
    # the helper must work across the whole payload.
    seen = 0
    for group in real_preset.bypass:
        for col in group.colBypass:
            for entry in col.sceneBypass:
                assert field_present(entry, "bypass") in (True, False)
                assert entry.bypass in (True, False)
                seen += 1
    assert seen, "the fixture carries per-scene bypass entries"


def test_scene_mode_distinguishes_scene_following_blocks(real_preset):
    # Per-scene bypass values are only meaningful where sceneMode is set; where it
    # is not, the entries are unmaintained and can contradict the unit.
    modes = [col.sceneMode
             for group in real_preset.bypass for col in group.colBypass]
    assert len(modes) == 32, "4 rows x 8 columns"
    assert any(modes), "the fixture has scene-following blocks"
    assert not all(modes), "and blocks that ignore scenes, which is the trap"


# -- other padded collections --------------------------------------------------


def test_output_control_is_present_on_every_row(real_preset):
    # Lane Output Control (model 23000) exists on every row, populated, whether or
    # not the row has any blocks - so its presence says nothing about occupancy.
    for i, chain in enumerate(real_preset.chains):
        assert len(chain.output_control) == 1, f"row {i}"
        oc = chain.output_control[0]
        assert field_present(oc, "hash") and oc.hash == 23000
        # Five parameters on the wire, though the catalog documents four.
        assert len(oc.params) == 5


def test_recalled_chains_carry_no_explicit_row(real_preset):
    # The reason chain index has to stand in for grid row, and why a full preset
    # written back does nothing: there is no row for the device to key on.
    for chain in real_preset.chains:
        assert not field_present(chain, "row")


# -- per-scene parameter values (feature: writable per-scene values) ------------

SCENE_FIXTURE = FIXTURE.parent / "scene_preset.bin"


@pytest.fixture(scope="module")
def scene_preset():
    """A preset carrying real per-scene parameter structure.

    Read off a device after using the library to mute one scene via the Lane
    Output Control. The per-scene SHAPE is real - which parameters follow scenes,
    and which scene differs - with the values themselves flattened to 0.5 (or 0.0
    for the muted scene).
    """
    p = preset_pb.BinaryPreset()
    p.ParseFromString(SCENE_FIXTURE.read_bytes())
    return p


def scene_following_params(p):
    """(location, scene_mode, values) for parameters that follow scenes."""
    out = []
    for ci, chain in enumerate(p.chains):
        for label, coll in (("models", chain.models),
                            ("output_control", chain.output_control)):
            for mi, model in enumerate(coll):
                for pi, param in enumerate(model.params):
                    if field_present(param, "scene_mode") and param.scene_mode:
                        out.append(((ci, label, mi, pi), [
                            pv.float_value if field_present(pv, "float_value") else None
                            for pv in param.param_values]))
    return out


def test_the_device_really_stores_per_scene_parameter_values(scene_preset):
    # The claim this feature rests on: a parameter with scene_mode set keeps eight
    # independent values, and they can genuinely differ.
    following = scene_following_params(scene_preset)
    assert following, "the fixture must carry scene-following parameters"
    varying = [(loc, vals) for loc, vals in following if len(set(vals)) > 1]
    assert varying, "at least one must actually differ between scenes"
    for _, vals in following:
        assert len(vals) == 8, "eight scenes, always"


def test_a_muted_scene_reads_back_as_zero_in_that_scene_only(scene_preset):
    # The requester's use case: one scene silent, the rest untouched. Written with
    # set_lane_output(param="VOLUME", value=0.0, scene=Scene.E).
    lane = [(loc, vals) for loc, vals in scene_following_params(scene_preset)
            if loc[1] == "output_control"]
    assert lane, "the Lane Output Control parameter should follow scenes"
    _, vals = lane[0]
    zeros = [i for i, v in enumerate(vals) if v == 0.0]
    assert len(zeros) == 1, f"exactly one scene silent, got {zeros}"
    assert all(v != 0.0 for i, v in enumerate(vals) if i not in zeros)


def test_scene_mode_is_what_distinguishes_per_scene_parameters(scene_preset):
    # A parameter without scene_mode has one global value repeated eight times, so
    # writing it looks like "all eight scenes changed" - which is how per-scene
    # writes were first mistaken for impossible.
    global_params = []
    for chain in scene_preset.chains:
        for model in chain.models:
            for param in model.params:
                if not (field_present(param, "scene_mode") and param.scene_mode):
                    vals = [pv.float_value for pv in param.param_values
                            if field_present(pv, "float_value")]
                    if len(vals) == 8:
                        global_params.append(vals)
    assert global_params, "the fixture has ordinary, non-scene-following parameters"
    for vals in global_params:
        assert len(set(vals)) == 1, "a global parameter reads the same in every scene"


# -- grid topology: where a row splits ----------------------------------------


def test_splits_recovers_the_branch_columns(real_preset):
    # The splitter block carries no column, so where a lane leaves the row looked
    # unknowable and had to be inferred from a lone block lining up. It is in
    # Chain.split_control_points instead - whose split and mix fields have NO
    # presence, so HasField reports them absent and they are easy to miss entirely.
    from pyquadcortex import splits

    found = splits(real_preset)
    # This fixture's rows are serial: they report the -1 sentinel, which splits()
    # filters out rather than reporting as column -1.
    if not found:
        for chain in real_preset.chains:
            for scp in chain.split_control_points:
                assert scp.split == -1 and scp.mix == -1
        pytest.skip("this fixture is serial; the sentinel case is asserted above")
    for s in found:
        assert 0 <= s.row < 4
        assert 0 <= s.split_column < 8
        assert 0 <= s.mix_column < 8


def test_split_control_point_fields_have_no_presence(real_preset):
    # Locks in WHY splits() reads the values directly: gating on presence, which is
    # the correct habit everywhere else in this schema, silently yields nothing here.
    from pyquadcortex import field_present

    for chain in real_preset.chains:
        for scp in chain.split_control_points:
            assert field_present(scp, "split") is False, (
                "if this ever becomes True the schema changed and splits() can "
                "start gating on presence like everything else"
            )
            assert isinstance(scp.split, int), "the value is readable regardless"

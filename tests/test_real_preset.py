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

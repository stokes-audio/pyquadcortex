"""The one place screen coordinates and display units become wire values.

Design principle 5 in ``docs/domain-model.md``: the model speaks touchscreen
coordinates and display units everywhere, and the conversion happens in exactly
one module. These tests are deliberately exhaustive, because the bug they exist
to stop is silent - the protocol layer's own header says an edit to the wrong row
"lands on a real row, just not the one intended, and it reads back perfectly".
Nothing on the unit and nothing in the reply tells you. So a test is the only
thing that can.

Two of the checks below are structural rather than behavioural: they read the
source of every file in the package that is not the protocol layer, and prove no
other module does the arithmetic or reaches past the boundary for a protocol
helper that does. Not just the model directory - a rule scoped to a directory is
satisfiable by moving the code to a different one.
"""
import ast
import importlib
import pathlib
import pkgutil

import pytest

import pyquadcortex
from pyquadcortex import protocol
from pyquadcortex.device import translate
from pyquadcortex.protocol.proto import Preset_pb2 as preset_pb
from pyquadcortex.protocol.targets import Block, Tempo
from pyquadcortex.protocol.values import Milliseconds, Real


# -- rows: 1-4 on screen, 0-3 on the wire ------------------------------------


@pytest.mark.parametrize("row,index", [(1, 0), (2, 1), (3, 2), (4, 3)])
def test_every_row_converts_both_ways(row, index):
    assert translate.row_to_wire(row) == index
    assert translate.row_from_wire(index) == row


@pytest.mark.parametrize("row", [0, 5, -1, 100])
def test_a_row_the_screen_does_not_show_is_refused(row):
    with pytest.raises(ValueError, match="1 to 4"):
        translate.row_to_wire(row)


@pytest.mark.parametrize("index", [-1, 4, 99])
def test_a_wire_row_outside_the_grid_is_refused(index):
    with pytest.raises(ValueError, match="0 to 3"):
        translate.row_from_wire(index)


def test_a_bool_is_not_a_row():
    # True == 1, so an unguarded check converts True to wire row 0 and edits the
    # top row. bool is a subclass of int, which is why this needs saying.
    with pytest.raises(TypeError):
        translate.row_to_wire(True)


def test_a_float_is_not_a_row():
    with pytest.raises(TypeError):
        translate.row_to_wire(1.0)


# -- slots: 1-8 on screen, columns 0-7 on the wire ---------------------------


@pytest.mark.parametrize("slot,column", list(zip(range(1, 9), range(0, 8))))
def test_every_slot_converts_both_ways(slot, column):
    assert translate.slot_to_wire(slot) == column
    assert translate.slot_from_wire(column) == slot


@pytest.mark.parametrize("slot", [0, 9, -1])
def test_a_slot_the_screen_does_not_show_is_refused(slot):
    with pytest.raises(ValueError, match="1 to 8"):
        translate.slot_to_wire(slot)


@pytest.mark.parametrize("column", [-1, 8])
def test_a_wire_column_outside_the_row_is_refused(column):
    with pytest.raises(ValueError, match="0 to 7"):
        translate.slot_from_wire(column)


def test_a_bool_is_not_a_slot():
    with pytest.raises(TypeError):
        translate.slot_to_wire(True)


# -- one index is never another index ----------------------------------------
#
# The protocol layer's coordinate enums are IntEnums, so each one is an int and
# passes any `isinstance(x, int)` check. Scene B is 1 and row 2 is wire 1, which
# means a Scene handed to a row converter produces a real row rather than a
# complaint. That is the same class of mistake as the footswitch-versus-column
# confusion this module exists to prevent, so it is refused the same way.


@pytest.mark.parametrize("converter,wrong", [
    ("row_to_wire", protocol.Scene.B),
    ("slot_to_wire", protocol.Footswitch.C),
    ("row_from_wire", protocol.Footswitch.A),
    ("slot_from_wire", protocol.Scene.H),
])
def test_a_coordinate_from_somewhere_else_is_refused(converter, wrong):
    with pytest.raises(TypeError):
        getattr(translate, converter)(wrong)


def test_a_scene_index_is_not_a_footswitch_index():
    with pytest.raises(TypeError):
        translate.footswitch_from_wire(protocol.Scene.E)


def test_a_footswitch_index_is_not_a_scene_index():
    with pytest.raises(TypeError):
        translate.scene_from_wire(protocol.Footswitch.E)


def test_a_scene_letter_is_not_a_footswitch():
    """Both are letters A to H and both are strings, so nothing but the type
    itself keeps `scenes["E"]`'s key out of a footswitch API."""
    with pytest.raises(TypeError):
        translate.footswitch_to_wire(translate.SceneLetter.E)


def test_a_footswitch_letter_is_not_a_scene():
    with pytest.raises(TypeError):
        translate.scene_to_wire(translate.FootswitchLetter.B)


# -- footswitches: letters in the model, indexes on the wire -----------------
#
# The protocol layer's `Footswitch` enum is the reference. It is also the reason
# this type exists: `stomp_is_momentary` is keyed by footswitch index, and a
# block at column 3 assigned to footswitch E comes back keyed 4 (domain-model.md
# section 7). Where a bare int can reach a model API, someone eventually passes
# a column to it.

LETTERS = ("A", "B", "C", "D", "E", "F", "G", "H")


@pytest.mark.parametrize("letter", LETTERS)
def test_every_footswitch_converts_both_ways(letter):
    reference = getattr(protocol.Footswitch, letter)
    switch = translate.FootswitchLetter(letter)
    assert translate.footswitch_to_wire(switch) == reference
    assert translate.footswitch_from_wire(reference) is switch


def test_a_footswitch_letter_is_a_string():
    """So it prints as the screen shows it and keys an ordinary dict."""
    assert str(translate.FootswitchLetter.E) == "E"
    assert translate.FootswitchLetter.E == "E"
    assert {translate.FootswitchLetter.E: "vibe"}["E"] == "vibe"


def test_a_plain_letter_is_accepted_where_a_footswitch_is_wanted():
    assert translate.footswitch_to_wire("E") == protocol.Footswitch.E
    assert translate.footswitch_to_wire("e") == protocol.Footswitch.E


def test_a_bare_integer_is_never_a_footswitch():
    """The whole reason FootswitchLetter exists. 4 is E, and it is also column 5."""
    with pytest.raises(TypeError, match="column"):
        translate.footswitch_to_wire(4)


def test_a_letter_no_footswitch_carries_is_refused():
    with pytest.raises(ValueError, match="A to H"):
        translate.footswitch_to_wire("J")


@pytest.mark.parametrize("index", [-1, 8])
def test_a_wire_footswitch_index_off_the_pedalboard_is_refused(index):
    with pytest.raises(ValueError):
        translate.footswitch_from_wire(index)


# -- scenes: letters in the model, indexes on the wire -----------------------


@pytest.mark.parametrize("letter", LETTERS)
def test_every_scene_converts_both_ways(letter):
    reference = getattr(protocol.Scene, letter)
    scene = translate.SceneLetter(letter)
    assert translate.scene_to_wire(scene) == reference
    assert translate.scene_from_wire(reference) is scene


def test_a_plain_letter_is_accepted_where_a_scene_is_wanted():
    assert translate.scene_to_wire("b") == protocol.Scene.B


def test_a_bare_integer_is_never_a_scene():
    with pytest.raises(TypeError):
        translate.scene_to_wire(1)


def test_a_letter_no_scene_carries_is_refused():
    with pytest.raises(ValueError, match="A to H"):
        translate.scene_to_wire("I")


# -- preset addresses: "28C" on screen, 218 on the wire ----------------------


def test_an_address_renders_the_way_the_directory_shows_it():
    assert str(translate.PresetAddress(28, "C")) == "28C"
    assert str(translate.PresetAddress(1, "A")) == "1A"


def test_an_address_parses_the_same_form_it_renders():
    assert translate.PresetAddress.parse("28C") == translate.PresetAddress(28, "C")


def test_a_padded_bank_parses_and_renders_unpadded():
    """The unit displays "1A". `slot_to_position` takes "01A" too, so parsing
    accepts it and rendering does not produce it."""
    assert translate.PresetAddress.parse("01A") == translate.PresetAddress(1, "A")
    assert str(translate.PresetAddress.parse("01A")) == "1A"


@pytest.mark.parametrize("written,bank,position", [
    (" 28c ", 28, "C"),
    ("32H", 32, "H"),
    ("1a", 1, "A"),
])
def test_parsing_normalises_what_a_person_types(written, bank, position):
    address = translate.PresetAddress.parse(written)
    assert (address.bank, address.position) == (bank, position)


#: Names the unit never shows. One list, because the boundary has TWO public
#: doors onto this conversion and a name that only one of them refuses is a hole
#: with a test in front of it - see the two tests below.
#:
#: "٢٨C" and "28²C" are the ones that were getting through. Python's `\d` spans
#: every Unicode digit and so does `str.isdigit()`, which is what the protocol
#: helper checks the bank with, and `int()` reads those digits too.
MALFORMED_NAMES = ["", " ", "C", "28", "28I", "0A", "33A", "28CC", "-1A",
                   "2.5C", "A28", "28 C", "2 8C", "٢٨C", "28²C"]


@pytest.mark.parametrize("malformed", MALFORMED_NAMES)
def test_a_malformed_address_is_refused_when_it_is_parsed(malformed):
    """Not when it is written. A bad address that survives parsing becomes a
    wire position, and a wrong position is a preset that recalls fine and is the
    wrong preset."""
    with pytest.raises(ValueError):
        translate.PresetAddress.parse(malformed)


@pytest.mark.parametrize("malformed", MALFORMED_NAMES)
def test_the_other_door_onto_a_slot_name_refuses_them_too(malformed):
    """`slot_to_position` is public, converts the same names, and was refusing a
    different set.

    Only `PresetAddress.parse` had the ASCII-digit pattern, so
    `translate.slot_to_position("٢٨C")` returned 218 - a real preset, from a name
    no screen shows, through the front door of the module whose whole job is to
    stop exactly that. Delegation could not fix it: the protocol helper accepts
    those digits by design, so the shape check has to be at the boundary.
    """
    with pytest.raises(ValueError):
        translate.slot_to_position(malformed)


@pytest.mark.parametrize("wrong_type", [None, 218, ["28C"]])
def test_an_address_that_is_not_text_is_refused(wrong_type):
    with pytest.raises(TypeError):
        translate.PresetAddress.parse(wrong_type)


@pytest.mark.parametrize("bank,position", [(0, "A"), (33, "A"), (28, "I"),
                                           (28, ""), (28, "CC")])
def test_an_impossible_address_is_refused_at_construction(bank, position):
    with pytest.raises(ValueError):
        translate.PresetAddress(bank, position)


def test_an_address_is_hashable_and_compares_by_value():
    a = translate.PresetAddress(28, "C")
    assert a == translate.PresetAddress.parse("28C")
    assert len({a, translate.PresetAddress(28, "C")}) == 1


ALL_POSITIONS = list(range(32 * 8))


@pytest.mark.parametrize("position", ALL_POSITIONS)
def test_every_address_in_a_setlist_round_trips_against_the_protocol_layer(position):
    """All 256 of them, against the protocol layer's own pair as the reference."""
    name = protocol.position_to_slot(position)
    address = translate.PresetAddress.from_wire(position)
    assert str(address) == name
    assert address.to_wire() == position
    assert translate.slot_to_position(name) == protocol.slot_to_position(name)
    assert translate.position_to_slot(position) == name


def test_a_padded_render_is_available_for_the_wire_facing_form():
    assert translate.position_to_slot(0, pad=True) == "01A"
    assert protocol.position_to_slot(0, pad=True) == "01A"


@pytest.mark.parametrize("position", [-1, 256, 1000])
def test_a_wire_position_outside_a_setlist_is_refused(position):
    with pytest.raises(ValueError):
        translate.position_to_slot(position)
    with pytest.raises(ValueError):
        translate.PresetAddress.from_wire(position)


@pytest.mark.parametrize("position", [218.9, True, "218", None])
def test_a_wire_position_that_is_not_a_whole_number_is_refused(position):
    """The protocol helper takes `int(position)`, so 218.9 quietly becomes 218
    and True becomes 1. Every other coordinate path here refuses a float and a
    bool; this is the path where the wrong answer recalls a real preset."""
    with pytest.raises(TypeError):
        translate.position_to_slot(position)
    with pytest.raises(TypeError):
        translate.PresetAddress.from_wire(position)


@pytest.mark.parametrize("name", [218, None, ["28C"]])
def test_a_slot_name_that_is_not_text_is_refused(name):
    with pytest.raises(TypeError):
        translate.slot_to_position(name)


def test_the_protocol_helper_really_is_looser_about_digits():
    """The reason the shape check is at the boundary and not left to delegation.

    If this ever starts raising, the protocol layer has tightened and the
    boundary's own pattern is no longer the thing holding the line - which is
    worth knowing, because the comment on `_SLOT_NAME` says it is.
    """
    assert protocol.slot_to_position("٢٨C") == 218
    with pytest.raises(ValueError):
        translate.slot_to_position("٢٨C")


def test_the_address_conversion_says_the_naming_depends_on_the_mode():
    """An address is only unambiguous alongside the mode it was read in: linear
    position 5 reads "1F" normally and "2B" under a PRESET-containing HYBRID.
    A caller who does not know that will mis-address a preset, so the function
    that converts has to say it."""
    doc = translate.slot_to_position.__doc__
    if doc is None:                     # python -OO strips docstrings
        pytest.skip("docstrings are stripped under -OO")
    assert "mode" in doc and "unambiguous" in doc
    assert "HYBRID" in doc or "hybrid" in doc


# -- display units -----------------------------------------------------------
#
# **What the equality assertions below do and do not prove.** Three of these five
# mappings delegate to a protocol-layer helper, so `translate.input_level_db(v)
# == protocol.input_level_db(v)` cannot fail today - it is one function calling
# the other. It is not a check on the arithmetic, and it is not a substitute for
# one. What it pins is that the boundary goes on DELEGATING: the day someone
# copies the formula in here to add a clamp or a rounding rule, this is what
# fails.
#
# The measured numbers themselves are pinned where the measurement lives, in
# tests/test_client.py: `test_input_level_db_matches_the_four_measured_points`,
# `test_lane_level_db_matches_the_three_measured_points` and
# `test_tempo_bpm_matches_every_measured_point` check the screen readings taken
# against simultaneous wire reads. Nothing here restates them, because a second
# copy of a measured constant drifts and both copies keep returning a plausible
# number.
#
# The other two mappings - the tuner and hold timing - have no protocol helper
# to call, only a documented rule and a shared constant, so they are pinned
# below against the protocol WRITE path through a fake transport. That is a real
# check: it fails if the model's idea of 442 Hz stops matching what the method
# that sends it expects.


class Recorder:
    """The smallest transport a `QuadCortex` needs to record what it would send."""

    def __init__(self):
        self.sent = []

    def send(self, message):
        self.sent.append(message)


WIRE_LEVELS = [0.0, 0.01, 0.16667, 0.4, 0.5, protocol.UNITY_LEVEL, 0.9, 1.0]


@pytest.mark.parametrize("level", WIRE_LEVELS)
def test_input_level_matches_the_protocol_layers_own_conversion(level):
    assert translate.input_level_db(level) == protocol.input_level_db(level)


@pytest.mark.parametrize("db", [-12.0, -6.0, 0.0, 17.2, 24.0, 60.0])
def test_input_level_round_trips_through_the_wire_scale(db):
    assert translate.input_level_db(translate.db_to_input_level(db)) == \
        pytest.approx(db)
    assert translate.db_to_input_level(db) == protocol.db_to_input_level(db)


@pytest.mark.parametrize("db", [-12.1, 60.1, -100.0])
def test_an_input_gain_the_unit_has_no_setting_for_is_refused(db):
    with pytest.raises(ValueError, match="-12"):
        translate.db_to_input_level(db)


@pytest.mark.parametrize("value", WIRE_LEVELS)
def test_lane_level_matches_the_protocol_layers_own_conversion(value):
    assert translate.lane_level_db(value) == protocol.lane_level_db(value)


@pytest.mark.parametrize("db", [-40.0, -39.5, -3.1, 0.0, 6.0, 12.0])
def test_lane_level_round_trips_through_the_wire_scale(db):
    assert translate.lane_level_db(translate.db_to_lane_level(db)) == \
        pytest.approx(db)
    assert translate.db_to_lane_level(db) == protocol.db_to_lane_level(db)


def test_unity_on_a_lane_level_is_zero_db():
    """10/13, measured on every row carrying one across 17 factory presets.

    The tolerance is absolute because the target is zero, and loose because
    `UNITY_LEVEL` is 10/13 rounded to eight decimals - well inside what the
    screen can show.
    """
    assert translate.lane_level_db(protocol.UNITY_LEVEL) == \
        pytest.approx(0.0, abs=1e-6)


@pytest.mark.parametrize("db", [-40.1, 12.1, 60.0])
def test_a_lane_level_the_unit_has_no_setting_for_is_refused(db):
    with pytest.raises(ValueError, match="-40"):
        translate.db_to_lane_level(db)


@pytest.mark.parametrize("converter", ["input_level_db", "db_to_input_level",
                                       "lane_level_db", "db_to_lane_level",
                                       "tempo_bpm", "bpm_to_tempo"])
@pytest.mark.parametrize("wrong", [True, "0.5", None])
def test_a_display_value_that_is_not_a_number_is_refused(converter, wrong):
    """`True` is an int, so an unguarded lane level read it as full scale and
    returned +12 dB. The tempo has the same shape - `protocol.tempo_bpm(True)`
    is 240.0, the top of the span - and `bpm_to_tempo(True)` would be a bpm of
    1, refused for being off the bottom rather than for being a bool. A string
    reached the protocol layer and came back as a `TypeError` about multiplying
    a sequence."""
    with pytest.raises(TypeError):
        getattr(translate, converter)(wrong)


def test_the_two_level_scales_are_not_interchangeable():
    """Both are a 0..1 wire value and they mean different dB. Reading a lane
    level with the input mapping is a wrong answer that looks plausible."""
    assert translate.input_level_db(0.5) != translate.lane_level_db(0.5)


# -- the tempo: bpm on screen, a 0..1 value on the wire -----------------------
#
# The wrapper is here rather than the helper itself. `set_param(Tempo(), Real())`
# calls `protocol.bpm_to_tempo` from inside the protocol layer, so moving the
# helper up to the boundary would make the protocol layer import the model -
# which `tests/test_namespace.py` refuses. Delegating gets one copy of the
# measured span either way.


TEMPO_WIRE_VALUES = [0.0, 0.095, 0.355, 0.4, 0.5, 1.0]


@pytest.mark.parametrize("value", TEMPO_WIRE_VALUES)
def test_the_tempo_matches_the_protocol_layers_own_conversion(value):
    assert translate.tempo_bpm(value) == protocol.tempo_bpm(value)


@pytest.mark.parametrize("bpm", [40.0, 59.0, 111.0, 120.0, 240.0])
def test_the_tempo_round_trips_through_the_wire_scale(bpm):
    assert translate.tempo_bpm(translate.bpm_to_tempo(bpm)) == pytest.approx(bpm)
    assert translate.bpm_to_tempo(bpm) == protocol.bpm_to_tempo(bpm)


@pytest.mark.parametrize("bpm", [39.9, 240.1, 0.0, 1000.0])
def test_a_tempo_the_unit_has_no_setting_for_is_refused(bpm):
    with pytest.raises(ValueError, match=r"40\.\.240"):
        translate.bpm_to_tempo(bpm)


@pytest.mark.parametrize("value", [-0.01, 1.01, 2.0])
def test_a_tempo_wire_value_off_the_scale_is_refused(value):
    """Unlike a level, which converts off the end of its knob. An out-of-span
    tempo names a bpm the unit has no setting for, so the protocol helper
    refuses it and this one inherits that."""
    with pytest.raises(ValueError):
        translate.tempo_bpm(value)


def test_the_tempo_is_what_the_protocol_write_expects():
    """The same shape as the tuner and hold-timing checks: the model's idea of
    111 bpm has to be the number `set_param(Tempo(), Real())` puts on the wire.

    Weaker than those two, and worth saying so. Both sides of this equality run
    through the same MIN_TEMPO / MAX_TEMPO numbers, so it does not check the
    arithmetic. What it fails on is `set_param(Tempo(), Real())` routing
    somewhere else, or the device layer and the protocol layer drifting onto
    different spans."""
    from tests.test_catalog import make_payload, SAMPLE_XML
    from pyquadcortex.protocol import catalog as catalog_module

    recorder = Recorder()
    qc = protocol.QuadCortex(recorder)
    # TEMPO converts through the catalog now, like every other parameter.
    qc._catalog = catalog_module.parse_model_repo(make_payload(SAMPLE_XML))
    qc.set_param(Tempo(), "TEMPO", Real(111.0))
    sent = recorder.sent[-1].preset.tempoProgramData[0].params[0]
    assert sent.param_values[0].float_value == \
        pytest.approx(translate.bpm_to_tempo(111.0))


# -- the tuner's reference pitch: absolute Hz on screen, an offset on the wire


@pytest.mark.parametrize("hz,offset", [(440.0, 0.0), (442.0, 2.0), (445.0, 5.0),
                                       (436.5, -3.5)])
def test_the_tuner_reference_converts_both_ways(hz, offset):
    assert translate.hz_to_tuner_reference(hz) == pytest.approx(offset)
    assert translate.tuner_reference_hz(offset) == pytest.approx(hz)


def test_the_tuner_reference_is_what_the_protocol_write_expects():
    """442 Hz on screen broadcast `frequency: 1.99999809`, so the wire carries
    the offset from 440 and the model has to hand the method that offset.

    `set_tuner_reference` does no arithmetic - it passes its argument through as
    `frequency=` - so the protocol METHOD is not an independent reference for
    the number, and this does not check the 440. What it catches is the `+ 440`
    moving across the seam: if the model started sending absolute Hz, or the
    method started adding the offset itself, one of the two would double up and
    this fails.
    """
    recorder = Recorder()
    qc = protocol.QuadCortex(recorder)
    qc.set_tuner_reference(translate.hz_to_tuner_reference(442.0))
    assert recorder.sent[-1].frequency == pytest.approx(2.0)


def test_the_tuner_reference_refuses_something_that_is_not_a_pitch():
    with pytest.raises(TypeError):
        translate.hz_to_tuner_reference("442")


def test_the_tuner_reference_is_not_rounded_to_a_precision_nobody_has_read():
    """The wire value the unit sent for a screen reading of 442 was
    1.99999809, so this returns 441.99999809. Rounding it to 442 would mean
    knowing how many digits the FREQ field shows, and nobody has read that off
    the unit."""
    assert translate.tuner_reference_hz(1.99999809) == pytest.approx(441.99999809)


# -- hold timing: milliseconds on screen, one of six indexes on the wire ------


@pytest.mark.parametrize("index", range(6))
def test_every_hold_timing_index_reads_the_same_way_the_protocol_layer_does(index):
    """The shared constant is the reference, so this does NOT check the six
    numbers - `ms_to_hold_timing(reference[index]) == index` reduces to
    `tuple.index(tuple[i]) == i`, true for any six distinct values. Replace
    `HOLD_TIMING_MS` with nonsense and this stays green.

    The numbers are pinned as literals where they were read off the unit, in
    `tests/test_client.py::test_set_hold_timing_writes_the_index_not_the_milliseconds`
    (500 ms is index 0, 800 is 3, 1000 is 5). What this pins is the pair being
    inverses of each other over that constant, whatever it holds.
    """
    reference = protocol.QuadCortex.HOLD_TIMING_MS
    assert translate.hold_timing_ms(index) == reference[index]
    assert translate.ms_to_hold_timing(reference[index]) == index


def test_hold_timing_is_what_the_protocol_write_expects():
    recorder = Recorder()
    qc = protocol.QuadCortex(recorder)
    qc.set_hold_timing(Milliseconds(800))
    assert recorder.sent[-1].hold_timing == translate.ms_to_hold_timing(800)


@pytest.mark.parametrize("ms", [499, 550, 1100, 0])
def test_a_hold_timing_the_unit_does_not_offer_is_refused(ms):
    """The device stores 0 or 5000 as happily as a real index and validates
    nothing, so a value that is not one of the six is a setting no screen can
    show and no gesture will match."""
    with pytest.raises(ValueError):
        translate.ms_to_hold_timing(ms)


@pytest.mark.parametrize("index", [-1, 6, 5000])
def test_a_hold_timing_index_the_unit_cannot_have_written_is_refused(index):
    with pytest.raises(ValueError):
        translate.hold_timing_ms(index)


@pytest.mark.parametrize("ms", [500.9, 800.0, "500", True])
def test_a_hold_timing_that_is_not_a_whole_number_of_ms_is_refused(ms):
    """`int(milliseconds)` rounded 500.9 down to a valid setting and read "500"
    as a number, which is not what "refused rather than rounded" means."""
    with pytest.raises(TypeError):
        translate.ms_to_hold_timing(ms)


@pytest.mark.parametrize("index", ["3", 3.0, True])
def test_a_hold_timing_index_that_is_not_a_whole_number_is_refused(index):
    """A wrong TYPE raises TypeError here, as it does everywhere else in this
    module; a wrong VALUE raises ValueError. This one used to raise ValueError
    for both."""
    with pytest.raises(TypeError):
        translate.hold_timing_ms(index)


# -- the boundary is the ONLY place -----------------------------------------
#
# Everything above proves the conversions are right. These two prove they are
# the only ones, which is the half a reviewer cannot check by reading a diff:
# a stray `- 1` in a future module is one character, looks deliberate, and
# produces an edit that lands on a real row and reads back perfectly.
#
# The scan covers EVERY source file in the package that is not the protocol
# layer, not just the model directory. Scoping it to `device/` would leave the
# rule satisfiable by putting the arithmetic in `pyquadcortex/coords.py`, one
# directory up - which is where somebody would put it after reading a failure
# message that named a directory.

#: The boundary is a PACKAGE, so the exemption below covers a directory rather
#: than a file. That is a bigger hole and it is why `BOUNDARY_MODULES` exists.
BOUNDARY = pathlib.Path(translate.__file__).resolve().parent
BOUNDARY_SOURCES = sorted(BOUNDARY.rglob("*.py"))
PACKAGE_ROOT = pathlib.Path(pyquadcortex.__file__).resolve().parent
PROTOCOL_ROOT = pathlib.Path(protocol.__file__).resolve().parent
MODEL_SOURCES = sorted(p for p in PACKAGE_ROOT.rglob("*.py")
                       if not p.is_relative_to(PROTOCOL_ROOT))
OTHER_MODEL_SOURCES = [p for p in MODEL_SOURCES
                       if not p.is_relative_to(BOUNDARY)]

#: The boundary package's modules, by name. Everything in that directory is
#: exempt from the two checks at the bottom of this file, so without this list
#: the exemption is a hole shaped like a directory: somebody adds
#: `translate/whatever.py`, puts the arithmetic in it, and the scan skips the
#: file for the same reason it skips the real converters. Naming them means a
#: new module has to come through here, with a reason, in the same commit.
BOUNDARY_MODULES = frozenset({
    "__init__",      # re-exports the whole public surface
    "guards",        # the shared type checks
    "coordinates",   # rows 1-4 and slots 1-8
    "letters",       # scene and footswitch letters
    "addresses",     # "28C" and its linear position
    "units",         # dB, Hz, bpm, milliseconds
    "grid",          # a whole wire preset, renumbered for the screen
})


def test_the_boundary_package_holds_only_the_modules_it_names():
    # Keyed on the path within the package, not on the filename. `rglob` is
    # recursive, so a stem-keyed check let `translate/legacy/grid.py` pass as
    # "grid" - and everything under the boundary is exempt from the arithmetic
    # scan, so that was the directory-shaped hole this test exists to close,
    # still open one level down.
    found = {str(p.relative_to(BOUNDARY).with_suffix("")) for p in BOUNDARY_SOURCES}
    added = sorted(found - BOUNDARY_MODULES)
    gone = sorted(BOUNDARY_MODULES - found)
    assert not added, (
        f"{added} is inside the translation boundary but is not named in "
        f"BOUNDARY_MODULES. Every file in that directory TREE is exempt from "
        f"the index-arithmetic scan, so a module added quietly is a way round "
        f"the whole rule. Add it here with a reason, or put it outside.")
    assert not gone, (
        f"BOUNDARY_MODULES names {gone}, which is not in the package - so this "
        f"list is guarding a file that does not exist")


#: The boundary's own coordinate tables. It publishes them, so a model module
#: can convert a coordinate with `translate.ROWS.index(row)` and never write a
#: `- 1` at all. That spelling used the boundary's own names to get around the
#: boundary, which is why looking one up is treated as arithmetic.
COORDINATE_TABLES = ("ROWS", "SLOTS")

#: Letters the unit labels a scene or a footswitch with. A table of these, in
#: any container, is a conversion whether or not any number appears near it.
UNIT_LETTERS = "ABCDEFGH"


def _index_arithmetic(tree: ast.AST) -> list[str]:
    """Every spelling of index arithmetic in `tree`, as "line N: what".

    Blunt on purpose, and wider than `+ 1`. A rule that only fired on operands
    spelled `row` or `slot` would miss `n - 1`, and `n` is what the arithmetic
    is called by the time somebody has extracted a helper for it. The calls
    listed here are how a person actually writes the conversions this module
    owns: `ord`/`chr` or a letter table for a scene or footswitch letter,
    `divmod` for a preset address, and `ROWS.index(...)` for a coordinate,
    which does the job with no arithmetic in it anywhere.

    **What it cannot see**, so that nobody reads this as a proof. A constant
    with a name (`OFFSET = 1` then `row - OFFSET`) needs the value followed
    across statements, and a table built at run time (`tuple(range(1, 5))`,
    a comprehension over `ascii_uppercase`) needs it evaluated. Both are past
    what an AST pass does, and a check that claimed otherwise would be worse
    than one that says where it stops. `ARITHMETIC_BLIND_SPOTS` below pins the
    known ones so the gap is written down rather than discovered.
    """
    def one(node) -> bool:
        """A literal one, however spelled: 1, 1.0, or True - which equals 1."""
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, str)
                and node.value == 1)

    def offset(node) -> bool:
        return one(node) or (isinstance(node, ast.UnaryOp)
                             and isinstance(node.op, ast.USub)
                             and one(node.operand))

    def letters(values) -> bool:
        """A run of the unit's letters, as a sequence of one-character strings."""
        if len(values) < 3 or not all(isinstance(v, str) and len(v) == 1
                                      for v in values):
            return False
        return "".join(values) in UNIT_LETTERS

    def letter_table(node) -> bool:
        """A literal table of the letters the unit labels with.

        A string ("ABCDEFGH"), a tuple or list of them, or the keys of a dict -
        all four are one lookup away from an index, and only the string spelling
        was caught before. A run that does not start at "A" counts: "BCDEFGH"
        with an offset is the same conversion with a bug in it.
        """
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return len(node.value) >= 3 and node.value in UNIT_LETTERS
        if isinstance(node, (ast.Tuple, ast.List, ast.Set)):
            return letters([e.value for e in node.elts
                            if isinstance(e, ast.Constant)]
                           if all(isinstance(e, ast.Constant) for e in node.elts)
                           else [])
        if isinstance(node, ast.Dict):
            return letters([k.value for k in node.keys
                            if isinstance(k, ast.Constant)]
                           if all(isinstance(k, ast.Constant)
                                  for k in node.keys) else [])
        return False

    def called(node):
        if isinstance(node.func, ast.Name):
            return node.func.id
        return node.func.attr if isinstance(node.func, ast.Attribute) else None

    def table_lookup(node) -> bool:
        """`.index()` on one of the boundary's coordinate tables."""
        return (isinstance(node.func, ast.Attribute) and node.func.attr == "index"
                and isinstance(node.func.value, (ast.Name, ast.Attribute))
                and _rightmost_name(node.func.value) in COORDINATE_TABLES)

    found = []
    for node in ast.walk(tree):
        where = f"line {getattr(node, 'lineno', 0)}"
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            if offset(node.left) or offset(node.right):
                found.append(f"{where}: adds or subtracts one")
            elif _ord_of_a(node.left) or _ord_of_a(node.right):
                found.append(f"{where}: 65, which is ord('A') - letter arithmetic")
        elif isinstance(node, ast.AugAssign) \
                and isinstance(node.op, (ast.Add, ast.Sub)) and offset(node.value):
            found.append(f"{where}: adds or subtracts one")
        elif isinstance(node, ast.Call):
            name = called(node)
            if name in ("ord", "chr"):
                found.append(f"{where}: {name}() - letter arithmetic")
            elif name == "divmod":
                found.append(f"{where}: divmod() - splitting a linear position")
            elif name == "enumerate" and len(node.args) > 1:
                found.append(f"{where}: enumerate() with a start offset")
            elif table_lookup(node):
                found.append(f"{where}: .index() on a coordinate table - a "
                             f"conversion with no arithmetic in it")
        elif isinstance(node, ast.Attribute) \
                and node.attr in ("ascii_uppercase", "ascii_letters"):
            found.append(f"{where}: string.{node.attr} - a letter table")
        elif letter_table(node):
            found.append(f"{where}: a letter table")
    return sorted(set(found))


def _rightmost_name(node) -> str | None:
    """The last name in an attribute chain: `translate.ROWS` gives "ROWS"."""
    if isinstance(node, ast.Attribute):
        return node.attr
    return node.id if isinstance(node, ast.Name) else None


def _ord_of_a(node) -> bool:
    """The literal 65, which is `ord("A")` written without saying so."""
    return (isinstance(node, ast.Constant) and isinstance(node.value, int)
            and not isinstance(node.value, bool) and node.value == 65)


#: Protocol-layer names that carry a coordinate or a raw scale. Reaching for one
#: of these outside the boundary is how a second conversion gets written.
#:
#: **The criterion is what a name HANDS OVER, not whether it looks like a
#: conversion.** `protocol.stomp_assignments` reads as a plain query and returns
#: `StompAssignment(row, column, footswitch)` - three raw wire indexes, one of
#: them the footswitch index whose confusion with a column is the reason
#: `FootswitchLetter` exists at all. A model module that calls it and keys a
#: mapping by that footswitch reintroduces the original bug without writing a
#: single `- 1`, so the readers below are on the list next to the converters.
#:
#: Two tests keep this honest, in the two directions it can rot: every name here
#: must resolve in the protocol layer, and everything the boundary itself
#: delegates to must be here. Neither can tell whether a name the boundary does
#: NOT use is missing - that half is judgement, applied when a protocol-layer
#: name starts handing out a coordinate or a raw scale.
PROTOCOL_CONVERSIONS = {
    # conversions
    "Footswitch", "Scene", "slot_to_position", "position_to_slot",
    "input_level_db", "db_to_input_level", "lane_level_db", "db_to_lane_level",
    "tempo_bpm", "bpm_to_tempo", "option_at", "option_value",
    "UNITY_LEVEL", "HOLD_TIMING_MS",
    # readers that hand back raw wire coordinates. `Block` is now BOTH: it is
    # what `blocks()` returns AND the address every grid write is keyed to, so a
    # model module building one would be putting screen coordinates on the wire
    # - the exact bug this boundary exists to stop, and harder to spot than a
    # bare `- 1` because it reads like construction rather than conversion.
    "blocks", "Block", "stomp_assignments", "StompAssignment",
    "free_rows", "row_status", "RowStatus", "input_chain_rows",
    "splits", "Split",
    # `bypass_state` is here for what it HANDS OVER, not for what it takes:
    # `BypassState.scenes` is an eight-tuple keyed by the WIRE's scene index, so
    # a model module reading `scenes[1]` for scene B has done the conversion
    # this boundary exists to own - with no `- 1` anywhere in sight. That it
    # also TAKES a wire row and column is the other half.
    "bypass_state", "BypassState",
}
#: Deliberately NOT here, having been considered: `beats` (already keyed by the
#: 1-based BEAT the screen shows, so nothing is left to convert), `param_options`
#: (option NAMES, no coordinate and no scale) and `describe_mode` (names a mode
#: for a log line). Listing those would make the check fire on model code that
#: has no conversion to do, which teaches people to work around it.

#: Protocol-layer names the BOUNDARY uses that are not conversions, so every
#: model module may use them too.
#:
#: This exists because the derived check below holds the boundary to "everything
#: you reach for is a conversion, because converting is all you do". That was
#: true while the boundary converted scalars. It stopped being true when the
#: boundary started reading whole presets, because reading one needs a presence
#: check and the port enums, and neither carries a coordinate or a raw scale.
#: Without this set the check would force `field_present` onto the ban list -
#: and the root `CLAUDE.md` REQUIRES every model property that reads a device
#: field to call it, so that is a rule the codebase cannot have.
#:
#: The criterion is the one `PROTOCOL_CONVERSIONS` uses: what does the name HAND
#: OVER? A row, a slot, a scene index, a footswitch index or a raw scale belongs
#: above. A bool, a port id or a name does not.
#:
#: Keep it short and keep a reason on every entry. A real conversion parked here
#: instead of above is the one way this pair of lists can rot, and no test can
#: catch that - only a reviewer reading the reason can.
PROTOCOL_NON_CONVERSIONS = {
    # Hands over a bool about a field's presence. Every model property that
    # reads a device field is required to call it, so it could never be banned.
    "field_present",
    # Port ids, not coordinates. `row.output.destination` IS one of these, and
    # a port number is not a row, a slot, a scene or a footswitch.
    "Input", "Output",
    # Three members of `Output`, which the scan sees separately. Their NAMES
    # carry a row number but the values do not - they hand over 16, 17 and 18 -
    # and `translate.routes_to_a_row` deliberately refuses to report which row
    # they feed, because that reading is obvious rather than confirmed.
    "NEXT_ROW_3", "NEXT_ROW_4", "NEXT_ROW_3_4",
    # A value TYPE, which labels a number rather than converting one. ADR-0016
    # put these in the protocol layer on purpose - they name units the device
    # PUBLISHES, not coordinates this library chooses - so the boundary is
    # allowed to hand one back. `hz_to_tuner_reference` still does its own
    # arithmetic here, inside the boundary, and only wraps the result.
    "Hertz",
}


def test_a_module_hidden_in_a_subdirectory_is_caught_too():
    """The check above is only as good as the key it compares on.

    A stem-keyed version passed `translate/legacy/grid.py` as "grid", which is
    the exemption's own shape used against it. Proved rather than asserted,
    because the failure is silent: the arithmetic scan skips the file for
    exactly the reason it skips the real converters.
    """
    nested = BOUNDARY / "legacy" / "grid.py"
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("X = 1 - 1\n")
    try:
        found = {str(p.relative_to(BOUNDARY).with_suffix(""))
                 for p in sorted(BOUNDARY.rglob("*.py"))}
        assert not found <= BOUNDARY_MODULES, (
            "a module one directory down is invisible to the module list, so "
            "the arithmetic scan can be escaped by putting the conversion in "
            "translate/anything/")
    finally:
        nested.unlink()
        nested.parent.rmdir()


def test_the_two_protocol_lists_do_not_overlap():
    """A name on both lists is banned and permitted at once, and which one wins
    is whichever check runs. That is worse than either answer."""
    both = sorted(PROTOCOL_CONVERSIONS & PROTOCOL_NON_CONVERSIONS)
    assert not both, (
        f"{both} is on the ban list AND on the not-a-conversion list. Decide "
        f"which it is: does it hand over a coordinate or a raw scale?")


def test_every_non_conversion_is_a_real_protocol_name():
    """Same reason the ban list has this check: a misspelling here silently
    widens what the boundary is allowed to reach for."""
    missing = object()
    unresolved = sorted(
        name for name in PROTOCOL_NON_CONVERSIONS
        if getattr(protocol, name, missing) is missing
        and getattr(protocol.Output, name, missing) is missing
    )
    assert not unresolved, (
        f"{unresolved} is on the not-a-conversion list but is not a name the "
        f"protocol layer publishes, so it excuses nothing")


def _protocol_aliases(tree: ast.AST) -> set:
    """The local names that MEAN the protocol layer in `tree`.

    Worked out first, because the spellings that reach it are not all
    `protocol.`: a module can alias the package, import a submodule of it, or
    reach through two attributes at once
    (`protocol.QuadCortex.HOLD_TIMING_MS`). Each of those was a hole in the
    first version of this check, and each is one a person would write without
    any idea they were evading anything.
    """
    # `protocol` is seeded because the house style is
    # `from pyquadcortex import protocol`, and a module using that name without
    # the import in the same snippet is the ordinary case in a sample below.
    aliases = {"pyquadcortex", "protocol"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.startswith("pyquadcortex"):
                    aliases.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module == "pyquadcortex" or module.startswith("pyquadcortex.protocol"):
                for alias in node.names:
                    if module != "pyquadcortex" or alias.name == "protocol":
                        aliases.add(alias.asname or alias.name)
    return aliases


def _protocol_names_reached(tree: ast.AST) -> set:
    """Every protocol-layer name `tree` reaches for, conversion or not.

    The END of each attribute chain, not the pieces of it:
    `protocol.QuadCortex.HOLD_TIMING_MS` reaches for `HOLD_TIMING_MS`, and
    counting `QuadCortex` as well would make the allowlist test below demand a
    class be listed as a conversion.
    """
    aliases = _protocol_aliases(tree)
    stepped_through = {id(node.value) for node in ast.walk(tree)
                       if isinstance(node, ast.Attribute)}

    def root_of(node):
        """The leftmost name in an attribute chain, or None."""
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and id(node) not in stepped_through \
                and root_of(node.value) in aliases:
            found.add(node.attr)
        elif isinstance(node, ast.ImportFrom) \
                and (node.module or "").startswith("pyquadcortex.protocol"):
            found |= {a.name for a in node.names}
    return found


def _protocol_conversions_used(tree: ast.AST) -> list[str]:
    """Every protocol-layer CONVERSION name `tree` reaches for.

    Only through the protocol layer: `translate.slot_to_position(...)` is the
    boundary doing its job and must not be reported, so a bare attribute name is
    not enough to accuse on.
    """
    aliases = _protocol_aliases(tree)

    def root_of(node):
        while isinstance(node, ast.Attribute):
            node = node.value
        return node.id if isinstance(node, ast.Name) else None

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in PROTOCOL_CONVERSIONS \
                and root_of(node.value) in aliases:
            found.append(node.attr)
        elif isinstance(node, ast.ImportFrom) \
                and (node.module or "").startswith("pyquadcortex.protocol"):
            found += [a.name for a in node.names if a.name in PROTOCOL_CONVERSIONS]
    return sorted(set(found))


def test_the_scan_covers_every_module_that_is_not_the_protocol_layer():
    """Guards both checks below. An empty list passes them vacuously, and a
    scan that skipped a module enforces nothing in it.

    Checked against the import machinery's own walk, so a module added tomorrow
    is covered the day it is created rather than the day somebody remembers to
    add it here.
    """
    assert set(BOUNDARY_SOURCES) <= set(MODEL_SOURCES)
    assert len(OTHER_MODEL_SOURCES) >= 2
    walked = {
        pathlib.Path(importlib.import_module(info.name).__file__).resolve()
        for info in pkgutil.walk_packages(pyquadcortex.__path__, "pyquadcortex.")
        if not info.name.startswith("pyquadcortex.protocol")
    }
    missed = sorted(str(p) for p in walked - set(MODEL_SOURCES))
    assert not missed, f"the scan does not cover {missed}"


def test_every_name_on_the_allowlist_is_a_real_protocol_name():
    """A misspelled entry protects nothing and says nothing about it.

    The check below only ever compares an attribute name against this set, so
    `tempo_bmp` in it would sit there looking like a rule while `protocol.tempo_bpm`
    went unwatched. Two lookups because `HOLD_TIMING_MS` hangs off `QuadCortex`
    and the rest are module-level.
    """
    missing = object()
    unresolved = sorted(
        name for name in PROTOCOL_CONVERSIONS
        if getattr(protocol, name, missing) is missing
        and getattr(protocol.QuadCortex, name, missing) is missing
    )
    assert not unresolved, (
        f"{unresolved} is on the allowlist but is not a name the protocol layer "
        f"publishes, so nothing is being kept out of the model by it")


def test_the_allowlist_covers_everything_the_boundary_delegates_to():
    """The direction the list actually rots in.

    A conversion arrives at the protocol layer, the boundary starts calling it,
    and the allowlist is not updated - so every OTHER module may call it too and
    nothing says a word. That is not hypothetical: PR #22 added `tempo_bpm` and
    `bpm_to_tempo`, and they sat unlisted until this branch put them in by hand.

    Derived rather than listed, so it cannot rot the same way: a name the
    boundary reaches for has to be ACCOUNTED FOR, in one list or the other,
    before this passes.

    It used to be stronger than that, and the weakening is worth stating.
    The rule was "whatever the boundary reaches for IS a conversion, because
    converting is all it does", with no second list at all. That held while the
    boundary converted scalars. Reading a whole preset broke it: `field_present`
    and the port enums are neither conversions nor bannable, so the choice was
    a second list or a false rule. What is left is still the direction that
    rots - a new conversion cannot arrive silently - but the reviewer now has to
    check WHICH list a new name went into. `PROTOCOL_NON_CONVERSIONS` carries a
    reason per entry for exactly that reading.
    """
    reached = set()
    for source in BOUNDARY_SOURCES:
        reached |= _protocol_names_reached(ast.parse(source.read_text()))
    assert reached, "the boundary reaches for nothing - this check is vacuous"
    unlisted = sorted(reached - PROTOCOL_CONVERSIONS - PROTOCOL_NON_CONVERSIONS)
    assert not unlisted, (
        f"the translation boundary delegates to the protocol layer's "
        f"{unlisted}, which is on neither list. If it hands over a row, a slot, "
        f"a scene or footswitch index, or a raw scale, put it in "
        f"PROTOCOL_CONVERSIONS so no other module may call it. If it does not, "
        f"put it in PROTOCOL_NON_CONVERSIONS with the reason.")


#: The four functions the exclusion exists for. Named, because "somewhere in
#: the translate package" is not the thing being protected - and a package is a
#: vaguer somewhere than a file was.
COORDINATE_CONVERTERS = ("row_to_wire", "row_from_wire",
                         "slot_to_wire", "slot_from_wire")


def test_the_boundary_itself_still_does_the_arithmetic():
    """The exclusion below has to be load-bearing. If the boundary stopped
    converting, the check would pass because nothing anywhere converts - which
    is the one failure a "no arithmetic elsewhere" test cannot see.

    Anchored to the four converters BY NAME rather than to the file. The
    file-wide version was satisfied by `_screen_number`'s error-message
    formatter (`allowed[-1] - allowed[0] + 1 == len(allowed)`), which converts
    nothing - so all four converters could have been rewritten
    `ROWS.index(row)`, arithmetic-free, and this backstop would have stayed
    green on that one line while the "nowhere else" check stayed silent too.
    """
    functions = {}
    for source in BOUNDARY_SOURCES:
        for node in ast.walk(ast.parse(source.read_text())):
            if isinstance(node, ast.FunctionDef):
                functions[node.name] = node
    gone = [name for name in COORDINATE_CONVERTERS if name not in functions]
    assert not gone, (
        f"the translation boundary no longer defines {gone} - this test names "
        f"the converters it is protecting, so a rename has to come through here")
    silent = [name for name in COORDINATE_CONVERTERS
              if not _index_arithmetic(functions[name])]
    assert not silent, (
        f"{silent} do no index arithmetic. If the conversion moved somewhere "
        f"else, the 'nowhere else' check below is now passing because nothing "
        f"anywhere converts")


@pytest.mark.parametrize("source", OTHER_MODEL_SOURCES, ids=lambda p: p.name)
def test_no_index_arithmetic_outside_the_boundary(source):
    found = _index_arithmetic(ast.parse(source.read_text()))
    assert not found, (
        f"{source.name} does index arithmetic - {found}. If that is a screen "
        f"value becoming a wire value it belongs in {BOUNDARY.name} with a "
        f"test, wherever in the package the file sits - an off-by-one here "
        f"edits a real row, just not the one intended, and reads back perfectly"
    )


@pytest.mark.parametrize("source", OTHER_MODEL_SOURCES, ids=lambda p: p.name)
def test_only_the_boundary_reaches_for_a_protocol_conversion(source):
    found = _protocol_conversions_used(ast.parse(source.read_text()))
    assert not found, (
        f"{source.name} uses the protocol layer's {found} directly. Convert "
        f"through {BOUNDARY.name} instead, so there is one account of what a "
        f"row, a slot, a letter or a dB means"
    )


ARITHMETIC_SAMPLES = [
    ("a bare decrement", "wire_row = row - 1", True),
    ("a bare increment", "row = wire_row + 1", True),
    ("an augmented one", "slot += 1", True),
    ("one on the left", "column = 1 - offset", True),
    ("hidden in a call", "qc.set_param(Block(row - 1, slot - 1))", True),
    ("a comprehension", "[s - 1 for s in slots]", True),
    ("a negated one", "wire_row = row + -1", True),
    ("a float one", "wire_row = row - 1.0", True),
    ("a bool one", "wire_row = row - True", True),
    ("a letter from an index", "letter = chr(ord('A') + index)", True),
    ("an index from a letter", "index = ord(letter) - ord('A')", True),
    ("a letter table lookup", "letter = 'ABCDEFGH'[index]", True),
    ("a letter table search", "index = 'ABCDEFGH'.index(letter)", True),
    ("a letter table under any name", "LETTERS = 'ABCDEFGH'", True),
    ("a letter table as a tuple",
     "LETTERS = ('A', 'B', 'C', 'D', 'E', 'F', 'G', 'H')", True),
    ("a letter table as a list", "LETTERS = ['A', 'B', 'C']", True),
    ("a letter table as dict keys", "INDEX = {'A': 0, 'B': 1, 'C': 2}", True),
    ("a letter table that starts late", "letter = 'BCDEFGH'[index]", True),
    ("the standard library's letter table",
     "import string\nletter = string.ascii_uppercase[index]", True),
    ("ord('A') without ord", "index = letter.encode()[0] - 65", True),
    ("splitting a linear position", "bank, letter = divmod(position, 8)", True),
    ("a one-based enumerate", "[(n, r) for n, r in enumerate(rows, 1)]", True),
    ("the boundary's own table, looked up",
     "from pyquadcortex.device import translate\n"
     "wire_row = translate.ROWS.index(row)", True),
    ("the same, imported bare", "wire_column = SLOTS.index(slot)", True),
    ("the boundary doing it", "wire_row = translate.row_to_wire(row)", False),
    ("arithmetic that is not off-by-one", "total = a + 2", False),
    ("a plain enumerate", "[(i, r) for i, r in enumerate(rows)]", False),
    ("a string that is not a letter table", "name = 'ABY Splitter'", False),
    ("an ordinary attribute", "name = block.device.name", False),
    ("a list of names, not letters", "MODELS = ['Amp', 'Bass', 'Cab']", False),
    ("index() on something that is not a coordinate table",
     "position = names.index(name)", False),
]


@pytest.mark.parametrize("label,source,detected", ARITHMETIC_SAMPLES,
                         ids=[s[0] for s in ARITHMETIC_SAMPLES])
def test_the_arithmetic_check_sees_what_it_claims_to(label, source, detected):
    """A check with blind spots enforces the rule only for the spellings
    somebody happened to think of, while reading as though it enforced all of
    them.

    The samples that earn their place are the ones a version of this check
    missed: letter arithmetic, a letter table in any of four containers,
    `divmod` on a preset position, the three ways of writing one that are not
    the token `1`, and `ROWS.index(row)` - which converts a coordinate using
    the boundary's own published table and contains no arithmetic at all.
    None of those is exotic. They are how the conversions this module owns get
    written by somebody writing them somewhere else.
    """
    assert bool(_index_arithmetic(ast.parse(source))) is detected


#: Spellings this check CANNOT see, asserted as unseen.
#:
#: A table of samples where every "should be caught" case is caught reads like
#: proof of a complete check. It is not one, and the honest way to say so is to
#: pin the gap rather than describe it: each of these needs a value followed
#: across statements or a table evaluated, which is past what an AST pass does.
#:
#: This test fails if one of them starts being caught. That is the point - the
#: entry moves up into `ARITHMETIC_SAMPLES` and the docs saying where the check
#: stops get corrected in the same edit.
ARITHMETIC_BLIND_SPOTS = [
    ("a one with a name", "OFFSET = 1\nwire_row = row - OFFSET"),
    ("a coordinate table under another name, built at run time",
     "GRID_ROWS = tuple(range(1, 5))\nwire_row = GRID_ROWS.index(row)"),
    ("a letter table under another name, sliced out of a longer one",
     "TAGS = 'ABCDEFGHIJ'[:8]\nletter = TAGS[index]"),
    ("arithmetic behind a helper somebody else wrote",
     "from elsewhere import to_wire\nwire_row = to_wire(row)"),
]


@pytest.mark.parametrize("label,source", ARITHMETIC_BLIND_SPOTS,
                         ids=[s[0] for s in ARITHMETIC_BLIND_SPOTS])
def test_the_arithmetic_check_says_where_it_stops(label, source):
    """See `ARITHMETIC_BLIND_SPOTS`. These are the ones known to get through."""
    assert not _index_arithmetic(ast.parse(source)), (
        f"the check now catches {label!r} - move it into ARITHMETIC_SAMPLES and "
        f"correct the 'what it cannot see' paragraph in _index_arithmetic")


CONVERSION_SAMPLES = [
    ("an attribute", "x = protocol.Footswitch.A", True),
    ("through the package", "x = pyquadcortex.protocol.lane_level_db(v)", True),
    ("an import", "from pyquadcortex.protocol import slot_to_position", True),
    ("a module import", "from pyquadcortex.protocol.client import UNITY_LEVEL", True),
    ("two attributes deep", "x = protocol.QuadCortex.HOLD_TIMING_MS", True),
    ("an aliased package",
     "from pyquadcortex import protocol as p\nx = p.Footswitch.A", True),
    ("an imported submodule",
     "from pyquadcortex.protocol import client\nx = client.lane_level_db(v)", True),
    ("an aliased submodule",
     "from pyquadcortex.protocol import client as c\nx = c.UNITY_LEVEL", True),
    ("a plain import",
     "import pyquadcortex.protocol\nx = pyquadcortex.protocol.Scene.A", True),
    ("the boundary's own name", "x = translate.slot_to_position(name)", False),
    ("a protocol name that is not a conversion",
     "x = protocol.field_present(reply, 'serial')", False),
    ("something else entirely, named the same", "x = self.grid.Scene.A", False),
]


@pytest.mark.parametrize("label,source,detected", CONVERSION_SAMPLES,
                         ids=[s[0] for s in CONVERSION_SAMPLES])
def test_the_reach_past_the_boundary_check_sees_what_it_claims_to(label, source,
                                                                  detected):
    """Same standard as the arithmetic samples: the entries worth having are
    the spellings the first version of this check could not see."""
    assert bool(_protocol_conversions_used(ast.parse(source))) is detected


# -- what a caller can import ------------------------------------------------


def test_the_letter_types_and_the_address_are_public():
    """A caller holds these: `preset.stomps[FootswitchLetter.E]` and
    `device.recall(PresetAddress.parse("28C"))`. The conversion functions are
    not published - they are the seam's own business."""
    import pyquadcortex

    assert pyquadcortex.FootswitchLetter is translate.FootswitchLetter
    assert pyquadcortex.SceneLetter is translate.SceneLetter
    assert pyquadcortex.PresetAddress is translate.PresetAddress
    assert not hasattr(pyquadcortex, "row_to_wire")


# -- a wire preset, read in the numbers the screen shows ----------------------
#
# The conversions above are checked one value at a time. These read a REAL
# preset payload, because the mistakes that survive a unit-value test are the
# ones about shape: every row reports eight slots whether or not they hold
# anything, a branch's columns are not on the splitter block, and the bypass
# table is keyed by wire scene index.

PRESETS = pathlib.Path(__file__).parent / "fixtures" / "presets"


def _preset_fixture(name):
    payload = preset_pb.BinaryPreset()
    payload.ParseFromString((PRESETS / name).read_bytes())
    return payload


@pytest.fixture
def structural():
    return _preset_fixture("structural_preset.bin")


@pytest.fixture
def split():
    return _preset_fixture("split_preset.bin")


def test_placed_blocks_are_numbered_the_way_the_screen_numbers_them(structural):
    """The fixture's first block is wire row 0 column 0, which is row 1 slot 1."""
    placed = translate.placed_blocks(structural)
    assert placed[0].row == 1 and placed[0].slot == 1
    assert {b.row for b in placed} == {1, 3}, (
        "the fixture holds blocks on wire rows 0 and 2")
    assert max(b.slot for b in placed) == 8, "wire column 7 is slot 8"
    assert min(b.slot for b in placed) == 1


def test_every_placed_block_agrees_with_the_protocol_layer(structural):
    """Same cells, two vocabularies. The only difference must be the numbering."""
    wire = protocol.blocks(structural)
    screen = translate.placed_blocks(structural)
    assert len(screen) == len(wire)
    for w, s in zip(wire, screen):
        assert s.row == translate.row_from_wire(w.row)
        assert s.slot == translate.slot_from_wire(w.column)
        assert s.device_id == w.model_id


def test_only_rows_1_and_3_can_start_a_branch():
    assert translate.SPLITTABLE_ROWS == (1, 3)


def test_path_b_is_the_row_below():
    assert translate.path_b_of(1) == 2
    assert translate.path_b_of(3) == 4


@pytest.mark.parametrize("row", [2, 4])
def test_a_row_that_cannot_branch_has_no_path_b(row):
    with pytest.raises(ValueError, match="1 or 3"):
        translate.path_b_of(row)


def test_a_preset_with_no_branch_reports_none(structural):
    assert translate.branches(structural) == ()


def test_the_split_fixture_still_holds_the_two_shapes_it_is_for(split):
    """The fixture is DERIVED (see make_split_preset.py). If it were ever
    regenerated from a serial source, every branch test below would pass by
    reading nothing, so the fixture's own shape is asserted first."""
    wire = protocol.splits(split)
    assert len(wire) == 2, "one branch that rejoins, one that does not"
    assert [s.rejoins for s in wire] == [False, True]


def test_a_branch_is_numbered_the_way_the_screen_numbers_it(split):
    branch = translate.branches(split)[0]
    assert branch.row == 1, "wire row 0 is screen row 1"
    assert branch.at == 3, "wire column 2 is slot 3"
    assert branch.path_b == 2


def test_a_branch_that_never_rejoins_says_so(split):
    """`mix` is -1 for a lane that never recombines, and -1 is a real column
    number away from being read as one. It has to come back as None."""
    assert translate.branches(split)[0].rejoins_at is None


def test_a_branch_that_rejoins_reports_where(split):
    branch = translate.branches(split)[1]
    assert branch.row == 3
    assert branch.at == 4, "wire column 3 is slot 4"
    assert branch.rejoins_at == 5, "wire column 4 is slot 5"
    assert branch.path_b == 4


def test_the_splitter_and_the_mixer_are_not_the_same_slot(split):
    """A reader that returned `at` for `rejoins_at` would pass a test whose
    fixture branched and rejoined in the same column."""
    branch = translate.branches(split)[1]
    assert branch.at != branch.rejoins_at


def test_every_branch_agrees_with_the_protocol_layer(split):
    wire = protocol.splits(split)
    screen = translate.branches(split)
    assert len(screen) == len(wire)
    for w, s in zip(wire, screen):
        assert s.row == translate.row_from_wire(w.row)
        assert s.at == translate.slot_from_wire(w.split_column)
        assert s.path_b == translate.row_from_wire(w.lane_row)
        if w.rejoins:
            assert s.rejoins_at == translate.slot_from_wire(w.mix_column)
        else:
            assert s.rejoins_at is None


def test_a_wire_column_no_row_has_is_refused_when_a_preset_is_read(structural):
    """The renumbering VALIDATES rather than just adding one. A preset carrying
    column 99 is a preset something wrote wrongly, and reporting slot 100 would
    pass it on as though the screen could show it."""
    structural.chains[0].models[0].column = 99
    with pytest.raises(ValueError, match="0 to 7"):
        translate.placed_blocks(structural)


def test_the_row_input_is_the_port_the_unit_reports(structural):
    assert translate.row_input(structural, 1) == protocol.Input.INPUT_1


def test_an_output_that_feeds_another_row_is_recognised(structural):
    destination = translate.row_output(structural, 1)
    assert destination == protocol.Output.NEXT_ROW_3
    assert translate.routes_to_a_row(destination)


def test_a_real_destination_is_not_a_row(structural):
    assert not translate.routes_to_a_row(translate.row_output(structural, 3))
    assert not translate.routes_to_a_row(protocol.Output.XLR_1_2)


def test_a_row_the_screen_does_not_show_has_no_chain(structural):
    with pytest.raises(ValueError, match="1 to 4"):
        translate.row_input(structural, 5)


def test_bypass_reads_through_the_scene_letter(structural):
    """The wire keys the eight bypass slots by scene INDEX; the model asks by
    letter, and this is the only place that mapping happens."""
    wire = protocol.bypass_state(structural, Block(0, 0))
    for letter in LETTERS:
        assert translate.block_bypassed(structural, 1, 1, letter) is \
            wire.scenes[protocol.Scene[letter]]


def test_bypass_tells_two_scenes_apart(structural):
    """The fixture stores the same flag in all eight, so drive them apart first
    - otherwise this test passes on a reader that ignores the scene entirely."""
    cell = structural.bypass[0].colBypass[0]
    cell.sceneMode = True
    cell.sceneBypass[0].bypass = True
    cell.sceneBypass[1].bypass = False
    assert translate.block_bypassed(structural, 1, 1, "A") is True
    assert translate.block_bypassed(structural, 1, 1, "B") is False


def test_bypass_refuses_a_bare_scene_number(structural):
    with pytest.raises(TypeError):
        translate.block_bypassed(structural, 1, 1, 1)


def test_a_scene_name_is_read_by_letter(structural):
    assert translate.scene_name(structural, "A") == "Scene A"
    assert translate.scene_name(structural, translate.SceneLetter.H) == "Scene H"


def test_an_unlabelled_scene_reads_as_no_name(structural):
    """The unit stores a single space for "no label" and shows the letter
    instead, so `label == ""` does not detect it (protocol.SCENE_UNLABELLED)."""
    structural.scene_labels[1] = protocol.SCENE_UNLABELLED
    assert translate.scene_name(structural, "B") == ""


def test_a_scene_name_refuses_a_bare_number(structural):
    with pytest.raises(TypeError):
        translate.scene_name(structural, 1)


def test_a_scene_letter_does_not_equal_a_footswitch_letter():
    """The module header says a scene letter reaching a footswitch API has to be
    a type error. The converters enforced that at their doors; the VALUE TYPES
    did not, so `SceneLetter.A == FootswitchLetter.A` was True and a mapping
    keyed by one answered to the other - and `preset.stomps` is documented as
    exactly such a mapping."""
    assert translate.SceneLetter.A != translate.FootswitchLetter.A
    assert translate.FootswitchLetter.E != translate.SceneLetter.E
    assert {translate.FootswitchLetter.E: "vibe"}.get(translate.SceneLetter.E) is None
    assert translate.SceneLetter.B not in {translate.FootswitchLetter.B}


def test_a_letter_still_behaves_like_the_string_it_prints_as():
    """The whole reason these are strings. Telling the two enums apart must not
    cost `scenes["B"]`, printing, or keying a plain dict by the letter."""
    for letter in (translate.SceneLetter.E, translate.FootswitchLetter.E):
        assert letter == "E"
        assert "E" == letter
        assert str(letter) == "E"
        assert {letter: "x"}["E"] == "x"
        assert {"E": "x"}[letter] == "x"
        assert letter in ("E", "F")


def test_a_letter_still_equals_itself():
    assert translate.SceneLetter.A == translate.SceneLetter.A
    assert translate.SceneLetter("A") == translate.SceneLetter.A
    assert len({translate.SceneLetter.A, translate.SceneLetter("A")}) == 1

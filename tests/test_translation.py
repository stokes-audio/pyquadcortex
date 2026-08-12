"""The one place screen coordinates and display units become wire values.

Design principle 5 in ``docs/domain-model.md``: the model speaks touchscreen
coordinates and display units everywhere, and the conversion happens in exactly
one module. These tests are deliberately exhaustive, because the bug they exist
to stop is silent - the protocol layer's own header says an edit to the wrong row
"lands on a real row, just not the one intended, and it reads back perfectly".
Nothing on the unit and nothing in the reply tells you. So a test is the only
thing that can.

Two of the checks below are structural rather than behavioural: they read the
model package's source and prove no other module does the arithmetic or reaches
past the boundary for a protocol helper that does.
"""
import ast
import pathlib

import pytest

from pyquadcortex import device, protocol
from pyquadcortex.device import translate


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


@pytest.mark.parametrize("malformed", [
    "", " ", "C", "28", "28I", "0A", "33A", "28CC", "-1A", "2.5C", "A28",
])
def test_a_malformed_address_is_refused_when_it_is_parsed(malformed):
    """Not when it is written. A bad address that survives parsing becomes a
    wire position, and a wrong position is a preset that recalls fine and is the
    wrong preset."""
    with pytest.raises(ValueError):
        translate.PresetAddress.parse(malformed)


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


def test_the_address_conversion_says_the_naming_depends_on_the_mode():
    """An address is only unambiguous alongside the mode it was read in: linear
    position 5 reads "1F" normally and "2B" under a PRESET-containing HYBRID.
    A caller who does not know that will mis-address a preset, so the function
    that converts has to say it."""
    doc = translate.slot_to_position.__doc__
    assert "mode" in doc and "unambiguous" in doc
    assert "HYBRID" in doc or "hybrid" in doc


# -- display units -----------------------------------------------------------
#
# Each of these has a protocol-layer helper that already carries the measurement
# and its evidence. The model must not restate the arithmetic, so these tests
# check the boundary against that helper rather than against a number retyped
# here - a retyped constant agrees with itself forever.


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


def test_the_two_level_scales_are_not_interchangeable():
    """Both are a 0..1 wire value and they mean different dB. Reading a lane
    level with the input mapping is a wrong answer that looks plausible."""
    assert translate.input_level_db(0.5) != translate.lane_level_db(0.5)


# -- the tuner's reference pitch: absolute Hz on screen, an offset on the wire


@pytest.mark.parametrize("hz,offset", [(440.0, 0.0), (442.0, 2.0), (445.0, 5.0),
                                       (436.5, -3.5)])
def test_the_tuner_reference_converts_both_ways(hz, offset):
    assert translate.hz_to_tuner_reference(hz) == pytest.approx(offset)
    assert translate.tuner_reference_hz(offset) == pytest.approx(hz)


def test_the_tuner_reference_is_what_the_protocol_write_expects():
    """The reference is the protocol method itself: 442 Hz on screen broadcast
    `frequency: 1.99999809`, so the wire carries the offset from 440."""
    recorder = Recorder()
    qc = protocol.QuadCortex(recorder)
    qc.set_tuner_reference(translate.hz_to_tuner_reference(442.0))
    assert recorder.sent[-1].frequency == pytest.approx(2.0)


def test_the_tuner_reference_refuses_something_that_is_not_a_pitch():
    with pytest.raises(TypeError):
        translate.hz_to_tuner_reference("442")


# -- hold timing: milliseconds on screen, one of six indexes on the wire ------


@pytest.mark.parametrize("index", range(6))
def test_every_hold_timing_index_reads_as_the_screens_milliseconds(index):
    reference = protocol.QuadCortex.HOLD_TIMING_MS
    assert translate.hold_timing_ms(index) == reference[index]
    assert translate.ms_to_hold_timing(reference[index]) == index


def test_hold_timing_is_what_the_protocol_write_expects():
    recorder = Recorder()
    qc = protocol.QuadCortex(recorder)
    qc.set_hold_timing(800)
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


# -- the boundary is the ONLY place -----------------------------------------
#
# Everything above proves the conversions are right. These two prove they are
# the only ones, which is the half a reviewer cannot check by reading a diff:
# a stray `- 1` in a future module is one character, looks deliberate, and
# produces an edit that lands on a real row and reads back perfectly.

BOUNDARY = pathlib.Path(translate.__file__).resolve()
MODEL_SOURCES = sorted(
    p for p in pathlib.Path(device.__file__).resolve().parent.rglob("*.py"))
OTHER_MODEL_SOURCES = [p for p in MODEL_SOURCES if p != BOUNDARY]


def _off_by_one_arithmetic(tree: ast.AST) -> list[int]:
    """Line numbers of every `x + 1` / `x - 1` / `x += 1` in `tree`.

    Blunt on purpose. A rule that only fired on operands spelled `row` or `slot`
    would miss `n - 1`, and `n` is what the arithmetic is called by the time
    someone has extracted a helper for it.
    """
    def is_one(node) -> bool:
        return (isinstance(node, ast.Constant)
                and type(node.value) is int and node.value == 1)

    hits = []
    for node in ast.walk(tree):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub)):
            if is_one(node.left) or is_one(node.right):
                hits.append(node.lineno)
        elif isinstance(node, ast.AugAssign) \
                and isinstance(node.op, (ast.Add, ast.Sub)) and is_one(node.value):
            hits.append(node.lineno)
    return sorted(set(hits))


#: Protocol-layer names that carry a coordinate or a raw scale. Reaching for one
#: of these outside the boundary is how a second conversion gets written.
PROTOCOL_CONVERSIONS = {
    "Footswitch", "Scene", "slot_to_position", "position_to_slot",
    "input_level_db", "db_to_input_level", "lane_level_db", "db_to_lane_level",
    "UNITY_LEVEL", "HOLD_TIMING_MS",
}


def _protocol_conversions_used(tree: ast.AST) -> list[str]:
    """Every protocol-layer conversion name `tree` reaches for.

    Only through the protocol layer: `translate.slot_to_position(...)` is the
    boundary doing its job and must not be reported, so a bare attribute name is
    not enough to accuse on.
    """
    def is_the_protocol_layer(node) -> bool:
        return ((isinstance(node, ast.Name) and node.id == "protocol")
                or (isinstance(node, ast.Attribute) and node.attr == "protocol"))

    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in PROTOCOL_CONVERSIONS \
                and is_the_protocol_layer(node.value):
            found.append(node.attr)
        elif isinstance(node, ast.ImportFrom) \
                and (node.module or "").startswith("pyquadcortex.protocol"):
            found += [a.name for a in node.names if a.name in PROTOCOL_CONVERSIONS]
    return sorted(set(found))


def test_the_source_walk_found_the_model_package():
    """Guards both checks below: an empty list passes them vacuously."""
    assert BOUNDARY in MODEL_SOURCES
    assert len(OTHER_MODEL_SOURCES) >= 2


def test_the_boundary_itself_does_the_arithmetic():
    """The exclusion below has to be load-bearing. If the boundary stopped
    converting, the check would pass because nothing anywhere converts - which
    is the one failure a "no arithmetic elsewhere" test cannot see."""
    hits = _off_by_one_arithmetic(ast.parse(BOUNDARY.read_text()))
    assert hits, f"{BOUNDARY.name} does no +1/-1 arithmetic at all"


@pytest.mark.parametrize("source", OTHER_MODEL_SOURCES, ids=lambda p: p.name)
def test_no_index_arithmetic_outside_the_boundary(source):
    hits = _off_by_one_arithmetic(ast.parse(source.read_text()))
    assert not hits, (
        f"{source.name} does +1/-1 arithmetic at line(s) {hits}. If that is a "
        f"screen coordinate becoming a wire index, it belongs in "
        f"{BOUNDARY.name} with a test - an off-by-one here edits a real row, "
        f"just not the one intended, and reads back perfectly"
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
    ("hidden in a call", "qc.set_param(row=row - 1, column=slot - 1)", True),
    ("a comprehension", "[s - 1 for s in slots]", True),
    ("the boundary doing it", "wire_row = translate.row_to_wire(row)", False),
    ("arithmetic that is not off-by-one", "total = a + 2", False),
    ("a true that is not a one", "flag = other + True", False),
]


@pytest.mark.parametrize("label,source,detected", ARITHMETIC_SAMPLES,
                         ids=[s[0] for s in ARITHMETIC_SAMPLES])
def test_the_arithmetic_check_sees_what_it_claims_to(label, source, detected):
    """A check with blind spots enforces the rule only for the spellings
    somebody happened to think of."""
    assert bool(_off_by_one_arithmetic(ast.parse(source))) is detected


CONVERSION_SAMPLES = [
    ("an attribute", "x = protocol.Footswitch.A", True),
    ("through the package", "x = pyquadcortex.protocol.lane_level_db(v)", True),
    ("an import", "from pyquadcortex.protocol import slot_to_position", True),
    ("a module import", "from pyquadcortex.protocol.client import UNITY_LEVEL", True),
    ("the boundary's own name", "x = translate.slot_to_position(name)", False),
    ("a protocol name that is not a conversion",
     "x = protocol.field_present(reply, 'serial')", False),
]


@pytest.mark.parametrize("label,source,detected", CONVERSION_SAMPLES,
                         ids=[s[0] for s in CONVERSION_SAMPLES])
def test_the_reach_past_the_boundary_check_sees_what_it_claims_to(label, source,
                                                                  detected):
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

"""The one place a screen value becomes a wire value, and back.

The model speaks what the touchscreen shows: rows 1 to 4, slots 1 to 8, scenes
and footswitches as letters, levels in dB, the tuner in Hz. The wire speaks
zero-based indexes and raw scales. Every conversion between the two lives here,
and nowhere else in :mod:`pyquadcortex.device` - design principle 5 in
``docs/domain-model.md``.

**Why one module rather than a convention.** The protocol layer's own header says
it plainly: rows are zero-based, "getting this wrong is quiet rather than loud -
an edit lands on a real row, just not the one intended, and it reads back
perfectly". There is no error, no wrong-looking value, and no complaint from the
unit. A ``- 1`` written in the wrong place is therefore invisible until someone
plays the preset. Collecting the arithmetic in one module makes it reviewable in
one place, and ``tests/test_translation.py`` proves the rest of the model package
contains none of it.

Nothing here talks to a device. These are pure functions and value types, so they
are cheap to test exhaustively, which is the point.

Two words carry two meanings in this file, both of them the unit's own:

* a **slot** is one of the eight cells in a grid row (``row.slots[3]``), and it
  is also a preset's place in a setlist ("28C"). The design doc uses both. The
  grid sense converts with :func:`slot_to_wire`; the setlist sense with
  :func:`slot_to_position` and :class:`PresetAddress`.
* a **position** is the letter part of a preset address ("C") to the model, and
  the linear index of that address (218) to the wire.
"""

import enum
import re
from dataclasses import dataclass

from pyquadcortex import protocol

#: Rows on the touchscreen, top to bottom. The wire numbers the same four 0 to 3.
ROWS = (1, 2, 3, 4)

#: The eight cells in a row, as the manual counts them ("four rows, each
#: containing eight device block slots"). The wire calls a cell a ``column`` and
#: numbers them 0 to 7.
SLOTS = (1, 2, 3, 4, 5, 6, 7, 8)

#: The tuner's reference pitch when the wire offset is zero. The wire stores an
#: OFFSET from this, not the pitch itself - see :func:`tuner_reference_hz`.
CONCERT_A_HZ = 440.0

# What the wire may carry for each of the above, derived from them so the two
# accounts cannot disagree about how many there are. Scenes and footswitches
# share the eight-index range with slots.
_WIRE_ROWS = tuple(range(len(ROWS)))
_WIRE_COLUMNS = tuple(range(len(SLOTS)))
_LETTERS = "ABCDEFGH"

#: Only the three value types are re-exported from :mod:`pyquadcortex`; a caller
#: holds those. The conversions are the seam's own business and are reached as
#: ``translate.row_to_wire(...)`` from inside the model.
__all__ = [
    "ROWS", "SLOTS", "CONCERT_A_HZ",
    "FootswitchLetter", "SceneLetter", "PresetAddress",
    "row_to_wire", "row_from_wire", "slot_to_wire", "slot_from_wire",
    "footswitch_to_wire", "footswitch_from_wire",
    "scene_to_wire", "scene_from_wire",
    "slot_to_position", "position_to_slot",
    "input_level_db", "db_to_input_level",
    "lane_level_db", "db_to_lane_level",
    "tuner_reference_hz", "hz_to_tuner_reference",
    "hold_timing_ms", "ms_to_hold_timing",
]


def _screen_number(value, what: str, allowed: tuple) -> int:
    """Check one screen coordinate before it is converted.

    ``bool`` is refused explicitly because it is a subclass of ``int`` and
    ``True == 1``: an unguarded check converts ``True`` to the first row or slot
    and edits it, which is exactly the silent wrong answer this module exists to
    prevent. A float is refused for the same reason in slower motion - ``1.0``
    means the caller is computing coordinates in a type that rounds.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an int, not {type(value).__name__} ({value!r})"
        )
    if value not in allowed:
        raise ValueError(
            f"{what} must be {allowed[0]} to {allowed[-1]} - the unit shows "
            f"{len(allowed)} of them; got {value}"
        )
    return value


def _a_number(value, what: str) -> float:
    """A real number, and not a bool wearing one's clothes."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{what} must be a number, not {type(value).__name__} ({value!r})")
    return float(value)


# -- coordinates: rows and slots --------------------------------------------


def row_to_wire(row: int) -> int:
    """The screen's row number (1-4) as the wire's row index (0-3)."""
    return _screen_number(row, "a row", ROWS) - 1


def row_from_wire(index: int) -> int:
    """The wire's row index (0-3) as the row number the screen shows (1-4)."""
    return _screen_number(index, "a wire row index", _WIRE_ROWS) + 1


def slot_to_wire(slot: int) -> int:
    """A row's slot number (1-8) as the wire's column index (0-7).

    The manual calls the eight cells in a row slots; the wire calls the same
    thing a column. Same cell, two vocabularies, and this is the seam.
    """
    return _screen_number(slot, "a slot", SLOTS) - 1


def slot_from_wire(column: int) -> int:
    """The wire's column index (0-7) as the slot number the screen shows (1-8)."""
    return _screen_number(column, "a wire column index", _WIRE_COLUMNS) + 1


# -- letters: scenes and footswitches ---------------------------------------


class FootswitchLetter(enum.StrEnum):
    """A footswitch, as the unit labels it: A to H.

    **The model's only public footswitch key.** A footswitch index is not a
    column, and the two are equal often enough to look like the same number:
    ``stomp_is_momentary`` is keyed by footswitch index, and that stayed hidden
    for months because every sample happened to have the two agree - until a
    block at column 3 assigned to footswitch E came back keyed 4
    (``docs/domain-model.md`` section 7). Documenting the difference was not
    enough, so the model takes a letter and the zero-based index stays inside the
    protocol layer, where :class:`~pyquadcortex.protocol.enums.Footswitch`
    already lives.

    It is a ``str``, so it prints as the screen shows it and keys an ordinary
    mapping::

        preset.stomps[FootswitchLetter.E]
        preset.stomps["E"]                  # the same key
    """

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"


class SceneLetter(enum.StrEnum):
    """A scene, as the unit labels it: A to H. A ``str``, like
    :class:`FootswitchLetter`."""

    A = "A"
    B = "B"
    C = "C"
    D = "D"
    E = "E"
    F = "F"
    G = "G"
    H = "H"


def _letter(value, kind: type, what: str, trap: str):
    """Coerce a caller's letter into `kind`, refusing a number outright.

    A number is refused rather than converted even though the wire is numeric.
    ``trap`` names what that number would more likely have been - the mistake the
    letter types exist to make impossible.
    """
    if isinstance(value, kind):
        return value
    if isinstance(value, bool) or isinstance(value, (int, float)):
        raise TypeError(
            f"{what} is a letter A to H, not the number {value!r} - the model "
            f"never takes a bare index here, because {trap}"
        )
    if not isinstance(value, str):
        raise TypeError(
            f"{what} is a letter A to H, not {type(value).__name__} ({value!r})")
    try:
        return kind(value.strip().upper())
    except ValueError:
        raise ValueError(
            f"{what} is a letter A to H - the unit shows eight; got {value!r}"
        ) from None


def footswitch_to_wire(footswitch) -> protocol.Footswitch:
    """A footswitch letter as the zero-based index the wire carries.

    Takes a :class:`FootswitchLetter` or the plain letter. An ``int`` is refused:
    see :class:`FootswitchLetter` for the block-at-column-3 case that makes a
    number here a write that silently lands on the wrong switch.
    """
    letter = _letter(footswitch, FootswitchLetter, "a footswitch",
                     "a footswitch index and a block's column are different "
                     "numbers that are equal often enough to look alike")
    return protocol.Footswitch[letter.value]


def footswitch_from_wire(index) -> FootswitchLetter:
    """The wire's footswitch index (0-7) as the letter the unit labels it with."""
    return FootswitchLetter(
        _LETTERS[_screen_number(index, "a wire footswitch index", _WIRE_COLUMNS)])


def scene_to_wire(scene) -> protocol.Scene:
    """A scene letter as the zero-based index the wire carries.

    Takes a :class:`SceneLetter` or the plain letter, as ``scenes["B"]`` does.
    """
    letter = _letter(scene, SceneLetter, "a scene",
                     "scene B is wire index 1, and a number here reads as "
                     "either one")
    return protocol.Scene[letter.value]


def scene_from_wire(index) -> SceneLetter:
    """The wire's scene index (0-7) as the letter the unit labels it with."""
    return SceneLetter(
        _LETTERS[_screen_number(index, "a wire scene index", _WIRE_COLUMNS)])


# -- preset addresses: "28C" on screen, a linear position on the wire -------


def slot_to_position(name: str) -> int:
    """A preset's slot name ("28C") as the linear position the wire carries (218).

    The letters run **A to H, eight to a bank** - the non-hybrid naming, which is
    what the unit shows in every mode except one. A PRESET-containing HYBRID mode
    halves the bank to four, so the SAME preset is named differently: linear
    position 5 reads "1F" normally and "2B" under that hybrid. So a slot name -
    and therefore a :class:`PresetAddress` - is only unambiguous alongside the
    mode it was read in. The linear position is not: it means one preset whatever
    the footswitches are doing, which is why it is what goes on the wire and why
    two addresses are best compared as positions.

    Delegates to :func:`pyquadcortex.protocol.slot_to_position`, so the model and
    the protocol layer cannot drift apart on what "28C" means. A zero-padded bank
    ("01A") is accepted; :func:`position_to_slot` renders unpadded by default,
    because that is what the unit displays.
    """
    return protocol.slot_to_position(name)


def position_to_slot(position: int, pad: bool = False) -> str:
    """The wire's linear position (218) as the slot name the unit shows ("28C").

    The inverse of :func:`slot_to_position`, and it carries the same caveat: the
    name it returns is the non-hybrid one, so it is only unambiguous alongside
    the mode the address was read in.

    Unpadded by default ("1A"), which is what the unit displays; ``pad=True``
    gives "01A".
    """
    return protocol.position_to_slot(position, pad=pad)


@dataclass(frozen=True)
class PresetAddress:
    """Where a preset lives, as the Directory shows it: a bank and a position.

    ``PresetAddress(28, "C")`` renders as ``"28C"`` and
    :meth:`parse` reads the same form back. Malformed input is refused here,
    when the address is built, rather than later when something writes it: a bad
    address that survives parsing turns into a wire position anyway, and the
    device recalls whatever preset is at that position without complaint.

    ``position`` is the letter, "A" to "H" - the non-hybrid naming. See
    :func:`slot_to_position` for why an address needs the mode beside it to be
    unambiguous, and why comparing positions beats comparing names.
    """

    bank: int
    position: str

    def __post_init__(self):
        if isinstance(self.bank, bool) or not isinstance(self.bank, int):
            raise TypeError(
                f"a bank is a number, not {type(self.bank).__name__} "
                f"({self.bank!r})")
        if not isinstance(self.position, str):
            raise TypeError(
                f"a position is a letter A to H, not "
                f"{type(self.position).__name__} ({self.position!r})")
        object.__setattr__(self, "position", self.position.strip().upper())
        # Validation is the protocol helper's, so there is one account of how big
        # a setlist is and what a slot name may look like.
        slot_to_position(f"{self.bank}{self.position}")

    def __str__(self) -> str:
        return f"{self.bank}{self.position}"

    def __repr__(self) -> str:
        return f"PresetAddress({str(self)!r})"

    @classmethod
    def parse(cls, text: str) -> "PresetAddress":
        """Read an address a person wrote: ``"28C"``, ``"01a"``, ``" 32H "``.

        Raises ``ValueError`` for anything that is not a bank number followed by
        a letter A to H, and ``TypeError`` for anything that is not text.
        """
        if not isinstance(text, str):
            raise TypeError(
                f"a preset address is text like '28C', not "
                f"{type(text).__name__} ({text!r})")
        match = re.fullmatch(r"\s*(\d+)\s*([A-Za-z])\s*", text)
        if not match:
            raise ValueError(
                f"a preset address is a bank number and a letter A to H, like "
                f"'28C': {text!r}")
        return cls(int(match.group(1)), match.group(2))

    @classmethod
    def from_wire(cls, position: int) -> "PresetAddress":
        """The address at a linear wire position: ``218`` gives ``"28C"``."""
        return cls.parse(position_to_slot(position))

    def to_wire(self) -> int:
        """This address as the linear position the wire carries."""
        return slot_to_position(str(self))


# -- display units ----------------------------------------------------------
#
# Each mapping below was measured on hardware and is documented at the protocol
# layer, on the helper that performs it. The model delegates rather than
# restating the arithmetic: two copies of a measured scale drift, and the drift
# is invisible because both copies still return a plausible number.


def input_level_db(level: float) -> float:
    """An input port's wire level (0..1) as the dB the unit displays.

    Input gain spans -12 to +60 dB. Delegates to
    :func:`pyquadcortex.protocol.input_level_db`, which carries the measurement.

    An input port and a lane are both a 0..1 wire value and they are NOT the
    same scale - see :func:`lane_level_db`.
    """
    return protocol.input_level_db(level)


def db_to_input_level(db: float) -> float:
    """Displayed input-gain dB as the wire level an input port takes.

    Refuses anything outside -12..+60 dB rather than clamping, because a clamped
    write lands and reads back as a value the caller never asked for.
    """
    return protocol.db_to_input_level(db)


def lane_level_db(value: float) -> float:
    """A lane, mixer or splitter LEVEL wire value (0..1) as displayed dB.

    These span -40 to +12 dB, with 0 dB at :data:`pyquadcortex.protocol.UNITY_LEVEL`
    (10/13). Delegates to :func:`pyquadcortex.protocol.lane_level_db`.

    The bottom of the knob is a detent, not a dB value: wire 0.0 reads "Off" on
    screen and -39.5 dB (wire 0.01) is the lowest numeric step. This converts the
    scale; it does not model the Off position.
    """
    return protocol.lane_level_db(value)


def db_to_lane_level(db: float) -> float:
    """Displayed dB as the wire value a lane, mixer or splitter LEVEL takes.

    Refuses anything outside -40..+12 dB. For silence write the wire's 0.0
    directly - the Off position - rather than converting a dB value.
    """
    return protocol.db_to_lane_level(db)


def tuner_reference_hz(offset: float) -> float:
    """The tuner's wire ``frequency`` as the absolute reference pitch on screen.

    The wire stores an OFFSET from 440 Hz, not the pitch: setting FREQ to 442 on
    the unit broadcast ``frequency: 1.99999809``. The screen shows 442, so the
    model does too.

    **Evidence:** that single observed pair (442 -> 2.0) is the whole of it. It
    fixes the zero point and the direction; that the unit is one Hz per unit
    rather than something that merely agrees at 2.0 has not been checked against
    a second value on screen, and this function says so rather than implying more
    (see :meth:`pyquadcortex.protocol.QuadCortex.set_tuner_reference`). No range
    is enforced for the same reason: the unit's FREQ limits have not been read,
    and a limit invented here would refuse a setting the unit allows.
    """
    return CONCERT_A_HZ + _a_number(offset, "a tuner reference offset")


def hz_to_tuner_reference(hz: float) -> float:
    """A reference pitch in Hz as the offset from 440 the wire carries.

    Inverse of :func:`tuner_reference_hz`; see it for the evidence and for why
    no range is enforced.
    """
    return _a_number(hz, "a tuner reference pitch") - CONCERT_A_HZ


def hold_timing_ms(index: int) -> int:
    """The wire's ``hold_timing`` index as the milliseconds the screen shows.

    Six settings, 500 to 1000 ms in 100 ms steps. The device accepts and stores
    any integer in that field without validating it, so an index outside the six
    means something wrote a value no screen can show - reported rather than
    rounded to the nearest real setting.
    """
    choices = protocol.QuadCortex.HOLD_TIMING_MS
    if isinstance(index, bool) or not isinstance(index, int) \
            or not 0 <= index < len(choices):
        raise ValueError(
            f"hold timing reads {index!r}, which is outside the "
            f"{len(choices)} values the unit offers - something wrote an "
            f"unvalidated value into it"
        )
    return choices[index]


def ms_to_hold_timing(milliseconds: int) -> int:
    """Milliseconds as the ``hold_timing`` index the wire carries.

    Only the six values the unit offers convert. Anything else is refused rather
    than rounded, because the device would store it and no gesture would match
    it.
    """
    choices = protocol.QuadCortex.HOLD_TIMING_MS
    try:
        return choices.index(int(milliseconds))
    except (ValueError, TypeError):
        raise ValueError(
            f"hold timing must be one of {list(choices)} ms, "
            f"not {milliseconds!r}"
        ) from None

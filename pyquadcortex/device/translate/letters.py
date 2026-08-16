"""Scenes and footswitches: letters A to H on screen, indexes 0 to 7 on the wire.

The two are separate types on purpose. Both label eight things A to H, both are
strings, and they are equal often enough to look like one idea - which is exactly
why a scene letter reaching a footswitch API has to be a type error.
"""

import enum

from pyquadcortex import protocol
from pyquadcortex.device.translate.guards import _a_whole_number


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

    The OTHER letter type is refused too. :class:`SceneLetter` and
    :class:`FootswitchLetter` are both ``StrEnum`` over A to H, so each is a
    plain string as far as any check goes, and scene E reaching a footswitch API
    is the same wrong-thing-right-shape mistake as passing the number 4.
    """
    if isinstance(value, kind):
        return value
    if isinstance(value, enum.Enum):
        raise TypeError(
            f"{what} is a {kind.__name__}; {value!r} is a "
            f"{type(value).__name__}, which labels something else"
        )
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
    """The wire's footswitch index (0-7) as the letter the unit labels it with.

    Takes a plain int or a :class:`~pyquadcortex.protocol.enums.Footswitch`. A
    :class:`~pyquadcortex.protocol.enums.Scene` is refused even though it is an
    ``IntEnum`` over the same eight numbers, because a scene index arriving here
    means something upstream mixed up two things the unit keeps apart.

    The letter comes from the protocol enum's own member name rather than a
    second copy of the alphabet, so the two layers cannot disagree about which
    index is which switch.
    """
    if not isinstance(index, protocol.Footswitch):
        _a_whole_number(index, "a wire footswitch index")
    return FootswitchLetter(protocol.Footswitch(index).name)


def scene_to_wire(scene) -> protocol.Scene:
    """A scene letter as the zero-based index the wire carries.

    Takes a :class:`SceneLetter` or the plain letter, as ``scenes["B"]`` does.
    """
    letter = _letter(scene, SceneLetter, "a scene",
                     "scene B is wire index 1, and a number here reads as "
                     "either one")
    return protocol.Scene[letter.value]


def scene_from_wire(index) -> SceneLetter:
    """The wire's scene index (0-7) as the letter the unit labels it with.

    Takes a plain int or a :class:`~pyquadcortex.protocol.enums.Scene`, and
    refuses a footswitch index for the reason in :func:`footswitch_from_wire`.
    """
    if not isinstance(index, protocol.Scene):
        _a_whole_number(index, "a wire scene index")
    return SceneLetter(protocol.Scene(index).name)

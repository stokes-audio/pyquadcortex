"""Where a preset lives: "28C" on screen, a linear position on the wire.

Note the two senses of *slot* this package carries, both of them the unit's own.
Here a slot is a preset's place in a setlist; in
:mod:`~pyquadcortex.device.translate.coordinates` it is one of the eight cells in
a grid row. A *position* is the letter part of an address ("C") to the model, and
the linear index of that address (218) to the wire.
"""

import re
from dataclasses import dataclass

from pyquadcortex import protocol
from pyquadcortex.device.translate.guards import _a_whole_number

#: What a slot name may look like to the model: ASCII digits, then one letter,
#: with nothing between them.
#:
#: The protocol helper is looser. It checks the bank with ``str.isdigit()``,
#: which is true for every Unicode digit, and ``int()`` reads those too - so
#: ``protocol.slot_to_position("٢٨C")`` returns 218. That is a real position for
#: a name no screen ever shows, which is the shape of mistake this module exists
#: to stop, so the model checks the name before handing it down. Both of the
#: boundary's doors use this one pattern; they disagreed when only
#: :meth:`PresetAddress.parse` had it.
_SLOT_NAME = re.compile(r"\s*([0-9]+)([A-Za-z])\s*")


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

    How big a setlist is, and the arithmetic, are
    :func:`pyquadcortex.protocol.slot_to_position`'s, so the model and the
    protocol layer cannot drift apart on what "28C" means. The SHAPE of the name
    is checked here first, against :data:`_SLOT_NAME`, because the protocol
    helper accepts non-ASCII digits and the model should not. A zero-padded bank
    ("01A") is accepted; :func:`position_to_slot` renders unpadded by default,
    because that is what the unit displays.
    """
    if not isinstance(name, str):
        raise TypeError(
            f"a slot name is text like '28C', not {type(name).__name__} "
            f"({name!r})")
    if not _SLOT_NAME.fullmatch(name):
        raise ValueError(
            f"a slot name is a bank number and a letter A to H, like '28C': "
            f"{name!r}")
    return protocol.slot_to_position(name)


def position_to_slot(position: int, pad: bool = False) -> str:
    """The wire's linear position (218) as the slot name the unit shows ("28C").

    The inverse of :func:`slot_to_position`, and it carries the same caveat: the
    name it returns is the non-hybrid one, so it is only unambiguous alongside
    the mode the address was read in.

    Unpadded by default ("1A"), which is what the unit displays; ``pad=True``
    gives "01A".

    A whole number only. The protocol helper takes ``int(position)``, so 218.9
    would quietly become 218 and ``True`` would become 1 - and unlike a bad row,
    a bad position names a real preset that recalls without complaint.
    """
    return protocol.position_to_slot(
        _a_whole_number(position, "a wire preset position"), pad=pad)


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
        _a_whole_number(self.bank, "a bank")
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
        # The same pattern :func:`slot_to_position` uses. Python's `\d` spans
        # every Unicode digit, and so does `str.isdigit()`, so an unrestricted
        # check reads "٢٨C" as bank 28 - see :data:`_SLOT_NAME`.
        match = _SLOT_NAME.fullmatch(text)
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

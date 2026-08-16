"""Rows and slots: what the screen numbers 1 to 4 and 1 to 8, and the wire 0 up.

The four converters here are the ones ``tests/test_translation.py`` names by
name. If they stop doing arithmetic, the "nowhere else does this" check below
them starts passing because nothing anywhere converts, so the test asserts they
still do.
"""

from pyquadcortex.device.translate.guards import _screen_number

#: Rows on the touchscreen, top to bottom. The wire numbers the same four 0 to 3.
ROWS = (1, 2, 3, 4)

#: The eight cells in a row, as the manual counts them ("four rows, each
#: containing eight device block slots"). The wire calls a cell a ``column`` and
#: numbers them 0 to 7.
SLOTS = (1, 2, 3, 4, 5, 6, 7, 8)

# What the wire may carry for a row and a slot, derived from the screen values
# above so the two accounts cannot disagree about how many there are. Scene and
# footswitch indexes are NOT validated against these: they have their own enums
# at the protocol layer, and borrowing a range named for grid columns to check a
# footswitch is the confusion this module exists to end.
_WIRE_ROWS = tuple(range(len(ROWS)))
_WIRE_COLUMNS = tuple(range(len(SLOTS)))


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

"""Type checks the conversions share.

Each one refuses something that is a silent wrong answer rather than a crash if
it gets through, which is why they are checks rather than casts.
"""

import enum


def _a_whole_number(value, what: str) -> int:
    """A plain ``int``, and nothing that is merely spelled like one.

    Three things are refused here, and each one is a silent wrong answer rather
    than a crash if it gets through:

    * ``bool``, because it subclasses ``int`` and ``True == 1``, so an unguarded
      check converts ``True`` to the first row or slot and edits it.
    * a ``float``, because ``1.0`` means the caller is computing coordinates in
      a type that rounds, and 218.9 becoming preset 218 recalls a real preset.
    * any :class:`enum.Enum`, because the protocol layer's coordinate enums are
      ``IntEnum``: :class:`~pyquadcortex.protocol.enums.Scene` ``B`` is 1, and
      handing it to a row converter otherwise produces row 2 without complaint.
      That is the footswitch-versus-column confusion in another costume. The two
      wire-index converters that legitimately take one of those enums unwrap it
      themselves, so only the RIGHT enum gets through.
    """
    if isinstance(value, enum.Enum):
        raise TypeError(
            f"{what} must be a plain int; {value!r} is a {type(value).__name__}, "
            f"which numbers something else"
        )
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(
            f"{what} must be an int, not {type(value).__name__} ({value!r})"
        )
    return value


def _screen_number(value, what: str, allowed: tuple) -> int:
    """One screen coordinate, checked against the values the unit shows."""
    _a_whole_number(value, what)
    if value not in allowed:
        contiguous = allowed[-1] - allowed[0] + 1 == len(allowed)
        span = (f"{allowed[0]} to {allowed[-1]}" if contiguous
                else f"one of {list(allowed)}")
        raise ValueError(f"{what} must be {span} - the unit has "
                         f"{len(allowed)} of them; got {value}")
    return value


def _a_number(value, what: str) -> float:
    """A real number, and not a bool wearing one's clothes."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(
            f"{what} must be a number, not {type(value).__name__} ({value!r})")
    return float(value)

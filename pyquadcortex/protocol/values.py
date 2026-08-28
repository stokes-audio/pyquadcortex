"""A parameter value that knows which SCALE it is on.

**Every knob on the unit has two number lines.**

The screen shows one of them. A lane volume runs from -40 dB to +12 dB, a drive's
GAIN from 0 to 10, a filter's cutoff from 20 Hz to 20000 Hz. Each knob has its
own, and they are all different.

The device stores the other. Every parameter, without exception, is kept as a
number from 0.0 to 1.0 - the same line for all 3,809 of them.

A value type is how you say which line your number is on::

    Real(0.0)      zero on the SCREEN's line
    Encoded(0.0)   zero on the DEVICE's line

On a lane volume those are opposite ends of the knob. Zero on the screen's line
is 0 dB, which is unity - full signal, nothing taken away. Zero on the device's
line is the bottom of the travel, which is silence.

Same number. Opposite results. That is why the type is required and not a
convenience: a bare ``0.0`` would be a coin flip between them.

**The unit types are a `Real` that also names its unit.** ``Db(-3.1)`` says the
same thing as ``Real(-3.1)`` and adds a claim: this parameter had better be in
dB. Hand it to one the catalog calls Hz and you get a ``TypeError`` instead of a
silently wrong write. Use ``Real`` when you do not want the check, or when the
parameter has no unit at all - 1,780 of them do not::

    qc.set_param(LaneOutput(0), "VOLUME", Db(-3.1))    # checked
    qc.set_param(block, "GAIN", Real(5.0))             # 5 of 0..10, no unit
    qc.set_param(block, 21, Encoded(0.5))              # an index the catalog omits

**The numbers, and what they are counted over.** The catalog holds 3,809
parameters. 539 carry an option list and take an enum or a bool rather than any
of this. Of the 3,270 that remain, 1,490 carry a unit and 1,780 do not - which
is why ``Real`` is the general case and the unit types narrow it, rather than a
wall of units with a hole in it.

**Two smaller things worth knowing.**

The catalog spells two units twice - ``Cents``/``cents`` and
``Semitones``/``st``. A type collapses each pair; a string comparison would not.

And on 279 parameters the two lines happen to coincide, because they are
unitless and run 0..1 anyway. There ``Real(0.5)`` and ``Encoded(0.5)`` write the
same thing. That is a coincidence, not a rule, and it does not hold for the
other 3,530.

**The unit is also checked STATICALLY**, where the caller names the parameter
with a generated constant (ADR-0018). `params.LaneOutputParam.VOLUME` is a
`Param[DbUnit]` - an ``int`` that also carries which unit its parameter is in -
so a type checker rejects ``Hertz`` there before the code runs. The runtime
check below is unchanged and still does the work for every other caller: a
string, a bare index, or anyone not running a checker.

See ADR-0016, ADR-0018, and
``docs/superpowers/specs/2026-08-27-typed-parameter-values-design.md``.
"""

from typing import Generic, TypeVar

#: The unit a parameter is in, as a TYPE rather than a string. These are marker
#: classes and are never instantiated - their whole job is to be different from
#: each other, so a checker can tell `Param[DbUnit]` from `Param[HzUnit]`.
#:
#: One per unit the catalog actually spells, plus `NoUnit` for the 1,780
#: parameters with none. `NoUnit` is not "unknown": it is a positive statement
#: that the parameter has no unit, which is what makes `Db` on a drive's GAIN a
#: static error rather than something that slips through.


class DbUnit: ...
class PercentUnit: ...
class HertzUnit: ...
class MillisecondsUnit: ...
class SecondsUnit: ...
class SemitonesUnit: ...
class CentsUnit: ...
class BpmUnit: ...
class NoUnit: ...


U = TypeVar("U")


class Param(int, Generic[U]):
    """A parameter's wire index, tagged with the unit it takes.

    An ``int`` at runtime and nothing more - it goes straight to the wire and
    needs no catalog, which is what makes a generated constant the cheap route
    as well as the checked one. The type argument exists only for the checker.

    ADR-0016 recorded this as possible and deferred it; ADR-0018 is where it
    ships, once a type checker was running in CI for it to mean anything.
    """

    # No `__slots__` here, unlike `Value` below, and not an oversight: CPython
    # refuses a non-empty `__slots__` on an `int` subclass, and an EMPTY one
    # would make `name` unassignable. These are a few thousand module-level
    # constants built once at import, so a `__dict__` each is not worth a
    # cleverer way of carrying one string.

    #: What the constant is called. `params.py` fills it in, so the generated
    #: constants keep the one `IntEnum` behaviour worth carrying over.
    name: str

    def __new__(cls, value: int, name: str = ""):
        self = super().__new__(cls, value)
        self.name = name
        return self

    def __repr__(self) -> str:
        return f"{self.name}({int(self)})" if self.name else f"Param({int(self)})"


class Value(float):
    """A parameter value that knows which scale it is on.

    A ``float`` subclass, so ``float(Db(-3.1))`` works and arithmetic works.
    What distinguishes a typed value from a bare number is the CLASS.
    :meth:`~pyquadcortex.protocol.QuadCortex.set_param` tests for
    :class:`Encoded` and :class:`Real` specifically rather than for this base,
    because the two take different paths - so a bare ``Value`` is refused along
    with a bare number, which is the safe answer for a type that says nothing.

    **Two values of different types compare EQUAL if their numbers match.**
    ``Db(1) == Hertz(1)`` is ``True``, and they hash together. That is inherited
    from ``float`` and is not worth fighting, but it means a test asserting a
    type with ``==`` would not fail - use ``isinstance`` or ``type(x) is``.
    Arithmetic strips the type instead: ``Db(1) + 1`` is a plain ``float``,
    which ``set_param`` then refuses, and that failure is loud.
    """

    __slots__ = ()

    def __repr__(self) -> str:
        return f"{type(self).__name__}({float(self)!r})"


class Encoded(Value):
    """The DEVICE's own scale: 0..1, identical for every parameter.

    Needs no catalog, which is what keeps an index-addressed write free of a
    round trip to fetch one.

    This is an escape hatch, not a route. It stays available because the wire
    carries more parameters than the catalog describes, and because a caller
    who has a wire value in hand should be able to write it. But where the
    catalog knows the parameter, :class:`Real` or a unit type says more, and
    the documentation points there instead.
    """

    __slots__ = ()


class Real(Value, Generic[U]):
    """The value on the PARAMETER's own scale, whatever that scale is.

    Generic so a checker can tell `Db` from `Hertz`, and UNPARAMETERISED here
    on purpose: a bare `Real` claims no unit, fits any `Param[U]`, and is the
    general case for the 1,780 parameters that have none. The subclasses below
    each bind one unit, which is what makes `Db` on an `Hz` parameter a static
    error as well as a runtime one.

    Use this where the parameter has no unit - a drive's ``GAIN`` runs 0..10 and
    means nothing more specific - or where you do not want the unit checked.
    1,780 of the 3,270 parameters that take a value at all are unitless with a
    real range, so this is the general case rather than a fallback.

    Where the parameter does have a unit, the matching subclass says so and gets
    it checked.
    """

    __slots__ = ()

    #: The catalog ``units`` strings this type claims. Empty means it claims
    #: nothing, which is what makes :class:`Real` itself accepted anywhere.
    CATALOG_UNITS: frozenset = frozenset()

    def check_unit(self, spec) -> None:
        """Raise ``TypeError`` if ``spec`` disagrees about the unit.

        Usually a catalog `Parameter`, but not always: a few SETTINGS have a
        real scale the catalog never mentions, and they describe it the same
        way so this check works unchanged. That is why the message names the
        parameter rather than crediting the catalog for knowing.
        """
        if not self.CATALOG_UNITS or spec.units in self.CATALOG_UNITS:
            return
        claimed = sorted(self.CATALOG_UNITS)[0]
        raise TypeError(
            f"{self!r} says this value is in {claimed}, and {spec.name!r} is in "
            f"{spec.units or 'no unit'}. Use the matching type, or "
            f"Real({float(self)!r}) to make no claim about the unit."
        )


class Db(Real[DbUnit]):
    """Decibels. 499 parameters, the largest unit population in the catalog."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"dB"})


class Percent(Real[PercentUnit]):
    """Percent."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"%"})


class Hertz(Real[HertzUnit]):
    """Hertz."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Hz"})


class Milliseconds(Real[MillisecondsUnit]):
    """Milliseconds."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"ms"})


class Seconds(Real[SecondsUnit]):
    """Seconds."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"s"})


class Semitones(Real[SemitonesUnit]):
    """Semitones. The catalog spells this two ways, and both mean this."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Semitones", "st"})


class Cents(Real[CentsUnit]):
    """Cents. The catalog spells this two ways, and both mean this."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Cents", "cents"})


class Bpm(Real[BpmUnit]):
    """Beats per minute. One parameter: the preset's TEMPO."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"BPM"})


#: Which type a catalog ``units`` string maps to, built FROM the classes rather
#: than written out beside them, so the two cannot drift apart.
#:
#: A unit with no type - ``x``, ``bits``, ``dB/oct``, one or two parameters each
#: - is absent, and a read of one of those hands back a plain :class:`Real`.
#: Two parameters does not earn a public name, and `Real` is not a worse answer
#: for them, only a less specific one.
BY_CATALOG_UNIT = {
    unit: cls
    for cls in (Db, Percent, Hertz, Milliseconds, Seconds, Semitones, Cents, Bpm)
    for unit in cls.CATALOG_UNITS
}


def of_unit(units: str, value: float) -> Real:
    """``value`` as the type matching the catalog's ``units`` string.

    Falls back to :class:`Real` for a unit with no type of its own, and for a
    parameter with no unit at all - which is 1,780 of the 3,270 that take a value.
    """
    return BY_CATALOG_UNIT.get(units, Real)(value)


__all__ = [
    "Param", "U",
    "DbUnit", "PercentUnit", "HertzUnit", "MillisecondsUnit", "SecondsUnit",
    "SemitonesUnit", "CentsUnit", "BpmUnit", "NoUnit",
    "Value", "Encoded", "Real",
    "Db", "Percent", "Hertz", "Milliseconds", "Seconds", "Semitones", "Cents",
    "Bpm",
    "BY_CATALOG_UNIT", "of_unit",
]

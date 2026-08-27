"""A parameter value that knows which SCALE it is on.

Every parameter on the unit is stored as a float from 0.0 to 1.0, and that
number means something different for each one: 0.71 is -3.1 dB on a lane VOLUME
and 217 Hz on a Low-High Cut's HPF FREQ. So a bare number cannot say what it
means, and for a long time this library asked the caller to say it in the
keyword instead - ``value=`` for the device's scale, ``real=`` for the
parameter's own. Two arguments for one thing, mutually exclusive by convention,
and a variable holding ``-3.1`` still carried no clue.

These types put it in the value::

    qc.set_param(LaneOutput(0), "VOLUME", Db(-3.1))
    qc.set_param(block, "GAIN", Real(5.0))
    qc.set_param(block, 21, Encoded(0.5))

**The two scales, and why both exist.** :class:`Encoded` is the DEVICE's scale:
always 0..1, the same for every parameter, and it needs no catalog.
:class:`Real` is the PARAMETER's scale, whatever the catalog says that is. The
same number means different things through each, and on the lane VOLUME the
difference is total:

===============  ==========  =============================
you write        wire value  what the unit does
===============  ==========  =============================
``Real(0.0)``    0.76923     0 dB - unity, no attenuation
``Encoded(0.0)`` 0.0         the Off detent - silence
===============  ==========  =============================

That pair is why the type is mandatory rather than a convenience. A bare ``0.0``
would be a coin flip between unity and silence.

They are not interchangeable in range either: a drive's ``GAIN`` runs 0..10, so
``Real(5.0)`` is its midpoint while ``Encoded(5.0)`` is refused, because the
wire only carries 0..1.

**The unit types are a claim you can check.** :class:`Db`, :class:`Hertz` and
the rest subclass :class:`Real` and add an assertion about the unit, so handing
``Db`` to a parameter the catalog calls ``Hz`` is a ``TypeError`` rather than a
silently wrong write. A caller who does not want the check writes ``Real``; the
library's own examples always write the unit.

Note the catalog spells two units twice - ``Cents``/``cents`` and
``Semitones``/``st``. A type collapses that; a string comparison would not.

**Where the two scales coincide**, and it is worth knowing so it is not read as
a rule: 279 parameters are unitless with a range of exactly 0..1, and on those
``Real(0.5)`` and ``Encoded(0.5)`` write the same wire value. The distinction
still holds - one is half the knob's travel, the other is the encoded value 0.5
- it simply lands in the same place.

See ADR-0016, and ``docs/superpowers/specs/2026-08-27-typed-parameter-values-design.md``
for why each name was chosen and what was rejected.
"""


class Value(float):
    """A parameter value that knows which scale it is on.

    A ``float`` subclass, so ``float(Db(-3.1))`` works and arithmetic works.
    What distinguishes a typed value from a bare number is the CLASS, which is
    why :meth:`~pyquadcortex.protocol.QuadCortex.set_param` tests
    ``isinstance(v, Value)`` rather than testing for a number.
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


class Real(Value):
    """The value on the PARAMETER's own scale, whatever that scale is.

    Use this where the parameter has no unit - a drive's ``GAIN`` runs 0..10 and
    means nothing more specific - or where you do not want the unit checked.
    2,315 of the catalog's parameters are unitless with a real range, so this is
    the general case rather than a fallback.

    Where the parameter does have a unit, the matching subclass says so and gets
    it checked.
    """

    __slots__ = ()

    #: The catalog ``units`` strings this type claims. Empty means it claims
    #: nothing, which is what makes :class:`Real` itself accepted anywhere.
    CATALOG_UNITS: frozenset = frozenset()

    def check_unit(self, spec) -> None:
        """Raise ``TypeError`` if the catalog disagrees about the unit."""
        if not self.CATALOG_UNITS or spec.units in self.CATALOG_UNITS:
            return
        claimed = sorted(self.CATALOG_UNITS)[0]
        raise TypeError(
            f"{self!r} says this value is in {claimed}, and the catalog says "
            f"{spec.name!r} is in {spec.units or 'no unit'}. Use the matching "
            f"type, or Real({float(self)!r}) to make no claim about the unit."
        )


class Db(Real):
    """Decibels. 499 parameters, the largest unit population in the catalog."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"dB"})


class Percent(Real):
    """Percent."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"%"})


class Hertz(Real):
    """Hertz."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Hz"})


class Milliseconds(Real):
    """Milliseconds."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"ms"})


class Seconds(Real):
    """Seconds."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"s"})


class Semitones(Real):
    """Semitones. The catalog spells this two ways, and both mean this."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Semitones", "st"})


class Cents(Real):
    """Cents. The catalog spells this two ways, and both mean this."""

    __slots__ = ()
    CATALOG_UNITS = frozenset({"Cents", "cents"})


class Bpm(Real):
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
    parameter with no unit at all - which is 2,315 of them.
    """
    return BY_CATALOG_UNIT.get(units, Real)(value)


__all__ = [
    "Value", "Encoded", "Real",
    "Db", "Percent", "Hertz", "Milliseconds", "Seconds", "Semitones", "Cents",
    "Bpm",
    "BY_CATALOG_UNIT", "of_unit",
]

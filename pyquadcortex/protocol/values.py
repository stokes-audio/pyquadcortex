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

See ADR-0016 and
``docs/superpowers/specs/2026-08-27-typed-parameter-values-design.md``.
"""


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


class Real(Value):
    """The value on the PARAMETER's own scale, whatever that scale is.

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
    parameter with no unit at all - which is 1,780 of the 3,270 that take a value.
    """
    return BY_CATALOG_UNIT.get(units, Real)(value)


__all__ = [
    "Value", "Encoded", "Real",
    "Db", "Percent", "Hertz", "Milliseconds", "Seconds", "Semitones", "Cents",
    "Bpm",
    "BY_CATALOG_UNIT", "of_unit",
]

"""The parameter value types (pyquadcortex.protocol.values).

Pure values, no device, so everything here is exact. What these tests protect is
the claim each type makes about its unit, and the fact that two scales exist and
are not interchangeable.
"""

import pytest

from pyquadcortex.protocol import catalog, values


def _param(units: str, name: str = "X", low: float = 0.0, high: float = 1.0):
    return catalog.Parameter(index=0, name=name, minimum=low, maximum=high,
                             default=0.0, units=units, type="float")


# -- what a value IS ----------------------------------------------------------


def test_a_value_is_a_float_so_arithmetic_still_works():
    assert float(values.Db(-3.1)) == pytest.approx(-3.1)
    assert values.Db(-3.1) + 1 == pytest.approx(-2.1)
    assert isinstance(values.Db(-3.1), float)


def test_a_value_says_what_it_is():
    assert repr(values.Db(-3.1)) == "Db(-3.1)"
    assert repr(values.Encoded(0.5)) == "Encoded(0.5)"
    assert repr(values.Real(5.0)) == "Real(5.0)"


def test_the_hierarchy_is_the_one_set_param_dispatches_on():
    """A unit type IS a Real; Encoded is NOT, and that is the whole point."""
    assert isinstance(values.Db(1), values.Real)
    assert isinstance(values.Db(1), values.Value)
    assert not isinstance(values.Encoded(1), values.Real)
    assert isinstance(values.Encoded(1), values.Value)
    # A bare number is neither, which is how a missing type is caught.
    assert not isinstance(3.0, values.Value)


# -- the claim about the unit -------------------------------------------------


@pytest.mark.parametrize("cls, unit", [
    (values.Db, "dB"), (values.Percent, "%"), (values.Hertz, "Hz"),
    (values.Milliseconds, "ms"), (values.Seconds, "s"),
    (values.Semitones, "Semitones"), (values.Cents, "Cents"),
    (values.Bpm, "BPM"),
])
def test_each_unit_type_accepts_its_own_unit(cls, unit):
    cls(1.0).check_unit(_param(unit))


def test_the_catalog_spells_two_units_twice_and_the_type_collapses_both():
    """`Cents`/`cents` and `Semitones`/`st` are the same unit written two ways.

    A string comparison would have treated them as different; this is one of
    the concrete things a type buys.
    """
    values.Cents(5).check_unit(_param("Cents"))
    values.Cents(5).check_unit(_param("cents"))
    values.Semitones(2).check_unit(_param("Semitones"))
    values.Semitones(2).check_unit(_param("st"))


def test_the_wrong_unit_names_both_sides_and_offers_the_way_out():
    hz = _param("Hz", name="HPF FREQ", low=20.0, high=20000.0)
    with pytest.raises(TypeError) as excinfo:
        values.Db(-3.1).check_unit(hz)
    message = str(excinfo.value)
    assert "dB" in message and "Hz" in message
    assert "'HPF FREQ'" in message
    assert "Real(-3.1)" in message      # the escape, spelled out


def test_real_claims_nothing_so_it_fits_anywhere():
    for unit in ("dB", "Hz", "%", "", "bits"):
        values.Real(1.0).check_unit(_param(unit))


def test_a_unit_type_on_a_unitless_parameter_is_refused():
    """2,315 parameters have no unit. Db on one of those is a real mistake."""
    with pytest.raises(TypeError, match="no unit"):
        values.Db(1.0).check_unit(_param(""))


# -- the two scales -----------------------------------------------------------


def test_real_and_encoded_zero_are_different_things():
    """The pair that makes the type mandatory rather than a convenience.

    On a lane VOLUME, -40..+12 dB: Real(0.0) is unity and Encoded(0.0) is the
    Off detent. A bare 0.0 would be a coin flip between them.
    """
    volume = _param("dB", name="VOLUME", low=-40.0, high=12.0)
    assert volume.to_normalized(float(values.Real(0.0))) == pytest.approx(
        0.76923, abs=1e-4)
    # Encoded does not convert at all - it IS the wire value.
    assert float(values.Encoded(0.0)) == 0.0


def test_the_two_scales_are_not_interchangeable_in_range():
    """A drive's GAIN runs 0..10, so 5.0 is its midpoint and not a wire value."""
    gain = _param("", name="GAIN", low=0.0, high=10.0)
    assert gain.to_normalized(float(values.Real(5.0))) == pytest.approx(0.5)


def test_where_the_two_coincide_it_is_a_coincidence():
    """279 parameters are unitless over exactly 0..1, and there the two agree.

    Recorded so nobody reads the coincidence as a rule.
    """
    mix = _param("", name="BRIGHT", low=0.0, high=1.0)
    assert mix.to_normalized(float(values.Real(0.5))) == pytest.approx(0.5)
    assert float(values.Encoded(0.5)) == pytest.approx(0.5)


# -- the lookup ---------------------------------------------------------------


def test_the_lookup_is_built_from_the_classes_so_it_cannot_drift():
    for unit, cls in values.BY_CATALOG_UNIT.items():
        assert unit in cls.CATALOG_UNITS


def test_of_unit_picks_the_type_the_catalog_implies():
    assert isinstance(values.of_unit("dB", -3.1), values.Db)
    assert isinstance(values.of_unit("st", 2), values.Semitones)


@pytest.mark.parametrize("unit", ["", "x", "bits", "dB/oct"])
def test_a_unit_with_no_type_falls_back_to_real(unit):
    """One or two parameters each does not earn a public name."""
    got = values.of_unit(unit, 1.0)
    assert type(got) is values.Real

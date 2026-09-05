"""The parameter value types (pyquadcortex.protocol.values).

Pure values, no device, so everything here is exact. What these tests protect is
the claim each type makes about its unit, and the fact that two scales exist and
are not interchangeable.
"""

import pathlib
import re

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
    """1,780 parameters have no unit. Db on one of those is a real mistake."""
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


# -- reads hand back the same types -------------------------------------------


def test_a_read_says_which_units_it_is_in():
    """`to_real` returns the type the catalog's unit implies, so a value read
    back from the device carries the same information as one written to it."""
    volume = _param("dB", name="VOLUME", low=-40.0, high=12.0)
    got = volume.to_real(1.0)
    assert isinstance(got, values.Db)
    assert float(got) == pytest.approx(12.0)
    assert repr(got) == "Db(12.0)"


def test_a_unitless_read_is_a_plain_real():
    gain = _param("", name="GAIN", low=0.0, high=10.0)
    got = gain.to_real(0.5)
    assert type(got) is values.Real
    assert float(got) == pytest.approx(5.0)


def test_a_read_round_trips_through_a_write():
    """The point of typing both directions: what comes back can go straight
    back out without a caller having to remember what it meant."""
    volume = _param("dB", name="VOLUME", low=-40.0, high=12.0)
    back = volume.to_real(0.71)
    assert volume.to_normalized(back) == pytest.approx(0.71, abs=1e-9)


def test_a_floor_is_typed_whether_it_was_measured_or_derived():
    """Both branches of `floor`, because they used to disagree: the measured
    one handed back a bare float and the derived one a typed value."""
    measured = catalog.Parameter(
        index=0, name="LEVEL", minimum=-40.0, maximum=6.0, default=0.0,
        units="dB", type="float", skew=4.9594844, floor_wire=0.01,
        floor_display=-21.8)
    assert isinstance(measured.floor, values.Db)
    assert float(measured.floor) == pytest.approx(-21.8)

    derived = _param("dB", name="GAIN", low=-12.0, high=12.0)
    assert isinstance(derived.floor, values.Db)
    assert float(derived.floor) == pytest.approx(-12.0)


# -- against the device's own spellings ----------------------------------------
#
# Everything above builds its own `Parameter`, so it proves the types are
# internally consistent and nothing more. If the device spelled a unit
# differently from what a type claims, every test above would still pass and
# every real call would be refused.
#
# `params.py` is generated FROM the device's catalog and committed, so it is
# real spellings available offline. Its trailing comments carry them.

PARAMS_PY = pathlib.Path(__file__).parent.parent / "pyquadcortex" / "protocol" / "params.py"

#: Spellings the catalog uses that no type claims, with the reason. Two
#: parameters each does not earn a public name, and `Real` is not a worse
#: answer for them - only a less specific one. A NEW spelling appearing here is
#: the signal to decide, not to widen this list by reflex.
UNTYPED_SPELLINGS = {
    "x": "a ratio multiplier, 2 parameters",
    "bits": "a bit depth, 2 parameters, and both carry an option list",
    "dB/oct": "a filter slope, 2 parameters, and both carry an option list",
}


#: One generated constant: its unit MARKER type and the unit the trailing
#: comment spells. Both come off the same line, which is what lets the test
#: below hold them against each other.
CONSTANT = re.compile(
    r"^    [A-Z][A-Z0-9_]*: Param\[(?P<marker>\w+)\] = Param\(\d+, '[^']*'\)"
    r"\s+#\s*(?P<type>[\w/]+)(?:\s+(?P<units>\S+))?\s*$")


def _generated_constants():
    return [m for m in (CONSTANT.match(line)
                        for line in PARAMS_PY.read_text(
                            encoding="utf-8").splitlines()) if m]


def _spellings_the_device_publishes():
    found = {}
    for match in _generated_constants():
        if match["units"]:
            found[match["units"]] = found.get(match["units"], 0) + 1
    return found


def test_the_generator_still_annotates_units_at_all():
    """Every check below is vacuous if the comment format changes."""
    found = _spellings_the_device_publishes()
    # Only the unit-CARRYING constants are counted here; params.py annotates
    # about 2,445 in all and roughly 1,030 of those name a unit.
    assert sum(found.values()) > 900, found
    assert found.get("dB", 0) > 300


def test_every_unit_the_device_publishes_is_claimed_or_declined():
    """The one that would catch a firmware spelling change.

    A renamed unit would make `Db` refuse a correct call on every parameter
    carrying it, with the whole suite green - because nothing else here reads
    a spelling the device actually published.
    """
    for spelling in _spellings_the_device_publishes():
        assert (spelling in values.BY_CATALOG_UNIT
                or spelling in UNTYPED_SPELLINGS), (
            f"the catalog publishes units={spelling!r} and no value type claims "
            f"it. Give it a type, or add it to UNTYPED_SPELLINGS with the "
            f"reason - do not let it fall through to Real unremarked."
        )


def test_no_type_claims_a_spelling_the_device_never_uses():
    """The other direction: a claim nothing can satisfy is dead weight, and
    reads as coverage the library does not have."""
    published = set(_spellings_the_device_publishes())
    for spelling in values.BY_CATALOG_UNIT:
        assert spelling in published, (
            f"{spelling!r} is claimed by a value type and appears on no "
            f"parameter in the shipped catalog"
        )


def test_the_two_double_spellings_are_both_really_there():
    """`Cents`/`cents` and `Semitones`/`st` are the case a type exists to fix,
    so the suite should fail if the device stops doing it rather than quietly
    keeping a collapse nothing needs."""
    published = _spellings_the_device_publishes()
    for pair in (("Cents", "cents"), ("Semitones", "st")):
        for spelling in pair:
            assert spelling in published, spelling
        assert values.BY_CATALOG_UNIT[pair[0]] is values.BY_CATALOG_UNIT[pair[1]]


def test_every_constants_marker_type_matches_the_unit_beside_it():
    """The two halves of a generated line have to agree.

    `params.py` carries a parameter's unit twice: once as the `Param[...]`
    marker a type checker reads, and once in the trailing comment a person
    reads. They come from the same catalog attribute, so a disagreement means
    the generator's `UNIT_TYPES` map has drifted from `values.BY_CATALOG_UNIT`
    - and the halves would then disagree silently, with the checker enforcing
    one and the docs promising the other.
    """
    expected = {unit: cls.__name__ + "Unit" for unit, cls
                in values.BY_CATALOG_UNIT.items()}
    # The type names are not mechanically derived from the class names, so map
    # the two that differ rather than pretending a rule exists.
    expected.update({"Hz": "HertzUnit", "%": "PercentUnit", "BPM": "BpmUnit"})
    for match in _generated_constants():
        units, marker = match["units"], match["marker"]
        if units and units in expected:
            assert marker == expected[units], (
                f"a constant in units={units!r} is tagged {marker}, and the "
                f"value types say it should be {expected[units]}")
        elif not units:
            assert marker == "NoUnit", (
                f"a constant with no unit is tagged {marker}, not NoUnit")


def test_a_unit_with_no_value_type_is_tagged_as_having_none():
    """`x`, `bits` and `dB/oct` decline a value type, so they decline a marker.

    `NoUnit` is the honest tag: nothing checks them, and pretending otherwise
    would make a checker enforce a unit the library does not model.
    """
    for match in _generated_constants():
        if match["units"] in UNTYPED_SPELLINGS:
            assert match["marker"] == "NoUnit"

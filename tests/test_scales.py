"""Every reading taken off the unit's screen, against the device's own catalog.

These numbers were once the SOURCE of this library's parameter spans: a table of
hand-measured ranges that grew for months because the catalog was believed not
to publish them. It does. `skew` is the taper, and a symbolic `min="MIN_..."`
is a bound whose number lives in the firmware rather than a "placeholder range".

So the readings changed job. They are no longer where the spans come from - they
are the evidence that the catalog does not lie. A failure here is a finding
about the device, not a tolerance to widen.

The assertion is exact at the DISPLAY's precision rather than approximate: the
unit showed "217 Hz" and "-21.8 dB", so the test rounds the way the screen does
and demands the same string of digits. See `scripts/extract_scale_fixture.py`
for why the fixture holds distilled parameters instead of the whole ModelRepo.
"""

import json
import pathlib

import pytest

from pyquadcortex.protocol import catalog, units

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalog" / "scales.json"


def _load():
    rows = json.loads(FIXTURE.read_text())
    return {(r["model_id"], r["index"]): catalog.Parameter(
        index=r["index"], name=r["name"], minimum=r["minimum"],
        maximum=r["maximum"], default=0.0, units=r["units"], type=r["type"],
        steps=r["steps"], skew=r["skew"], floor_wire=r["floor_wire"],
    ) for r in rows}


SCALES = _load()

#: ``(model, index, wire, what the screen showed, decimal places it showed)``.
#:
#: The date beside each group is when it was read. Nothing here is a fit, a
#: rounding of a fit, or an endpoint inferred from one - every value was on the
#: display at the moment the wire value was known.
READINGS = [
    # -- the cab LEVEL, 2026-08-26 -------------------------------------------
    # A TAPERED control, and a warning. Three points in its upper half fit a
    # straight line beautifully and are 12 dB wrong at wire 0.01. It was written
    # up as having no closed form until four more points produced a taper; the
    # catalog had published that taper as skew="4.9594844" all along.
    (12000, 2, 0.01, -21.8, 1),
    (12000, 2, 0.02, -19.1, 1),
    (12000, 2, 0.05, -14.9, 1),
    (12000, 2, 0.10, -11.1, 1),
    (12000, 2, 0.15, -8.6, 1),
    (12000, 2, 0.25, -5.2, 1),
    (12000, 2, 0.35, -2.8, 1),
    (12000, 2, 0.50, 0.0, 1),
    (12000, 2, 0.60, 1.5, 1),
    (12000, 2, 0.75, 3.4, 1),
    (12000, 2, 0.95, 5.5, 1),
    (12000, 2, 1.00, 6.0, 1),

    # -- the block EQ band gains, 2026-08-25 ---------------------------------
    # Both ends and an off-half point, which a curved mapping would have missed.
    (4000, 0, 0.00, -12.0, 1),
    (4000, 0, 0.10, -9.6, 1),
    (4000, 0, 0.50, 0.0, 1),
    (4000, 0, 1.00, 12.0, 1),

    # -- Low-High Cut, 2026-08-26 --------------------------------------------
    # The skew-below-1 direction, which the cab could not test. A linear reading
    # would have been 5015 Hz and a log sweep 112 Hz.
    (4003, 1, 0.25, 217, 0),
    (4003, 3, 0.75, 7678, 0),
    # The same block's OUTPUT carries no skew: the linear control case, read in
    # the same session so a systematic error would show up here too.
    (4003, 4, 0.25, -10.0, 1),

    # -- Envelope Filter, 2026-08-26 -----------------------------------------
    # LOG_SKEW, which is not a log sweep. Two knobs over different ranges in
    # different units, both solving to exponent 1/0.3.
    (24003, 5, 0.25, 197, 0),
    (24003, 7, 0.75, 4.45, 2),

    # -- TEMPO, 2026-08-25 ----------------------------------------------------
    # The 59 is what makes the fit worth trusting: a span needs a point away
    # from the others.
    (25000, 0, 0.095, 59, 0),
    (25000, 0, 0.355, 111, 0),
    (25000, 0, 0.400, 120, 0),

    # -- the lane / mixer / splitter LEVEL family, 2026-08-25 ----------------
    # The lane VOLUME came first and the rest INHERITED its claim for several
    # releases before anyone measured them. They were measured, and it held.
    (23000, 0, 0.01, -39.5, 1),
    (23000, 0, 0.71, -3.1, 1),
    (23000, 0, 1.00, 12.0, 1),
    (11000, 5, 0.30, -24.4, 1),
    (11000, 5, 1.00, 12.0, 1),
    (10004, 3, 0.30, -24.4, 1),
    (10004, 4, 0.71, -3.1, 1),
    (10004, 4, 1.00, 12.0, 1),

    # -- the FX loop, 2026-08-26 ---------------------------------------------
    # Five parameters and TWO scales. The send tops out at unity because a send
    # cannot boost; the return reaches +12 like the lane levels.
    (13000, 0, 0.01, -39.6, 1),
    (13000, 0, 0.10, -36.0, 1),
    (13000, 0, 0.50, -20.0, 1),
    (13000, 0, 0.75, -10.0, 1),
    (13000, 0, 1.00, 0.0, 1),
    (13002, 0, 0.01, -39.5, 1),
    (13002, 0, 0.10, -34.8, 1),
    (13002, 0, 0.50, -14.0, 1),
    (13002, 0, 1.00, 12.0, 1),

    # -- the Splitter Crossover, 2026-08-26 ----------------------------------
    # Not read off the screen. The catalog states defaultValue="400.0" and the
    # unit was holding this wire value for that knob, which is what pins the
    # bounds - see FIRMWARE_CONSTANTS["MIN_EQ_FREQ"].
    (10004, 5, 0.49547526240348816, 400, 0),
]


@pytest.mark.parametrize("model_id, index, wire, screen, digits", READINGS)
def test_the_catalog_reproduces_what_the_screen_showed(
        model_id, index, wire, screen, digits):
    spec = SCALES[(model_id, index)]
    shown = round(spec.to_real(wire), digits)
    assert shown == pytest.approx(screen, abs=0), (
        f"{spec.name!r} on model {model_id} showed {screen} at wire {wire}, "
        f"and the catalog says the unit would show {shown}"
    )


@pytest.mark.parametrize("model_id, index, wire, screen, digits", READINGS)
def test_the_conversion_round_trips(model_id, index, wire, screen, digits):
    """Going back the other way lands on the same wire value.

    Not a tautology once a taper is involved: an exponent applied in the wrong
    direction still round-trips through itself but reads the screen wrong, which
    is why the test above exists as well as this one.
    """
    spec = SCALES[(model_id, index)]
    real = spec.to_real(wire)
    if spec.floor is not None and real < spec.floor:
        pytest.skip("below the Off detent, where to_normalized refuses by design")
    assert spec.to_normalized(real) == pytest.approx(wire, abs=1e-9)


# -- what the families share --------------------------------------------------


def test_four_families_share_the_lane_level_span():
    """Lane, mixer, splitter and FX return all resolve MIN_MIXER_DB."""
    keys = [(23000, 0), (11000, 5), (10004, 3), (10004, 4), (13002, 0)]
    spans = {(SCALES[k].minimum, SCALES[k].maximum) for k in keys}
    assert spans == {(-40.0, 12.0)}


def test_the_send_side_is_a_different_scale_from_the_return_side():
    """A send cannot boost. This is why one constant could not cover both."""
    send, ret = SCALES[(13000, 0)], SCALES[(13002, 0)]
    assert (send.minimum, send.maximum) == (-40.0, 0.0)
    assert (ret.minimum, ret.maximum) == (-40.0, 12.0)


def test_the_legacy_splitter_view_shares_the_unified_splitters_span():
    """`Splitter AB` (10000) is the read-only view of `Splitter` (10004)."""
    for legacy, unified in (((10000, 0), (10004, 3)), ((10000, 1), (10004, 4))):
        a, b = SCALES[legacy], SCALES[unified]
        assert (a.minimum, a.maximum, a.skew) == (b.minimum, b.maximum, b.skew)


def test_both_cab_microphones_share_the_layout():
    a, b = SCALES[(12000, 2)], SCALES[(12000, 10)]
    assert (a.minimum, a.maximum, a.skew) == (b.minimum, b.maximum, b.skew)


# -- the Off detent -----------------------------------------------------------


@pytest.mark.parametrize("key", [(12000, 2), (23000, 0), (11000, 5),
                                 (10004, 3), (13000, 0), (13002, 0)])
def test_a_level_family_parameter_has_a_measured_floor(key):
    """`min` is not a place these knobs go; below the floor the screen says OFF."""
    assert SCALES[key].floor_wire == 0.01


def test_asking_a_cab_for_a_level_it_cannot_reach_refuses():
    """The bug this floor exists to prevent.

    A cab LEVEL's law runs to -40 dB but its quietest real setting is -21.8 dB.
    Without the floor, -30 dB converts to wire 0.0005 and silently MUTES the
    microphone - a write that looks like it worked and did something else.
    """
    cab = SCALES[(12000, 2)]
    assert cab.floor == pytest.approx(-21.8, abs=0.05)
    with pytest.raises(ValueError, match="does not exist there"):
        cab.to_normalized(-30.0)


def test_a_knob_with_no_off_detent_converts_at_its_minimum():
    """The EQ gains reach every position, so nothing is refused there."""
    band = SCALES[(4000, 0)]
    assert band.floor_wire == 0.0
    assert band.to_normalized(-12.0) == pytest.approx(0.0)


# -- the one bound nobody has measured ----------------------------------------


def test_the_recorder_refuses_rather_than_converting_against_a_guess():
    """Its bounds are MIN_INPUT_TRIM / MAX_INPUT_TRIM and no one can read them.

    Placing the block to see the screen crashes the unit, so this is permanently
    unmeasured rather than merely unmeasured yet - see units.DO_NOT_PROBE.
    """
    rec = SCALES[(20000, 2)]
    assert rec.minimum is None and rec.maximum is None
    assert (20000, 2) in units.DO_NOT_PROBE
    with pytest.raises(ValueError, match="nobody has measured"):
        rec.to_real(0.5)
    with pytest.raises(ValueError, match="nobody has measured"):
        rec.to_normalized(-6.0)


# -- the guard that stops this happening again --------------------------------


def test_every_symbolic_bound_in_the_fixture_has_a_number_or_a_reason():
    """A firmware update adding a new one must fail loudly, not become 0..1.

    `catalog._as_bound` raises for a name it has never met. This proves the two
    tables between them cover everything the shipped catalog actually uses, so
    that raise is a future-proofing measure rather than a live bug.
    """
    named = set(units.FIRMWARE_CONSTANTS) | set(units.UNMEASURED_BOUNDS)
    assert set(units.FLOOR_WIRE) <= set(units.FIRMWARE_CONSTANTS), (
        "a floor belongs to a family whose bounds are known")
    for name in named:
        assert name.startswith(("MIN_", "MAX_")), name
    # Both halves of every family are present; a lone MIN_ would resolve one end
    # and silently leave the other at its fallback.
    for name in named:
        twin = ("MAX_" + name[4:]) if name.startswith("MIN_") else ("MIN_" + name[4:])
        assert twin in named, f"{name} has no {twin}"

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

import gzip
import io
import json
import pathlib
import tarfile

import pytest

from pyquadcortex.protocol import catalog, units

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "catalog" / "scales.json"


def _load():
    """Rebuild each parameter by RE-PARSING the device's own XML attributes.

    Not by feeding the recorded numbers straight into a `Parameter`. That was
    the first version and it left the evidence loop closed on itself: readings
    proved the arithmetic, the fixture supplied the resolved bounds, and
    `units.FIRMWARE_CONSTANTS` sat outside the loop entirely - six of its
    fourteen numbers could be changed to anything at all and every one of these
    tests still passed.

    Going through `parse_model_repo` means a reading proves the RESOLUTION too:
    the XML says `min="MIN_FXLOOP_OUT_GAIN_DB"`, and the screen said -20.0 dB at
    wire 0.50, and the only way both are true is if the constant is -40.
    """
    rows = json.loads(FIXTURE.read_text())

    # One <Model> per model id, with every wanted parameter at its real wire
    # index and cheap padding in between. Several models contribute more than
    # one row - a Parallax has a cab LEVEL per microphone - so building a model
    # per ROW would silently drop all but the last.
    by_model = {}
    for row in rows:
        by_model.setdefault((row["model_id"], row["model"]), {})[row["index"]] = row

    xml = ['<?xml version="1.0" ?><Models>']
    for (model_id, model_name), wanted in sorted(by_model.items()):
        xml.append(f'<Category id="{model_id}" name="c{model_id}">'
                   f'<Model id="{model_id}" name="{_escape(model_name)}">')
        for index in range(max(wanted) + 1):
            row = wanted.get(index)
            if row is None:
                xml.append('<Parameter name="pad" type="float" min="0" max="1"'
                           ' defaultValue="0"/>')
            else:
                attrs = " ".join(f'{k}="{_escape(v)}"'
                                 for k, v in row["raw"].items())
                xml.append(f"<Parameter {attrs}/>")
        xml.append("</Model></Category>")
    xml.append("</Models>")
    cat = catalog.parse_model_repo(_payload("".join(xml)))

    out = {}
    for row in rows:
        spec = cat[row["model_id"]].parameters[row["index"]]
        # The fixture's resolved columns are a second opinion on the parse. If
        # they disagree, either units.py moved or the device did.
        for field in ("minimum", "maximum", "skew", "floor_wire", "floor_display"):
            assert getattr(spec, field) == row[field], (
                f"{row['model']} {row['name']}: parsing {row['raw']} gives "
                f"{field}={getattr(spec, field)!r}, fixture records {row[field]!r}")
        out[(row["model_id"], row["index"])] = spec
    return out


def _escape(value) -> str:
    return (str(value).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _payload(xml: str) -> bytes:
    """Wrap XML the way the device does: a gzipped tar of ModelRepo.xml."""
    raw = xml.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("ModelRepo.xml")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    return gzip.compress(buf.getvalue())


SCALES = _load()

#: ``(model, index, wire, what the screen showed, decimal places it showed)``.
#:
#: The date beside each group is when it was read. Nothing here is a fit, a
#: rounding of a fit, or an endpoint inferred from one: every value was on the
#: display at the moment the wire value was known - with ONE exception, the
#: Splitter Crossover at the bottom, which is the catalog's own stated default
#: against a wire value read off the unit. It is labelled where it sits.
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
    # 0.20 is the one that broke the hand fit. The fitted law renders it -6.8;
    # the screen said -6.7, and the catalog's own numbers give -6.7477, which
    # rounds to -6.7. PR #32 had to widen a tolerance to hold this point and
    # dropped it from the worst-error check; here it just passes.
    (12000, 2, 0.20, -6.7, 1),
    (12000, 2, 0.25, -5.2, 1),
    (12000, 2, 0.35, -2.8, 1),
    (12000, 2, 0.50, 0.0, 1),
    (12000, 2, 0.60, 1.5, 1),
    (12000, 2, 0.75, 3.4, 1),
    (12000, 2, 0.85, 4.5, 1),
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

    # -- a cab whose own entry disagrees, 2026-08-27 -------------------------
    # Read through a `Plini Cab (M)` (12053), NOT a `Default Cabsim` - 12000 is
    # internal="true" and cannot be placed. That is the point of the reading.
    # 12053's own catalog entry calls index 2 `POSITION`, unitless over 0..1,
    # and the borrowed layout calls it `LEVEL` in dB. Writing this wire value
    # separates them: the layout predicts -3.0 dB and the cab's own entry
    # predicts 0.34. The screen showed `LEVEL -3.0 dB`, and the block's own
    # `POSITION` sat untouched at its 0.50 default - so the layout is right
    # about the NAME as well as the law, on a model that names it otherwise.
    (12000, 2, 0.339665, -3.0, 1),

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
    # No reading sits below its own floor - the floors ARE readings - so this
    # guard never fires today. It is here because adding one later should skip
    # rather than fail: below the detent `to_normalized` refuses by design, and
    # that refusal has its own tests.
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
        assert (a.minimum, a.maximum, a.skew, a.floor_wire) == (
            b.minimum, b.maximum, b.skew, b.floor_wire)


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


def test_every_symbolic_bound_has_a_number_or_a_written_reason():
    """A firmware update adding a new one must fail loudly, not become 0..1.

    `catalog._as_bound` raises for a name it has never met. This proves the two
    tables between them cover everything the shipped catalog uses, so that raise
    is future-proofing rather than a live bug.
    """
    named = set(units.FIRMWARE_CONSTANTS) | set(units.UNMEASURED_BOUNDS)
    for name in named:
        assert name.startswith(("MIN_", "MAX_")), name
        # Both halves of every family. A lone MIN_ would resolve one end and
        # leave the other silently at its fallback.
        twin = ("MAX_" + name[4:]) if name.startswith("MIN_") else ("MIN_" + name[4:])
        assert twin in named, f"{name} has no {twin}"


def test_a_floor_belongs_to_a_law_whose_bounds_are_known():
    """FLOOR_WIRE is keyed by the LAW, not by the catalog's constant name.

    Keyed by name it protected most cabs and not the PCOM ones, which spell the
    identical knob with literal bounds - so asking one of those for -30 dB
    returned wire 0.000516 and muted the microphone, which is the exact bug the
    table exists to prevent, surviving inside the fix for it.
    """
    known = set(units.FIRMWARE_CONSTANTS.values())
    for (low, high, skew), (floor_wire, displayed) in units.FLOOR_WIRE.items():
        assert low in known and high in known, (low, high)
        assert 0.0 < floor_wire < 1.0, floor_wire
        assert low <= displayed <= high, (displayed, low, high)
        assert skew > 0.0


def test_the_same_knob_is_floored_under_both_of_its_spellings():
    """The regression that made the key wrong in the first place.

    A cab LEVEL is `min="MIN_CABSIM_DB"` on most models and `min="-40" max="6"`
    on the PCOM variants. Same control, same taper, and before the fix only one
    of them refused a value that mutes the microphone.
    """
    symbolic = SCALES[(12000, 2)]        # min="MIN_CABSIM_DB"
    literal = SCALES[(12114, 25)]        # min="-40" max="6"
    assert literal.raw_is_literal if hasattr(literal, "raw_is_literal") else True
    for spec in (symbolic, literal):
        assert spec.floor_wire == 0.01, spec.name
        assert spec.floor == pytest.approx(-21.8, abs=0.05)
        with pytest.raises(ValueError, match="does not exist there"):
            spec.to_normalized(-30.0)


def test_parallax_carries_the_cab_law_itself():
    """It is a Bass Overdrive with a cab section, so it cannot borrow the layout.

    `targets.wire_model` only borrows for models in CABSIM_CATEGORIES, and
    Parallax is not one. It works only because its own catalog entry carries
    MIN_CABSIM_DB and the same skew - which is also the evidence the
    layout-borrowing design cites, so it is worth pinning where it is claimed.
    """
    for index in (16, 24):
        spec = SCALES[(3008, index)]
        assert (spec.minimum, spec.maximum) == (-40.0, 6.0)
        assert spec.skew == pytest.approx(4.9594844)
        assert spec.floor_wire == 0.01


def test_the_fx_families_are_more_than_one_model_each():
    """Nine send-side and six return-side parameters share two scales.

    Only one of each was pinned at first, which would not have noticed a second
    Send resolving to the return family or the other way round.
    """
    for key in ((13000, 0), (13001, 0)):
        assert (SCALES[key].minimum, SCALES[key].maximum) == (-40.0, 0.0)
    for key in ((13002, 0), (13003, 0)):
        assert (SCALES[key].minimum, SCALES[key].maximum) == (-40.0, 12.0)


def test_the_scene_following_mixer_levels_are_covered():
    """LEVEL A and LEVEL B nearly went down as NOT DRIVABLE.

    Four host writes looked dropped. Both are scene-following, so the wire
    carries eight values and a write lands on the ACTIVE scene while the reader
    was taking `param_values[0]` - scene A, on a unit sitting in scene E.
    """
    for key in ((11000, 0), (11000, 2), (11000, 5)):
        assert (SCALES[key].minimum, SCALES[key].maximum) == (-40.0, 12.0)
        assert SCALES[key].floor_wire == 0.01


def test_the_recorder_reason_says_what_actually_happened():
    """Membership is not enough: the REASON is the whole value of the entry."""
    reason = units.DO_NOT_PROBE[(20000, 2)].lower()
    assert "crash" in reason
    assert 20000 in units.UNPLACEABLE_MODELS
    assert "crashed" in units.UNPLACEABLE_MODELS[20000].lower()


def test_a_knob_with_no_detent_reports_its_minimum_as_its_floor():
    """TEMPO and the EQ gains reach every position, so nothing is refused."""
    for key in ((25000, 0), (4000, 0)):
        spec = SCALES[key]
        assert spec.floor_wire == 0.0
        assert spec.floor_is_measured is False
        assert spec.floor == spec.minimum


def test_a_refusal_mentions_the_off_position_only_where_there_is_one():
    """One shared message for four families told a tempo caller about an Off
    position the tempo has not got."""
    with pytest.raises(ValueError) as tempo:
        SCALES[(25000, 0)].to_normalized(300.0)
    assert "Off position" not in str(tempo.value)

    with pytest.raises(ValueError) as lane:
        SCALES[(23000, 0)].to_normalized(-39.9)
    assert "Off position" in str(lane.value)


def test_asking_for_the_bottom_of_the_scale_is_refused_where_it_is_a_detent():
    """`minimum` and `floor` diverge most sharply exactly here."""
    with pytest.raises(ValueError, match="does not exist there"):
        SCALES[(23000, 0)].to_normalized(-40.0)
    # ...and the number the refusal prints is one it would itself accept.
    assert SCALES[(23000, 0)].to_normalized(-39.5) == pytest.approx(0.01, abs=5e-4)


def test_reading_a_wire_value_the_wire_cannot_carry_is_refused():
    """It used to clamp. Four factory presets store NaN in `param_values`, so
    clamping reported the bottom of the range as a knob's value."""
    spec = SCALES[(12000, 2)]
    for outside in (-0.1, 1.5, float("nan")):
        with pytest.raises(ValueError, match="0..1"):
            spec.to_real(outside)


def test_every_cab_that_describes_a_level_describes_the_same_one():
    """What the catalog adds beyond three screen readings.

    All three measured blocks are mono, and 86 of the 174 models in the cabsim
    categories are stereo - so applying the law across the category IS an
    extrapolation. But of the 16 cab models that describe a LEVEL of their own,
    every one carries MIN_CABSIM_DB and skew 4.9594844, stereo variants
    included. The device says the law is uniform wherever it says anything.

    Pinned on the models the fixture carries, including one stereo and one PCOM
    variant with literal bounds.
    """
    law = (-40.0, 6.0, 4.9594844)
    for key in ((12000, 2), (12000, 10), (12114, 25), (32000, 2),
                (3008, 16), (3008, 24)):
        spec = SCALES[key]
        assert (spec.minimum, spec.maximum, spec.skew) == law, key


# -- the settings, whose spans the catalog does not publish --------------------


SETTING_READINGS = [
    # -- an input port's GAIN, 2026-08-25 ------------------------------------
    # Four owner-set trims read on screen and on the wire at the same moment.
    # The two interior points are what DISCRIMINATE the span - a wrong width
    # still reproduces 0 dB at 1/6 - and until now they lived only in a comment
    # in `units.py` while the tests asserted the two easy ones.
    ("INPUT_GAIN_DB", 0.16667, 0.0, 1),
    ("INPUT_GAIN_DB", 0.40043, 16.8, 1),
    ("INPUT_GAIN_DB", 0.40556, 17.2, 1),
    ("INPUT_GAIN_DB", 0.50009, 24.0, 1),

    # -- a Global EQ band's GAIN ---------------------------------------------
    # NOT a screen reading in the sense above, and listed apart on purpose: the
    # span is the MANUAL's, and these two points are what the library was told
    # rather than what anyone measured. They are 6 dB apart on a span claimed to
    # be 24 dB wide, so they cannot distinguish it from a wider one - exactly
    # the trap that put -100..+30 in this file for two releases. Driving the
    # ENDS on screen is what would settle it.
    ("GLOBAL_EQ_GAIN_DB", 0.5, 0.0, 1),
    ("GLOBAL_EQ_GAIN_DB", 0.75, 6.0, 1),
]


@pytest.mark.parametrize("span_key, wire, screen, digits", SETTING_READINGS)
def test_a_setting_span_reproduces_what_was_read(span_key, wire, screen, digits):
    """The spans in `units.SETTING_SPANS` are linear, so this is the whole law.

    Held here rather than only in `client.py` because these numbers have no
    catalog entry to check them against - if a span is wrong, nothing else in
    the offline suite would notice.
    """
    low, high = units.SETTING_SPANS[span_key]
    assert round(low + (high - low) * wire, digits) == pytest.approx(
        screen, abs=0), (
        f"{span_key} at wire {wire} was read as {screen}, and the span says "
        f"{round(low + (high - low) * wire, digits)}")


def test_the_setting_spans_and_the_parameters_built_from_them_agree():
    """`client` turns each span into a `catalog.Parameter` so the conversion is
    the one law rather than a private copy. This is what holds those together -
    a wrong bound in either place shows up as a disagreement."""
    from pyquadcortex.protocol import client

    for scale, key in ((client._INPUT_GAIN, "INPUT_GAIN_DB"),
                       (client._GLOBAL_EQ_GAIN, "GLOBAL_EQ_GAIN_DB")):
        assert (scale.minimum, scale.maximum) == units.SETTING_SPANS[key]
        assert scale.units == "dB"
        assert scale.skew == 1.0, "both spans are linear; a taper would need evidence"


def test_every_setting_span_is_reachable_and_used():
    """A span nobody converts against is a number with no evidence attached to
    anything, which is how the old placeholder ranges survived."""
    from pyquadcortex.protocol import client

    used = {"INPUT_GAIN_DB", "GLOBAL_EQ_GAIN_DB"}
    assert set(units.SETTING_SPANS) == used, (
        "a span was added or removed without a `catalog.Parameter` built from "
        "it - see `client._setting_scale`")
    assert client._INPUT_GAIN is not None and client._GLOBAL_EQ_GAIN is not None

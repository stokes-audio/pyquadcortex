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
import math
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

    # -- the pan family, 2026-09-11 ------------------------------------------
    # A labelled-end control draws 50 on one side, through the middle label, to
    # 50 on the other, and the catalog declares that span four different ways.
    # Recorded against the LAYOUT the way the cab LEVEL above is: the mono
    # readings were taken through a `Plini Cab (M)` (12053), which inherits
    # 12100's PAN, and 12000/12100 declare that parameter identically. The
    # stereo readings came through a `412 CA Stand OS S V30 90s (ST)` (32001),
    # which inherits 32000's BALANCE. The Minivoicer was driven directly.
    #
    # Wire 0.5 is deliberately absent: the screen shows the `mid_string` letter
    # "C" there rather than a number, so asserting 0.0 would be asserting a
    # number the unit did not print. See the test below it.
    (12000, 3, 0.0, -50.0, 0),          # screen: 50 L
    (12000, 3, 1.0, 50.0, 0),           # screen: 50 R
    (32000, 3, 0.0, -50.0, 0),          # screen: 50 L, declared -1..1
    (32000, 3, 0.75, 25.0, 0),          # screen: 25 R
    (32000, 3, 1.0, 50.0, 0),           # screen: 50 R
    (18007, 8, 0.6, 10.0, 0),           # screen: 10 R, at its untouched default
    (18007, 12, 0.0, -50.0, 0),         # screen: 50 L, declared 0..1

    # -- the amp OUTPUT family, 2026-09-11 -----------------------------------
    # 125 knobs on one law - every guitar and bass amp's OUTPUT, plus a drive
    # and three utilities - and none of them had ever been driven. Read off a
    # `Brit 2203` (1001) on the factory preset of the same name. The three wire
    # values were written from the host, because the unit's own encoder cannot
    # reach below 0.01 on a knob the catalog gives no `steps`: the owner turned
    # it down to the bottom and one step up landed on 0.01 exactly, which is
    # where all three floors recorded before this sat.
    #
    # This is the first hardware confirmation of skew 3.8018, and it holds over
    # four decades of wire: the law renders these -38.558, -42.132 and -58.098.
    (1001, 6, 0.01, -38.6, 1),
    (1001, 6, 0.005, -42.1, 1),
    (1001, 6, 0.000001, -58.1, 1),

    # -- the IR loader HI PASS, 2026-09-11 -----------------------------------
    # The one Off detent of the three looked at that turned out to be real: at
    # wire 0.000001 this screen says OFF, where the amp above says -58.1. Read
    # off a `Single (M)` (29001) placed on a free row. 20 Hz is the law's own
    # minimum, so the detent hides no value - see the test below.
    (29001, 4, 0.003, 20, 0),
    (29001, 4, 0.005025126, 20, 0),

    # -- the cab LEVEL below its recorded floor, 2026-09-11 ------------------
    # Read through a `412 CA Stand OS A V30 01 (M)` (12031), which inherits
    # 12000's layout the way the 2026-08-27 reading above does. It is here
    # because it CONTRADICTS a shipped record: FLOOR_WIRE says this knob reads
    # OFF below wire 0.01, and at wire 0.000001 the screen read -37.2 dB.
    # See `test_the_cab_floor_records_a_detent_the_screen_does_not_show`.
    (12000, 2, 0.000001, -37.2, 1),

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


def test_a_labelled_end_control_shows_a_letter_at_its_middle():
    """`mid_string` is the label at wire 0.5, which nobody had pinned before.

    Measured 2026-09-11: a mono cab's `PAN` and a stereo cab's `BALANCE` both
    read "C" at wire 0.5, and the appendix in `docs/domain-model.md` had this
    attribute listed as seen-but-unexplained because the catalog never says
    WHICH middle position it labels. It is the center of the wire.

    The law still answers 0.0 there, and that is the honest number to convert
    against; the letter is what the screen prints instead.
    """
    for key in ((12000, 3), (32000, 3)):
        spec = SCALES[key]
        assert spec.mid_label == "C", spec.name
        assert (spec.min_label, spec.max_label) == ("L", "R")
        assert spec.to_real(0.5) == pytest.approx(0.0)


def test_a_pans_default_is_the_position_the_screen_showed():
    """The Minivoicer's `V1 PAN` was read at its UNTOUCHED default.

    It declares 0.6 of 0..1 and the screen showed `10 R`, so the default has to
    move onto the drawn span with everything else. This is the one reading that
    needed no write at all, which is what makes it a check on the conversion
    rather than on the write path.
    """
    assert SCALES[(18007, 8)].default == pytest.approx(10.0)
    assert SCALES[(18007, 12)].default == pytest.approx(-10.0)
    # the cab pans declare their centre differently and both land on it
    assert SCALES[(12000, 3)].default == pytest.approx(0.0)
    assert SCALES[(32000, 3)].default == pytest.approx(0.0)


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



# -- what the 2026-09-11 Off-detent session found ------------------------------
#
# Three laws were driven, covering 161 of the 189 parameters that carried a
# min_label and no measured floor. Not one of them produced a new FLOOR_WIRE
# entry, and each failed to for a different reason. These tests hold the three
# answers, because "we looked and there was nothing there" is a result that
# costs a session to rediscover.


def test_gain_reduction_is_a_meter_and_the_device_says_so():
    """The 20 knobs on the '-Inf' law are readouts, not controls.

    The owner at the unit: it sits at 0.0 with no audio and flickers while
    something is playing, and a host write of wire 0.5 moved nothing on screen -
    though the value round-tripped through the preset, which is the
    accept-and-ignore trap and proves storage rather than control.

    The catalog had said so all along. ``type="grMeter"`` is its own kind, 39
    parameters across 39 models, every one of them named GAIN REDUCTION - so
    this needed no hardware at all, and the checking-the-catalog-first rule got
    another instance.
    """
    spec = SCALES[(6005, 17)]
    assert spec.type == "grMeter"
    assert spec.name == "GAIN REDUCTION"
    assert spec.min_label == "-Inf"
    # A meter has no floor to measure, so it must not acquire one by sharing a
    # law with something drivable. Nothing else in the catalog carries this law.
    assert (spec.minimum, spec.maximum, spec.skew) not in units.FLOOR_WIRE


def test_the_amp_output_family_has_no_off_detent_to_find():
    """125 knobs, and the numbers run all the way down.

    At wire 0.000001 a `Brit 2203` OUTPUT reads -58.1 dB - a number, not the
    word. OFF is the single position at wire 0.0 and nothing else, so there is
    no gap between the detent and where the numbers resume, and no entry to
    write. What LOOKED like a gap was the encoder: turning the knob off the
    bottom lands on wire 0.01, 18 dB above where a host write can reach.
    """
    spec = SCALES[(1001, 6)]
    assert spec.min_label == "OFF"
    assert (spec.minimum, spec.maximum, spec.skew) not in units.FLOOR_WIRE
    assert spec.floor_is_measured is False
    # The whole declared span stays available, which is the point of recording
    # no floor: the bottom of the law is reachable to the display's precision.
    assert float(spec.to_normalized(-58.1)) < 0.01
    assert float(spec.floor) == pytest.approx(-60.0)


def test_the_ir_loader_detent_hides_nothing():
    """A real detent, and the numbers resume at the law's own minimum.

    `Single (M)` HI PASS reads OFF at wire 0.000001 and 20 Hz at 0.003, so
    unlike the amp this one has a genuine boundary. 20 Hz is ``minimum``,
    though, so no value the caller can name becomes unreachable - which is why
    there is still no FLOOR_WIRE entry. The entry would change `floor` from
    20 Hz to 20 Hz.

    The boundary itself was not bisected; the owner had to step away. It lies
    between wire 0.000001 and 0.003, and the knob's own step is 1/199 (the
    catalog gives ``steps=200``), which is where the encoder puts it.
    """
    spec = SCALES[(29001, 4)]
    assert spec.min_label == "OFF" and spec.steps == 200
    assert spec.units == "Hz" and spec.show_as_integer is True
    assert float(spec.floor) == pytest.approx(spec.minimum)
    # 1/199 is position 1 of 200, the lowest the encoder reaches, and it
    # displays the same 20 Hz that a host write of 0.003 does.
    assert float(spec.to_real(1 / 199)) == pytest.approx(20.115, abs=0.01)


def test_the_cab_floor_records_a_detent_the_screen_does_not_show():
    """The finding this session did NOT act on, held where it cannot be lost.

    FLOOR_WIRE says a cab LEVEL reads OFF below wire 0.01 and so refuses -30 dB.
    On 2026-09-11 a `412 CA Stand OS A V30 01 (M)` was written to wire 0.000001
    and the screen read -37.2 dB. Both assertions below pass today, and they
    cannot both be describing the same device correctly.

    The guard is left ALONE on purpose. The record it rests on cites muted
    AUDIO, and a screen that prints -37.2 does not establish the microphone is
    audible there; only listening does. So this pins the contradiction rather
    than resolving it, and `units.FLOOR_WIRE` names what would settle it.
    """
    spec = SCALES[(12000, 2)]
    # What the guard believes.
    assert spec.floor_wire == 0.01
    assert float(spec.floor) == pytest.approx(-21.8, abs=0.05)
    with pytest.raises(ValueError, match="does not exist there"):
        spec.to_normalized(-30.0)
    # What the screen showed, four decades of wire below the recorded floor.
    assert float(spec.to_real(0.000001)) == pytest.approx(-37.2, abs=0.05)


def test_every_family_with_a_recorded_floor_is_a_stepless_knob():
    """Why the three recorded floors are all exactly 0.01, and are all suspect.

    The catalog gives none of them a ``steps``, and a stepless knob's encoder
    moves in hundredths - so 0.01 is the first position a player can turn to,
    whether or not it is the first that shows a number. The amp above is the
    fourth stepless family and the first driven BELOW that position, and there
    the word turned out to stop at wire 0.0.

    Stated as a pattern rather than a rule: three stepless families with a
    floor of 0.01 measured by turning the knob, one stepless family with no
    floor measured by writing under it, and one 200-step family whose detent is
    real. That is not enough to key a table on, and the last attempt to key
    this table on a rule shipped a bug, so nothing here acts on it.
    """
    for key in units.FLOOR_WIRE:
        assert units.FLOOR_WIRE[key][0] == 0.01, key
    for model_id, index in ((12000, 2), (23000, 0), (13000, 0), (1001, 6)):
        assert SCALES[(model_id, index)].steps is None, (model_id, index)
    assert SCALES[(29001, 4)].steps == 200


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

    # -- a Global EQ band's GAIN, 2026-09-11 ---------------------------------
    # Band 1's GAIN driven over the wire and the Global EQ page read each time,
    # on CorOS 4.0.1. The first two are the ENDS, which is what this run was
    # for: the span used to be the MANUAL's on two interior points 6 dB apart
    # on a range claimed to be 24 dB wide, and two close points cannot tell one
    # span from a wider one - the trap that put -100..+30 in this file for two
    # releases. The quartiles rule out a taper as well; see
    # `test_the_global_eq_gain_quartiles_rule_out_a_taper` below.
    ("GLOBAL_EQ_GAIN_DB", 0.0, -12.0, 1),
    ("GLOBAL_EQ_GAIN_DB", 1.0, 12.0, 1),
    ("GLOBAL_EQ_GAIN_DB", 0.25, -6.0, 1),
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


#: How far the exponent may sit from 1.0 and still be called linear. Set by what
#: the SCREEN could have shown, not by what the readings happen to give: a skew
#: this far from unity moves the display by 0.088 dB at worst anywhere on this
#: control, which is under one 0.1 dB step. So a law this test admits is one the
#: screen could not have told from a straight line at any point on the travel.
#: The 2026-09-11 readings pin 0.994..1.006 and clear it with about 40% to
#: spare - deliberately, so the gate is not fitted to them. A COARSER future
#: reading widens its band and can push past this, which is the failure this
#: guards; a finer one moves further inside.
LINEAR_SKEW_TOLERANCE = 0.01


def test_the_global_eq_gain_quartiles_rule_out_a_taper():
    """What the two quartile readings buy beyond the two ends.

    The ends settle the SPAN. They say nothing about the shape between them, and
    a cab LEVEL is the standing proof that shape hides: three well-separated
    points fitted it beautifully and it is a power law with skew 0.202 (see
    `docs/protocol.md`). So this checks the readings actually discriminate,
    rather than trusting that four points must be enough.

    Under ADR-0015's one law a taper is `wire ** (1 / skew)`. Each interior
    reading admits a band of skews - the display rounds, so the true dB lies
    within half a step of what was read - and the readings together admit only
    the INTERSECTION of those bands. That intersection is what is asserted here,
    so the bound quoted in `units.SETTING_SPANS` and `docs/STEERING.md` is the
    one this test computes rather than a looser stand-in for it.
    """
    low, high = units.SETTING_SPANS["GLOBAL_EQ_GAIN_DB"]
    interior = [(w, s, d) for k, w, s, d in SETTING_READINGS
                if k == "GLOBAL_EQ_GAIN_DB" and 0.0 < w < 1.0]
    assert len(interior) >= 2, (
        "this rests on readings taken BETWEEN the ends - the ends themselves "
        "fit any skew, since 0 and 1 are fixed points of every power law")

    bands = []
    for wire, screen, digits in interior:
        # The screen rounds to `digits`, so the true dB lies within half a step
        # of what was read; turn that into the exponents that could have
        # produced it. Taken from the reading rather than hard-coded, so each
        # band is tied to the precision actually recorded: a reading taken to
        # fewer digits widens its band and stops constraining the answer, which
        # is correct, where a hard-coded half-step would credit it with
        # precision it does not have. The opposite error - recording MORE digits
        # than the screen shows - narrows the band and overclaims, and no test
        # here can catch that; it is a discipline about writing readings down.
        half = 0.5 * 10 ** -digits
        bounds = [(screen + d - low) / (high - low) for d in (-half, half)]
        # A reading whose rounding band reaches a span END has no exponent to
        # solve for - 0 and 1 are fixed points of every power law - and one
        # sitting outside the span is not a reading of this control at all.
        # Both are `math.log` crashes rather than failures, so say which.
        assert all(0.0 < b < 1.0 for b in bounds), (
            f"the reading {screen} at wire {wire} rounds to within {half} of a "
            f"span end, so it constrains no exponent; drop it from this test "
            f"or record it to more digits")
        bands.append(sorted(math.log(wire) / math.log(b) for b in bounds))

    lowest, highest = max(b[0] for b in bands), min(b[1] for b in bands)
    assert lowest < 1.0 < highest, (
        f"the readings admit skews {lowest:.4f}..{highest:.4f}, which excludes "
        f"the linear law the span is converted with")
    assert abs(lowest - 1.0) < LINEAR_SKEW_TOLERANCE, (
        f"the readings only pin the skew to {lowest:.4f}..{highest:.4f}; that "
        f"is too loose to call the law linear rather than a taper close to it")
    assert abs(highest - 1.0) < LINEAR_SKEW_TOLERANCE, (
        f"the readings only pin the skew to {lowest:.4f}..{highest:.4f}; that "
        f"is too loose to call the law linear rather than a taper close to it")


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

"""Tests for the model catalog (pyquadcortex.protocol.catalog).

The catalog is parsed from the device's ModelRepo payload: a gzipped tar holding
a single ``ModelRepo.xml``. These tests build that container from a small
synthetic XML fixture, so they run offline and ship no vendor data.
"""

import gzip
import io
import tarfile

import pytest

from pyquadcortex.protocol import catalog, units

# A miniature ModelRepo covering every case the parser must handle: a plain
# factory model with parameters, a purchasable one (sku/plugin_id), a hidden
# one, an internal one, a model in a hidden category, and a Neural Capture
# (user content, whose ids are not stable across devices).
#
# The bounds are the device's OWN spellings. MIXER LEVEL and TEMPO carry
# symbolic ones because that is what the unit ships, and a fixture that wrote
# 0..1 there would be reproducing the bug this parser was fixed for.
SAMPLE_XML = """<?xml version="1.0" ?><Models>
<Category id="0" name="Guitar Overdrive">
  <Model blob="aaa" id="1" name="Myth Drive" tm="Based on Klon&#174; Centaur&#174;">
    <Parameter defaultValue="5" max="10" min="0" name="GAIN" type="float" units=""/>
    <Parameter defaultValue="5" max="10" min="0" name="TREBLE" type="float" units=""/>
  </Model>
  <Model blob="bbb" id="30" name="Plini Drive" plugin_id="7" sku="13"/>
  <Model blob="ccc" id="31" name="Secret Drive" hidden="true"/>
</Category>
<Category id="5" name="Compressor">
  <Model blob="ddd" id="5005" name="VCA Comp (M)">
    <Parameter defaultValue="-40" max="12" min="-60" name="THRESHOLD" type="float" units="dB"/>
  </Model>
</Category>
<Category id="14" name="Neural Capture">
  <Model blob="eee" id="14000" name="Eltron 30"/>
</Category>
<Category id="20" name="Neural Capture Internal">
  <Model blob="fff" id="20000" name="NC_Recorder" skip_self_test="true"/>
</Category>
<Category hidden="true" id="19" name="Utility_Deprecated">
  <Model blob="ggg" id="19000" name="Old Thing"/>
</Category>
<Category id="11" name="Mixer">
  <Model blob="mmm" id="11000" name="Mixer" internal="true">
    <Parameter defaultValue="0.769" max="MAX_MIXER_DB" min="MIN_MIXER_DB" name="MIXER LEVEL" type="float" units="dB" min_string="OFF"/>
    <Parameter defaultValue="5" max="10" min="0" name="PAN A" type="float" units=""/>
    <Parameter defaultValue="0" max="1" min="0" name="DUMMY" type="empty"/>
  </Model>
</Category>
<Category id="25" name="Tempo">
  <Model blob="ttt" id="25000" name="TempoControl" internal="true">
    <Parameter defaultValue="DEFAULT_TEMPO" max="MAX_TEMPO" min="MIN_TEMPO" name="TEMPO" type="float" units="BPM" steps="201" showAsInteger="true"/>
    <Parameter defaultValue="0" max="1" min="0" name="TYPE" type="switch"/>
    <Parameter defaultValue="1" max="1" min="0" name="LED LIGHT" type="switch"/>
    <Parameter defaultValue="0.6" max="9" min="-60" name="VOLUME" type="float" units="dB"/>
    <Parameter defaultValue="0" max="1" min="0" name="START" steps="2" type="toggleButton"/>
    <Parameter defaultValue="5" max="10" min="0" name="PAN" type="float"/>
    <Parameter defaultValue="0.1" max="1" min="0" name="TIME SIGNATURE" steps="21" type="comboBox"/>
    <Parameter defaultValue="0" max="1" min="0" name="NOTELENGTH" steps="4" type="comboBox"/>
    <Parameter defaultValue="0" max="1" min="0" name="SOUND" steps="6" type="comboBox"/>
    <Parameter defaultValue="0" max="1" min="0" name="ROUTING" steps="5" type="comboBox"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE0" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE1" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE2" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE3" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE4" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE5" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE6" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE7" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE8" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE9" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE10" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE11" steps="4" type="empty"/>
    <Parameter defaultValue="0" max="1" min="0" name="STEPSTATE12" steps="4" type="empty"/>
  </Model>
</Category>
<Category id="22" name="Internal Routing">
  <Model blob="hhh" id="22000" name="Router" internal="true"/>
</Category>
<Category id="24" name="Filter">
  <Model blob="iii" id="24003" name="Envelope Filter"/>
  <Model blob="jjj" id="24006" name="Envelope Filter" replaces="24003"/>
</Category>
</Models>"""


def make_payload(xml: str = SAMPLE_XML) -> bytes:
    """Wrap ``xml`` exactly as the device does: gzipped tar of ModelRepo.xml."""
    raw = xml.encode()
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        info = tarfile.TarInfo("ModelRepo.xml")
        info.size = len(raw)
        tf.addfile(info, io.BytesIO(raw))
    return gzip.compress(buf.getvalue())


@pytest.fixture
def cat():
    return catalog.parse_model_repo(make_payload())


def test_parses_models_keyed_by_wire_hash(cat):
    # The XML `id` attribute IS the value stored in Model.hash on the wire.
    assert cat[1].name == "Myth Drive"
    assert cat[5005].name == "VCA Comp (M)"
    assert cat[5005].category == "Compressor"
    assert cat[5005].category_id == 5


def test_model_carries_based_on_attribution(cat):
    assert cat[1].based_on == "Based on Klon® Centaur®"
    assert cat[5005].based_on == ""


def test_parameters_are_ordered_and_carry_metadata(cat):
    params = cat[1].parameters
    assert [p.name for p in params] == ["GAIN", "TREBLE"]
    gain = params[0]
    assert (gain.index, gain.minimum, gain.maximum, gain.default) == (0, 0.0, 10.0, 5.0)
    assert cat[5005].parameters[0].units == "dB"


def test_lookup_parameter_by_name_is_case_insensitive(cat):
    assert cat[1].parameter("gain").index == 0
    assert cat[1].parameter("TREBLE").index == 1
    with pytest.raises(KeyError):
        cat[1].parameter("nope")


def test_purchasable_models_are_flagged_not_factory(cat):
    plini = cat[30]
    assert plini.sku == "13"
    assert plini.is_factory is False


def test_hidden_internal_and_capture_models_are_not_factory(cat):
    assert cat[31].is_factory is False       # hidden model
    assert cat[22000].is_factory is False    # internal model
    assert cat[19000].is_factory is False    # model in a hidden category
    assert cat[14000].is_factory is False    # Neural Capture: user content
    assert cat[20000].is_factory is False    # capture internals


def test_plain_models_are_factory(cat):
    assert cat[1].is_factory is True
    assert cat[5005].is_factory is True


def test_factory_models_helper_returns_only_factory(cat):
    ids = {m.id for m in cat.factory_models()}
    assert ids == {1, 5005, 24003, 24006}


def test_find_by_name_is_case_insensitive_and_exact(cat):
    assert cat.find("vca comp (m)").id == 5005
    with pytest.raises(KeyError):
        cat.find("no such model")


def test_by_category_groups_models(cat):
    names = [m.name for m in cat.by_category("Guitar Overdrive")]
    assert "Myth Drive" in names and "Plini Drive" in names


def test_catalog_is_iterable_and_sized(cat):
    assert len(cat) == 12
    assert {m.id for m in cat} == {1, 30, 31, 5005, 11000, 14000, 20000, 19000,
                                   22000, 24003, 24006, 25000}


def test_missing_model_raises_keyerror(cat):
    with pytest.raises(KeyError):
        cat[999999]


def test_accepts_uncompressed_or_bare_xml_payloads():
    # Defensive: the transport may hand us already-gunzipped bytes, and a bare
    # XML payload should still parse.
    plain_tar = gzip.decompress(make_payload())
    assert catalog.parse_model_repo(plain_tar)[1].name == "Myth Drive"
    assert catalog.parse_model_repo(SAMPLE_XML.encode())[1].name == "Myth Drive"


# -- unit conversion ----------------------------------------------------------
# Confirmed on hardware: the wire carries a normalized 0..1 float. Sending 1.0
# to the VCA Comp's THRESHOLD (catalog range -60..+12 dB) made the unit display
# +12.0 dB, so normalized 1.0 maps to the parameter's maximum.


def test_parameter_converts_real_units_to_normalized(cat):
    thr = catalog.Parameter(index=0, name="THRESHOLD", minimum=-60.0,
                            maximum=12.0, default=-40.0, units="dB")
    assert thr.to_normalized(12.0) == pytest.approx(1.0)
    assert thr.to_normalized(-60.0) == pytest.approx(0.0)
    assert thr.to_normalized(-24.0) == pytest.approx(0.5)


def test_parameter_converts_normalized_back_to_real_units(cat):
    thr = catalog.Parameter(index=0, name="THRESHOLD", minimum=-60.0,
                            maximum=12.0, default=-40.0, units="dB")
    assert thr.to_real(1.0) == pytest.approx(12.0)
    assert thr.to_real(0.0) == pytest.approx(-60.0)
    assert thr.to_real(0.5) == pytest.approx(-24.0)


def test_real_unit_conversion_refuses_out_of_range(cat):
    """Refused, not clamped. A clamped write looks like it worked.

    This used to clamp. Both behaviours were in the library at once - the
    catalog path clamped and the measured-span path refused - and unifying them
    on the catalog meant picking one. Refusing is the project's rule: a setting
    the unit does not have is a mistake, not a request to round.
    """
    thr = catalog.Parameter(index=0, name="THRESHOLD", minimum=-60.0,
                            maximum=12.0, default=-40.0, units="dB")
    assert thr.to_normalized(12.0) == pytest.approx(1.0)
    assert thr.to_normalized(-60.0) == pytest.approx(0.0)
    for outside in (999.0, -999.0):
        with pytest.raises(ValueError, match="does not exist there"):
            thr.to_normalized(outside)


def test_degenerate_range_does_not_divide_by_zero(cat):
    flat = catalog.Parameter(index=0, name="X", minimum=5.0, maximum=5.0, default=5.0)
    assert flat.to_normalized(5.0) == 0.0
    assert flat.to_real(1.0) == 5.0


# -- superseded models --------------------------------------------------------
# Some models are replaced by newer ones carrying the SAME display name (the
# catalog has two "Graphic-9" equalizers, 4005 replaces=4002). The `replaces`
# attribute is what tells them apart, and it decides which one earns the clean
# generated constant name.


def test_replaces_is_parsed_onto_the_replacement(cat):
    assert cat[24006].replaces == (24003,)
    assert cat[24003].replaces == ()


def test_the_replaced_model_is_marked_superseded(cat):
    assert cat[24003].superseded is True
    assert cat[24006].superseded is False
    assert cat[5005].superseded is False


def test_superseded_models_are_still_factory_and_still_resolvable(cat):
    # An old preset can still reference a superseded model, so reading must work.
    assert cat[24003].is_factory is True
    assert cat[24003].name == "Envelope Filter"


# -- symbolic bounds ----------------------------------------------------------
# This library spent months believing in a "placeholder range": a parameter
# published as 0..1 with a real-world unit and therefore unconvertible. There is
# no such thing. Zero parameters in the shipped catalog are published that way.
#
# What actually happens is that `min` and `max` are sometimes a NAME -
# min="MIN_CABSIM_DB" - and the parser's float conversion fell back to 0.0 and
# 1.0 for anything it could not read. That fallback invented the concept, and
# a table of hand-measured spans grew for months to work around it.


def test_a_symbolic_bound_resolves_to_its_firmware_number():
    xml = ('<Models><Category id="12" name="Cabsim Guitar (M)">'
           '<Model id="12000" name="Default Cabsim">'
           '<Parameter name="LEVEL" type="float" units="dB" defaultValue="0.5"'
           ' min="MIN_CABSIM_DB" max="MAX_CABSIM_DB" skew="4.9594844"'
           ' min_string="OFF"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[12000].parameters[0]
    assert (p.minimum, p.maximum) == (-40.0, 6.0)


def test_a_symbolic_bound_nobody_has_measured_becomes_None():
    """It then refuses to convert, rather than answering against a guess."""
    xml = ('<Models><Category id="20" name="Neural Capture Internal">'
           '<Model id="20000" name="NC_Recorder">'
           '<Parameter name="OUT LEVEL" type="float" units="dB" steps="41"'
           ' min="MIN_INPUT_TRIM" max="MAX_INPUT_TRIM"'
           ' defaultValue="MAX_INPUT_TRIM"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[20000].parameters[0]
    assert p.minimum is None and p.maximum is None
    with pytest.raises(ValueError, match="nobody has measured"):
        p.to_real(0.5)


def test_a_bound_this_build_has_never_heard_of_is_loud():
    """A firmware update adding a constant must fail, not silently become 0..1.

    Falling back is exactly what created the placeholder-range bug, so the
    parser refuses rather than inventing a span.
    """
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="Widget">'
           '<Parameter name="Z" type="float" min="MIN_FUTURE_THING" max="1"'
           ' defaultValue="0"/>'
           '</Model></Category></Models>')
    with pytest.raises(ValueError, match="MIN_FUTURE_THING"):
        catalog.parse_model_repo(make_payload(xml))


def test_a_measured_family_carries_its_floor_from_the_units_table():
    """min_string="OFF" says the bottom is a word; only measurement says where
    the numbers resume."""
    xml = ('<Models><Category id="12" name="Cabsim Guitar (M)">'
           '<Model id="12000" name="Default Cabsim">'
           '<Parameter name="LEVEL" type="float" units="dB" defaultValue="0.5"'
           ' min="MIN_CABSIM_DB" max="MAX_CABSIM_DB" skew="4.9594844"'
           ' min_string="OFF"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[12000].parameters[0]
    assert p.floor_wire == 0.01
    assert p.floor == pytest.approx(-21.8, abs=0.05)


def test_the_placeholder_concept_is_gone():
    """There was never such a thing - see ADR-0015."""
    assert not hasattr(catalog.Parameter, "range_is_placeholder")
    assert not hasattr(units, "MEASURED_SPANS")


def test_a_unitless_parameter_on_the_same_model_still_converts(cat):
    pan = cat[11000].parameter("PAN A")
    assert pan.to_real(0.5) == pytest.approx(5.0)
    assert pan.to_normalized(10.0) == pytest.approx(1.0)


# -- list parameters: the catalog's `steps` is the option count -----------------
# Confirmed against the tempo controls: NOTELENGTH steps=4 and option 1 stored
# 0.3333; TIME SIGNATURE steps=21 and option 1 stored 0.05; ROUTING steps=5 and
# option 3 stored 0.75.


def test_option_count_comes_from_steps_for_a_list_parameter(cat):
    notelength = catalog.Parameter(index=7, name="NOTELENGTH", minimum=0.0,
                                   maximum=3.0, default=0.0, type="comboBox", steps=4)
    assert notelength.option_count == 4
    assert notelength.option_to_value(1) == pytest.approx(1 / 3)
    assert notelength.value_to_option(0.333333343) == 1
    tsig = catalog.Parameter(index=6, name="TIME SIGNATURE", minimum=0.0,
                             maximum=20.0, default=0.0, type="comboBox", steps=21)
    assert tsig.option_to_value(1) == pytest.approx(0.05)
    routing = catalog.Parameter(index=9, name="ROUTING", minimum=0.0, maximum=4.0,
                                default=0.0, type="comboBox", steps=5)
    assert routing.option_to_value(3) == pytest.approx(0.75)
    assert routing.value_to_option(0.75) == 3


def test_empty_typed_params_count_as_lists_only_when_they_carry_steps(cat):
    """The per-beat metronome cells are typed ``empty`` yet publish ``steps=4``.

    Counting them was safe to add because the catalog is small enough to check
    exhaustively: 16 parameters are typed ``empty`` - the 13 STEPSTATE cells with
    steps=4, and three DUMMY entries with no steps. Requiring steps therefore
    admits exactly the beats.
    """
    beat = cat[25000].parameters[10]
    assert beat.name == "STEPSTATE0"
    assert beat.type == "empty"
    assert beat.option_count == 4
    assert beat.option_to_value(2) == pytest.approx(2 / 3)
    assert beat.value_to_option(0.666666687) == 2
    dummy = cat[11000].parameter("DUMMY")
    assert dummy.type == "empty" and dummy.steps is None
    assert dummy.option_count is None


def test_option_helpers_reject_a_non_list_parameter_and_a_bad_option(cat):
    plain = catalog.Parameter(index=0, name="TEMPO", minimum=0.0, maximum=1.0,
                              default=0.5, type="float", steps=201)
    assert plain.option_count is None
    with pytest.raises(ValueError, match="not a list"):
        plain.option_to_value(1)
    routing = catalog.Parameter(index=9, name="ROUTING", minimum=0.0, maximum=4.0,
                               default=0.0, type="comboBox", steps=5)
    with pytest.raises(ValueError, match="5 options"):
        routing.option_to_value(5)
    with pytest.raises(ValueError, match="5 options"):
        routing.option_to_value(-1)


# --- The taper -------------------------------------------------------------
#
# The catalog publishes a `skew` attribute on 1,200 parameters and this library
# ignored it for several releases, converting every one of them as a straight
# line. 615 of them convert non-linearly, so 615 conversions were wrong.


@pytest.mark.parametrize("raw, expected", [
    (None, 1.0),
    ("LIN_SKEW", 1.0),
    ("1", 1.0),
    ("1.0", 1.0),
    ("LOG_SKEW", 0.3),
    ("0.3", 0.3),
    ("4.9594844", 4.9594844),
    (" 0.4", 0.4),      # the shipped catalog carries a leading space, twice
    ("", 1.0),          # and nothing at all, twice
])
def test_parse_skew_cleans_what_the_device_actually_ships(raw, expected):
    assert catalog.parse_skew(raw) == pytest.approx(expected)


@pytest.mark.parametrize("raw", ["nonsense", "EXP_SKEW", "0", "-2", "1e400"])
def test_parse_skew_refuses_a_taper_it_cannot_decode(raw):
    """It used to fall back to linear, and that was the wrong call.

    A named taper nobody has decoded would convert silently wrong by a factor
    of 25 at quarter travel - a Low-High Cut's HPF FREQ asked for 217 Hz would
    land near 24 Hz. `_as_bound` already refuses an unknown BOUND for exactly
    that reason; a wrong taper is no more forgivable than a wrong bound.

    An absent or empty attribute still means linear, because 2,609 parameters
    say so by carrying nothing and two more carry "".
    """
    with pytest.raises(ValueError):
        catalog.parse_skew(raw)


def test_log_skew_is_not_a_log_sweep():
    """The name is the device's and it is misleading.

    Confirmed on hardware 2026-08-26 - see the constant's docstring. Guarding
    the value here because the obvious "fix" is to make it logarithmic, and the
    unit says otherwise.
    """
    assert catalog.LOG_SKEW == 0.3


def _knob(minimum, maximum, skew, units=""):
    return catalog.Parameter(index=0, name="X", minimum=minimum, maximum=maximum,
                             default=0.0, units=units, type="float", skew=skew)


@pytest.mark.parametrize("minimum, maximum, skew, wire, screen, tol", [
    # Low-High Cut HPF FREQ, read 217 Hz on screen at wire 0.25.
    (20.0, 20000.0, 0.3, 0.25, 217.0, 0.5),
    # The same block's LPF FREQ, read 7678 Hz at wire 0.75.
    (20.0, 20000.0, 0.3, 0.75, 7678.0, 0.5),
    # The same block's OUTPUT, which carries no skew, read -10.0 dB at 0.25.
    (-20.0, 20.0, 1.0, 0.25, -10.0, 0.05),
    # An Envelope Filter's LOG_SKEW knobs: FREQ read 197 Hz, RESO read 4.45.
    (100.0, 10000.0, 0.3, 0.25, 197.0, 0.5),
    (1.0, 10.0, 0.3, 0.75, 4.45, 0.005),
    # A cab LEVEL, whose taper took three days to fit and one attribute to read.
    (-40.0, 6.0, 4.9594844, 0.01, -21.8, 0.05),
    (-40.0, 6.0, 4.9594844, 0.50, 0.0, 0.05),
    (-40.0, 6.0, 4.9594844, 1.00, 6.0, 0.05),
])
def test_to_real_reproduces_what_the_screen_showed(
        minimum, maximum, skew, wire, screen, tol):
    """Every row was read off the unit's own display. See docs/protocol.md."""
    assert _knob(minimum, maximum, skew).to_real(wire) == pytest.approx(screen, abs=tol)


def test_to_normalized_is_the_inverse_of_to_real():
    knob = _knob(20.0, 20000.0, 0.3)
    for wire in (0.0, 0.01, 0.25, 0.5, 0.75, 1.0):
        assert knob.to_normalized(knob.to_real(wire)) == pytest.approx(wire, abs=1e-9)


def test_a_linear_knob_is_untouched_by_the_change():
    """The 2,609 parameters with no `skew` attribute must convert as before."""
    knob = _knob(-60.0, 12.0, 1.0, units="dB")
    assert knob.to_real(1.0) == pytest.approx(12.0)
    assert knob.to_real(0.5) == pytest.approx(-24.0)
    assert knob.to_normalized(-24.0) == pytest.approx(0.5)


def test_the_parser_reads_skew_off_the_xml():
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="FREQ" type="float" min="20" max="20000"'
           ' units="Hz" skew="0.3" defaultValue="0"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.skew == pytest.approx(0.3)
    assert round(p.to_real(0.25)) == 217


# -- the attributes we used to discard -----------------------------------------
#
# The parser read 8 of the 24 attributes the device puts on a <Parameter>. These
# are the rest of the ones we can name a use for; the others are recorded in
# docs/domain-model.md's appendix rather than guessed at.


def test_option_names_come_from_the_catalog():
    """`set_param_option` said they do not. They always did.

    Its docstring read "the option names are not in the catalog - they are in
    the preset, per block". That is true of the 12 dynamic lists and of nothing
    else.
    """
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="MODE" type="comboBox" min="0" max="2" steps="3"'
           ' defaultValue="1" stepNames="Normal,Vibrato,Vibrato Bright Off"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.options == ("Normal", "Vibrato", "Vibrato Bright Off")
    assert p.dynamic is False
    assert p.option_count == 3
    assert p.option_to_value(2) == pytest.approx(1.0)


def test_padding_in_an_option_list_is_stripped():
    """The device pads some lists to line them up on screen."""
    assert catalog.parse_options("Flat,   -6, -12") == ("Flat", "-6", "-12")


def test_a_dynamic_list_is_marked_so_the_preset_stays_authoritative():
    """Its entries include one per upstream block, so `steps` overstates it."""
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="SOURCE" type="comboBox" dynamic="true" min="0"'
           ' max="44" steps="45" defaultValue="0" stepNames="Off,In 1,R1C1"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.dynamic is True
    # `steps` wins for a dynamic list, because the names are only a snapshot.
    assert p.option_count == 45


def test_the_labels_and_flags_are_read():
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="STEPS" type="float" min="1" max="16" steps="16"'
           ' defaultValue="1" expAssignable="false" showAsInteger="true"'
           ' min_string="OFF" max_string="MAX"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.exp_assignable is False
    assert p.show_as_integer is True
    assert (p.min_label, p.max_label) == ("OFF", "MAX")


def test_assignability_defaults_to_allowed():
    """Only 14 parameters in the shipped catalog say otherwise."""
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="GAIN" type="float" min="0" max="10"'
           ' defaultValue="5"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.exp_assignable is True

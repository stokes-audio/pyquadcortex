"""Tests for the model catalog (pyquadcortex.protocol.catalog).

The catalog is parsed from the device's ModelRepo payload: a gzipped tar holding
a single ``ModelRepo.xml``. These tests build that container from a small
synthetic XML fixture, so they run offline and ship no vendor data.
"""

import gzip
import io
import tarfile

import pytest

from pyquadcortex.protocol import catalog

# A miniature ModelRepo covering every case the parser must handle: a plain
# factory model with parameters, a purchasable one (sku/plugin_id), a hidden
# one, an internal one, a model in a hidden category, and a Neural Capture
# (user content, whose ids are not stable across devices).
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
    <Parameter defaultValue="0.769" max="1" min="0" name="MIXER LEVEL" type="float" units="dB"/>
    <Parameter defaultValue="5" max="10" min="0" name="PAN A" type="float" units=""/>
    <Parameter defaultValue="0" max="1" min="0" name="DUMMY" type="empty"/>
  </Model>
</Category>
<Category id="25" name="Tempo">
  <Model blob="ttt" id="25000" name="TempoControl" internal="true">
    <Parameter defaultValue="0.5" max="1" min="0" name="TEMPO" type="float" units="BPM"/>
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


def test_real_unit_conversion_clamps_out_of_range(cat):
    thr = catalog.Parameter(index=0, name="THRESHOLD", minimum=-60.0,
                            maximum=12.0, default=-40.0, units="dB")
    assert thr.to_normalized(999.0) == pytest.approx(1.0)
    assert thr.to_normalized(-999.0) == pytest.approx(0.0)


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


# -- placeholder ranges -------------------------------------------------------
# Some parameters are published as 0..1 with a real-world unit: the mixer,
# splitter and lane-output LEVEL controls are 0..1 "dB", and TEMPO is 0..1 "BPM".
# That is the wire's own normalized scale, not the span the control covers, so
# there is nothing to convert and the true span is not in the catalog. Measured:
# those level parameters read 0.76923077 (10/13, i.e. 0 dB on -40..+12) on every
# row carrying one across 17 factory presets.


def test_a_zero_to_one_range_with_a_unit_is_flagged_as_a_placeholder(cat):
    level = cat[11000].parameter("MIXER LEVEL")
    assert level.minimum == 0.0 and level.maximum == 1.0 and level.units == "dB"
    assert level.range_is_placeholder is True


def test_a_real_range_is_not_a_placeholder(cat):
    assert cat[5005].parameter("THRESHOLD").range_is_placeholder is False


def test_a_zero_to_one_range_without_a_unit_is_not_a_placeholder(cat):
    # A unitless 0..1 is a genuine fraction - a switch, a mix control - and
    # converts fine, so only the ones claiming a real-world unit are suspect.
    plain = catalog.Parameter(index=0, name="PHASE", minimum=0.0, maximum=1.0,
                              default=0.0, units="", type="switch", steps=2)
    assert plain.range_is_placeholder is False


def test_placeholder_conversions_refuse_rather_than_mislead(cat):
    level = cat[11000].parameter("MIXER LEVEL")
    with pytest.raises(ValueError, match="placeholder range"):
        level.to_real(0.76923077)
    with pytest.raises(ValueError, match="value="):
        level.to_normalized(0.0)


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

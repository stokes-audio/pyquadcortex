"""Tests for the model catalog (pyquadcortex.catalog).

The catalog is parsed from the device's ModelRepo payload: a gzipped tar holding
a single ``ModelRepo.xml``. These tests build that container from a small
synthetic XML fixture, so they run offline and ship no vendor data.
"""

import gzip
import io
import tarfile

import pytest

from pyquadcortex import catalog

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
    <Parameter defaultValue="0.5" max="1" min="0" name="THRESHOLD" type="float" units="dB"/>
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
<Category id="22" name="Internal Routing">
  <Model blob="hhh" id="22000" name="Router" internal="true"/>
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
    assert ids == {1, 5005}


def test_find_by_name_is_case_insensitive_and_exact(cat):
    assert cat.find("vca comp (m)").id == 5005
    with pytest.raises(KeyError):
        cat.find("no such model")


def test_by_category_groups_models(cat):
    names = [m.name for m in cat.by_category("Guitar Overdrive")]
    assert "Myth Drive" in names and "Plini Drive" in names


def test_catalog_is_iterable_and_sized(cat):
    assert len(cat) == 8
    assert {m.id for m in cat} == {1, 30, 31, 5005, 14000, 20000, 19000, 22000}


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

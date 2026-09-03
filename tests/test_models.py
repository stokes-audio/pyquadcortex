"""Tests for the generated factory-model constants (pyquadcortex.protocol.models).

The constants are generated from a device's ModelRepo by
``scripts/generate_models.py`` and cover only FACTORY models - the ones every
Quad Cortex is guaranteed to have. Purchased plugin models and the player's own
Neural Captures are deliberately absent: their ids are not portable, so they
must be looked up at runtime through ``qc.catalog``.
"""

import pytest

from pyquadcortex.protocol import models


def test_anchors_match_ids_confirmed_on_hardware():
    assert models.GuitarOverdrive.CHIEF_DS1 == 10
    assert models.Compressor.VCA_COMP_M == 5005
    assert models.Equalizer.GRAPHIC_9 == 4005
    assert models.Utility.ADAPTIVE_GATE == 16001


def test_categories_are_exposed_as_classes():
    for name in ("GuitarOverdrive", "GuitarAmplifier", "BassAmplifier",
                 "Compressor", "Delay", "Reverb", "Equalizer"):
        assert hasattr(models, name), f"missing category class {name}"


def test_all_constants_are_positive_ints():
    for model_id in models.ALL.values():
        assert isinstance(model_id, int) and model_id > 0


def test_ids_are_unique_across_categories():
    ids = list(models.ALL.values())
    assert len(ids) == len(set(ids))


def test_all_maps_qualified_names_to_ids():
    assert models.ALL["GuitarOverdrive.CHIEF_DS1"] == 10
    assert models.ALL["Compressor.VCA_COMP_M"] == 5005


def test_excludes_purchasable_and_capture_content():
    # Archetype plugin models (sku) and Neural Captures must not be constants:
    # a given unit may not have them, and capture ids are user slots.
    flat = set(models.ALL.values())
    assert 30 not in flat        # Plini Drive - purchasable
    assert 14000 not in flat     # a Neural Capture slot
    assert not any(k.startswith("NeuralCapture") for k in models.ALL)


def test_constants_are_usable_where_a_model_is_expected():
    # They are plain ints, so they pass straight to set_block.
    assert isinstance(models.Reverb.__dict__.get("__doc__"), (str, type(None)))
    some_id = models.Compressor.VCA_COMP_M
    assert int(some_id) == some_id


def test_count_is_the_full_factory_set():
    # 420 factory models on CorOS 4.1.0; a drift here means the generator
    # was re-run against a device with different content - re-check before
    # updating this number.
    assert len(models.ALL) == 420


def test_unknown_attribute_raises():
    with pytest.raises(AttributeError):
        models.Compressor.NO_SUCH_MODEL

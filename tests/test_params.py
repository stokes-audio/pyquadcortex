"""Tests for the generated parameter constants (pyquadcortex.protocol.params).

Generated from a device's ModelRepo by ``scripts/generate_params.py``, and
FACTORY-only for the same reason ``models.py`` is: a unit may not have a
purchased model, and capture ids are user slots.

The anchors below are values confirmed against hardware or against the unit's
own editor. They are what stops a regeneration silently renumbering something.
"""
import pytest

from pyquadcortex.protocol import params


def test_the_container_enums_match_what_the_targets_address():
    """These six are the parameters a target reaches, so they matter most."""
    assert params.LaneOutputParam.VOLUME == 0
    assert params.LaneOutputParam.PAN == 1
    assert params.LaneOutputParam.MUTE == 2
    assert params.LaneOutputParam.SOLO == 3
    assert params.LaneInputParam.NOISE_REDUCTION == 0
    assert params.LaneInputParam.BYPASS == 1
    assert params.LaneInputParam.INPUT_GAIN == 3
    assert params.TempoParam.TEMPO == 0


def test_a_member_is_its_wire_index():
    """IntEnum, so passing one needs no catalog - which is also the fast path."""
    assert isinstance(params.LaneOutputParam.VOLUME, int)
    assert params.LaneOutputParam.VOLUME + 0 == 0
    assert int(params.TempoParam.VOLUME) == 3


def test_the_tempo_names_agree_with_the_target_map():
    """`Tempo.NAMES` resolves screen names; the enum must not disagree with it."""
    from pyquadcortex.protocol.targets import Tempo

    for name, index in Tempo.NAMES.items():
        member = name.replace(" ", "_")
        if member in params.TempoParam.__members__:
            assert params.TempoParam[member] == index, (
                f"TempoParam.{member} is {params.TempoParam[member]} but "
                f"Tempo.NAMES says {index}")


# -- the cab layout, and why it is one enum rather than 140 -------------------


def test_the_cab_mic_mapping_is_the_one_measured_on_the_unit():
    """Mic 1 read POSITION 2.9 / DIST 3.0 on screen, against wire 0.29 / 0.30.

    Mic 2 read 5.6 / 3.3 against 0.56 / 0.33. That cross-check is what maps a
    mic to an index; ordering alone would have been an assumption, and this
    project has been wrong that way before.
    """
    assert params.Cabsim.MIC_1_POSITION == 5
    assert params.Cabsim.MIC_1_DISTANCE == 4
    assert params.Cabsim.MIC_2_POSITION == 13
    assert params.Cabsim.MIC_2_DISTANCE == 12
    assert params.Cabsim.MIC_1_IR_SELECTOR == 1
    assert params.Cabsim.MIC_2_IR_SELECTOR == 9


def test_the_cab_has_shared_controls_that_belong_to_neither_mic():
    for name in ("HPF", "LPF", "OUTPUT_VOLUME"):
        assert name in params.Cabsim.__members__
        assert not name.startswith("MIC_")


def test_no_per_cab_enum_is_emitted():
    """140 cab models share ONE layout - the catalog under-describes them.

    A per-cab enum would carry the two mic selectors the catalog lists and hide
    the other 20 parameters the wire actually has, which reads as complete.
    """
    assert not hasattr(params, "N212DarkglassNeoM")
    assert hasattr(params, "Cabsim")


def test_the_ir_loader_slots_match_what_set_ir_drives():
    """`IR_PATH_PARAMS = (2, 10)` was measured before this file existed."""
    from pyquadcortex.protocol.client import QuadCortex

    assert params.SingleM.IR_1_PATH == QuadCortex.IR_PATH_PARAMS[0]
    assert params.SingleM.IR_2_PATH == QuadCortex.IR_PATH_PARAMS[1]
    assert params.SingleM.IR_1_NAME == QuadCortex.IR_NAME_PARAMS[0]
    assert params.SingleM.IR_2_NAME == QuadCortex.IR_NAME_PARAMS[1]


# -- shape of the generated file ---------------------------------------------


def test_both_occurrences_of_a_repeated_name_are_numbered():
    """A bare first member would read like the real one and hide the pair."""
    for enum, group in ((params.Cabsim, "MIC"), (params.SingleM, "IR")):
        numbered = [n for n in enum.__members__ if n.startswith(f"{group}_")]
        assert numbered, f"{enum.__name__} has no {group} group"
        ones = {n for n in numbered if n.startswith(f"{group}_1_")}
        twos = {n for n in numbered if n.startswith(f"{group}_2_")}
        assert len(ones) == len(twos), f"{enum.__name__} has a lopsided group"


def test_every_enum_has_unique_indices():
    for model_id, enum in params.BY_MODEL.items():
        values = [int(m) for m in enum]
        assert len(values) == len(set(values)), f"{enum.__name__} repeats an index"


def test_by_model_maps_ids_to_their_enum():
    assert params.BY_MODEL[10] is params.ChiefDs1
    for model_id, enum in params.BY_MODEL.items():
        assert isinstance(model_id, int) and model_id > 0
        assert issubclass(enum, __import__("enum").IntEnum)


def test_no_member_starts_with_a_digit_or_shadows_a_keyword():
    import keyword

    for enum in params.BY_MODEL.values():
        for name in enum.__members__:
            assert not name[0].isdigit(), f"{enum.__name__}.{name}"
            assert not keyword.iskeyword(name.lower()), f"{enum.__name__}.{name}"

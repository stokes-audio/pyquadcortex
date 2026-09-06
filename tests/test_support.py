"""The vocabulary every profile speaks (spec section 1, "Modules")."""
import dataclasses

import pytest

from pyquadcortex.protocol import support


def test_support_has_exactly_the_two_modes_the_spec_names():
    assert {m.name for m in support.Support} == {"VERIFIED", "EXPERIMENTAL"}


def test_evidence_has_exactly_the_three_levels_the_spec_names():
    assert {e.name for e in support.Evidence} == {"MAINTAINER", "CONTRIBUTED", "STUB"}


def test_everything_contains_any_name_and_says_what_it_is():
    assert "set_scene_label" in support.EVERYTHING
    assert "anything_at_all" in support.EVERYTHING
    assert repr(support.EVERYTHING) == "EVERYTHING"


def test_hardware_is_frozen_and_takes_the_two_facts():
    hw = support.Hardware(footswitches=8, expression_ports=2)
    assert (hw.footswitches, hw.expression_ports) == (8, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        hw.footswitches = 4  # type: ignore[misc]


def test_no_snapshot_refuses_every_attribute_and_names_the_generator():
    missing = support.NoSnapshot("coros_4_1_0")
    with pytest.raises(AttributeError) as caught:
        missing.Delay
    text = str(caught.value)
    assert "coros_4_1_0" in text
    assert "--snapshot coros_4_1_0" in text
    assert repr(missing) == "NoSnapshot('coros_4_1_0')"


class QuadCortex41:
    """A stand-in profile class; only its name and MEASURED_ON are read.

    A real class, not an object carrying `__name__` as an attribute, because
    that is what `unverified_text` is handed at every call site.
    """

    MEASURED_ON = ("4.1.0",)


def test_unverified_text_names_the_profile_the_firmware_and_both_ways_out():
    evidence, workaround = support.unverified_text(QuadCortex41, "set_scene_label")
    assert evidence == "not yet verified on QuadCortex41 (CorOS 4.1.0)"
    assert "connect(support=Support.EXPERIMENTAL)" in workaround
    assert "pytest tests/hardware --hardware --verifies set_scene_label" in workaround
    assert "QuadCortex41.VERIFIED" in workaround

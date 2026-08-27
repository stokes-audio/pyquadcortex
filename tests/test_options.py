"""The generated option enums (pyquadcortex.protocol.options).

A list-valued parameter stores ``index / (count - 1)``, so picking an option
means knowing its position. The names come from the catalog's ``stepNames``,
which this library did not read until 2026-08-26.

What these tests protect is the MAPPING - that a member's value is the position
the device expects, and that our tidied-up member name never reaches the wire in
place of the device's own spelling.
"""

import re

import pytest

from pyquadcortex.protocol import options


def test_the_module_covers_the_lists_the_generator_found():
    assert len(options.OPTION_LABELS) == 111
    assert set(options.__all__) == {e.__name__ for e in options.OPTION_LABELS} | {
        "OPTION_LABELS"}


@pytest.mark.parametrize("enum_type", list(options.OPTION_LABELS))
def test_every_member_sits_at_its_wire_position(enum_type):
    """The value IS the index, because that is what the wire encodes."""
    labels = options.OPTION_LABELS[enum_type]
    members = list(enum_type)
    assert len(members) == len(labels), enum_type.__name__
    for position, member in enumerate(members):
        assert member.value == position


@pytest.mark.parametrize("enum_type", list(options.OPTION_LABELS))
def test_no_enum_is_a_disguised_boolean(enum_type):
    """`Off,On` parameters take True and False, so no enum is emitted for them.

    247 of the 527 fixed lists are exactly that pair, and `OffOn.ON` says
    nothing `True` does not.
    """
    labels = tuple(label.lower() for label in options.OPTION_LABELS[enum_type])
    assert labels != ("off", "on"), enum_type.__name__


@pytest.mark.parametrize("enum_type", list(options.OPTION_LABELS))
def test_every_member_name_is_a_usable_python_name(enum_type):
    for member in enum_type:
        assert re.fullmatch(r"[A-Z][A-Z0-9_]*", member.name), (
            f"{enum_type.__name__}.{member.name}")


def test_a_note_length_reads_as_a_note_length():
    """The mangling has to survive contact with '1/64T' and '1/8D'."""
    assert options.SyncNote21.N1_64T == 0
    assert options.SyncNote21.N1_8D == 12
    assert options.OPTION_LABELS[options.SyncNote21][12] == "1/8D"


def test_a_negative_option_reads_as_a_negative_option():
    """Confirmed on hardware: wire 0.25 on a Low-High Cut's HPF SLOPE - option
    2 of 9 - showed '-12 dB/o' on the unit, 2026-08-26."""
    assert options.HpfSlope.MINUS_12 == 2
    assert options.OPTION_LABELS[options.HpfSlope][2] == "-12"


def test_the_device_typo_is_corrected_in_the_name_and_kept_on_the_wire():
    """16 INVERT parameters offer 'Noral'. We must send that, not our spelling.

    This is the one entry in the generator's SPELLING_FIXES, and the split it
    demonstrates is the whole point of OPTION_LABELS existing: member names are
    ours, strings are the device's.
    """
    assert options.Invert.NORMAL == 0
    assert options.OPTION_LABELS[options.Invert] == ("Noral", "Inverted")


def test_names_do_not_collide_into_gibberish():
    """14 different lists are somebody's MODE, and 6 are somebody's SYNC NOTE.

    Naming them Mode2, Mode2_, Mode2__ would be unusable, so a colliding list is
    qualified by its model - which is what a caller has in hand.
    """
    names = {e.__name__ for e in options.OPTION_LABELS}
    assert not [n for n in names if n.endswith("_")]
    assert "SplitterMode" in names and "PliniDriveMode" in names


@pytest.mark.parametrize("enum_type", list(options.OPTION_LABELS))
def test_the_labels_are_stripped_but_otherwise_verbatim(enum_type):
    """The device pads some lists to line them up on screen; nothing else is
    touched, because a dynamic list is matched by string."""
    for label in options.OPTION_LABELS[enum_type]:
        assert label == label.strip()

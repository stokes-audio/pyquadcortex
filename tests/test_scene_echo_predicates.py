"""The scene echo predicates, checked offline against real captured echoes.

``tests/hardware/test_write_echo.py`` only runs with a unit attached, so a
predicate in it can stay quietly unsatisfiable for as long as nobody runs the
suite - and one did. Both scene predicates guarded on ``field_present(m,
"index")``, but ``SceneLabelMessage.index`` and ``SceneColorMessage.index`` are
bare proto3 scalars with no presence, so ``field_present`` answered ``False``
for every message that has ever existed. The predicates could not match, and the
suite reported "scene label write produced no echo at all" while the unit was
echoing correctly in 25 ms.

That is the ``ColBypass.column`` mistake from docs/domain-model.md wearing a
different hat: a filter on a presence-less field matches nothing, and the
absence reads as a fact about the device rather than about the filter.

These tests feed the predicates the exact bytes the device sent and assert they
say yes, so a predicate that cannot match its own echo fails here - offline, in
CI, with no unit attached.
"""
import importlib.util
from pathlib import Path

import pytest

from pyquadcortex.client import field_present
from pyquadcortex.proto import ProductionAutomation_pb2 as pa

#: REAL echoes, captured 2026-08-11 from the connected unit at 0.40.0 by tapping
#: the RX dispatch across a ``set_scene_label(1, "echo probe")`` and a
#: ``set_scene_color(1, 4294911783)``. These are the payload bytes the device
#: sent back, unmodified. ``index`` does ride the wire in both - it is the
#: ``1801`` pair, field 3 holding 1 - which is what makes the presence guard's
#: verdict wrong rather than merely conservative.
SCENE_LABEL_ECHO = bytes.fromhex("08011801220a6563686f2070726f6265")
SCENE_COLOR_ECHO = bytes.fromhex("0801180120a7cefcff0f")

#: The action the device stamps on both echoes above. Carried on the synthetic
#: messages below so they are shaped like the real thing, though no predicate
#: here reads it.
UPDATE = pa.MessageAction.Enum.Value("UPDATE")

_HARDWARE_SUITE = Path(__file__).parent / "hardware" / "test_write_echo.py"


@pytest.fixture(scope="module")
def echo_suite():
    """The hardware module imported as a plain module, not collected.

    ``tests/hardware/conftest.py`` refuses to *collect* it without ``--hardware``;
    importing it is a different thing and has to stay safe, which it is because
    nothing at its module scope touches a device. If that ever stops being true
    this fixture is where it will show up first.
    """
    spec = importlib.util.spec_from_file_location(
        "hardware_write_echo", _HARDWARE_SUITE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_scene_label_predicate_matches_the_real_echo(echo_suite):
    message = pa.SceneLabelMessage.FromString(SCENE_LABEL_ECHO)
    assert echo_suite.matches_scene_label(message, 1, "echo probe")


def test_scene_color_predicate_matches_the_real_echo(echo_suite):
    message = pa.SceneColorMessage.FromString(SCENE_COLOR_ECHO)
    assert echo_suite.matches_scene_color(message, 1, 4294911783)


def test_scene_predicates_still_discriminate_within_a_burst(echo_suite):
    """Matching on content is the point; loosening it to the type is not the fix.

    A scene edit made on the unit re-broadcasts all eight labels and colours, so
    a predicate that matched on message type alone would time whichever arrived
    first rather than the echo of this write.
    """
    other = pa.SceneLabelMessage(action=UPDATE, index=4, label="echo probe")
    assert not echo_suite.matches_scene_label(other, 1, "echo probe")

    stale = pa.SceneColorMessage(action=UPDATE, index=1, color=4282775650)
    assert not echo_suite.matches_scene_color(stale, 1, 4294911783)


def test_a_message_omitting_index_cannot_pass_for_scene_two(echo_suite):
    """Why dropping the presence guard is safe rather than merely necessary.

    Without presence, an absent ``index`` reads 0. Every scene these predicates
    target is nonzero, so a message that never carried the field fails the value
    check on its own - the guard was buying nothing even when it was reachable.
    """
    absent = pa.SceneLabelMessage(label="echo probe")
    assert absent.index == 0
    assert not echo_suite.matches_scene_label(absent, 1, "echo probe")


def test_scene_index_has_no_presence_which_is_why_the_guard_was_wrong():
    """Pin the protocol fact the predicates depend on.

    If a firmware or schema revision ever moves ``index`` into a ``oneof``, this
    fails and the predicates above can go back to asking about presence.
    """
    for message in (pa.SceneLabelMessage(index=1), pa.SceneColorMessage(index=1)):
        with pytest.raises(ValueError, match="does not have presence"):
            message.HasField("index")
        assert field_present(message, "index") is False

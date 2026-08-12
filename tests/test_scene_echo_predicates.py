"""The scene echo predicates and the probe's diagnosis, checked offline.

``tests/hardware/test_write_echo.py`` only runs with a unit attached, so a
predicate in it can stay quietly unsatisfiable for as long as nobody runs the
suite - and one did. Both scene predicates guarded on ``field_present(m,
"index")``, but ``SceneLabelMessage.index`` and ``SceneColorMessage.index`` are
bare proto3 scalars: the field cannot answer a presence question at all, so
``field_present`` said ``False`` for every message that has ever existed. The
predicates could not match, and the suite reported "produced no echo at all"
while the unit was echoing in about 2 ms.

That is the ``ColBypass.column`` mistake from docs/domain-model.md wearing a
different hat: a filter on a field the wire cannot distinguish from zero matches
nothing, and the absence reads as a fact about the device.

Two things are pinned here, both offline:

1. **The predicates.** Each is fed a real echo and a near-miss on every conjunct
   it depends on, so dropping any one of them fails a test rather than silently
   widening the match.
2. **The probe's diagnosis.** A predicate that raises and a device that says
   nothing both end as ``None``. Confusing those two is what cost the original
   investigation, so ``why_nothing_matched`` has to tell them apart.

What this file does NOT pin, since the docstrings used to imply otherwise: it
cannot catch a call site that stops using these predicates, and it cannot catch
the firmware changing what it sends. Both need a unit.
"""
import importlib.util
from pathlib import Path

import pytest

from pyquadcortex.protocol.client import field_present
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: Echoes captured 2026-08-11 from the connected unit at 0.40.0 by tapping the RX
#: dispatch across a scene-label and a scene-colour write. ``index`` rides the
#: wire in both - the ``1801`` pair, field 3 holding 1 - which is what makes the
#: old presence guard's verdict useless rather than merely conservative.
#:
#: Being honest about what these are worth: protobuf serialization here is
#: canonical, so these bytes are identical to what the constructors below
#: produce, and the capture cannot be told apart from a synthetic value by
#: inspection. They are a convenience that keeps the decode path in the test, not
#: independent evidence. The evidence that the device sends this shape is the
#: hardware suite, and only a unit can renew it.
SCENE_LABEL_ECHO = bytes.fromhex("08011801220a6563686f2070726f6265")
SCENE_COLOR_ECHO = bytes.fromhex("0801180120a7cefcff0f")

#: The action the device stamps on both echoes above.
UPDATE = pa.MessageAction.Enum.Value("UPDATE")

_HARDWARE_SUITE = Path(__file__).parent / "hardware" / "test_write_echo.py"


@pytest.fixture(scope="module")
def echo_suite():
    """The hardware module imported as a plain module, not collected.

    ``tests/hardware/conftest.py`` refuses to *collect* it without ``--hardware``;
    importing it is a different thing and has to stay safe, which it is because
    nothing at its module scope touches a device. Two module-scope dependencies
    ride on that and will fail loudly here if they move: ``QuadCortex.set_block``
    keeping a ``timeout`` parameter, and no module-scope ``import hid``.
    """
    spec = importlib.util.spec_from_file_location(
        "hardware_write_echo", _HARDWARE_SUITE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- the predicates -----------------------------------------------------------


def test_scene_label_predicate_matches_the_real_echo(echo_suite):
    message = pa.SceneLabelMessage.FromString(SCENE_LABEL_ECHO)
    assert echo_suite.matches_scene_label(
        message, echo_suite.SCENE_INDEX, echo_suite.SCENE_LABEL_PROBE)


def test_scene_color_predicate_matches_the_real_echo(echo_suite):
    message = pa.SceneColorMessage.FromString(SCENE_COLOR_ECHO)
    assert echo_suite.matches_scene_color(
        message, echo_suite.SCENE_INDEX, echo_suite.SCENE_COLOR_PROBE)


def test_the_label_predicate_rejects_a_near_miss_on_every_conjunct(echo_suite):
    """One case per thing the predicate checks, so none can be dropped silently.

    Without the wrong-index case, deleting the index comparison - the exact bug
    this file exists for - leaves the suite green.
    """
    index, label = echo_suite.SCENE_INDEX, echo_suite.SCENE_LABEL_PROBE
    matches = echo_suite.matches_scene_label

    assert not matches(pa.SceneLabelMessage(action=UPDATE, index=4, label=label),
                       index, label), "wrong scene index must not match"
    assert not matches(pa.SceneLabelMessage(action=UPDATE, index=index, label="other"),
                       index, label), "wrong label must not match"
    # A different message type that happens to carry a matching index. Dropping
    # the type guard turns this into an AttributeError rather than False, which
    # fails the test either way - that is the point.
    assert not matches(pa.SceneColorMessage(action=UPDATE, index=index, color=1),
                       index, label), "another message type must not match"


def test_the_color_predicate_rejects_a_near_miss_on_every_conjunct(echo_suite):
    index, color = echo_suite.SCENE_INDEX, echo_suite.SCENE_COLOR_PROBE
    matches = echo_suite.matches_scene_color

    assert not matches(pa.SceneColorMessage(action=UPDATE, index=4, color=color),
                       index, color), "wrong scene index must not match"
    assert not matches(pa.SceneColorMessage(action=UPDATE, index=index, color=42),
                       index, color), "wrong colour must not match"
    assert not matches(pa.SceneLabelMessage(action=UPDATE, index=index, label="x"),
                       index, color), "another message type must not match"


def test_a_message_omitting_index_cannot_pass_for_the_scene_written(echo_suite):
    """Why dropping the presence guard is safe rather than merely necessary.

    Without presence, an absent ``index`` reads 0. The scene written is nonzero,
    so a message that never carried the field fails the value check on its own.
    """
    absent = pa.SceneLabelMessage(label=echo_suite.SCENE_LABEL_PROBE)
    assert absent.index == 0
    assert not echo_suite.matches_scene_label(
        absent, echo_suite.SCENE_INDEX, echo_suite.SCENE_LABEL_PROBE)


def test_scene_index_zero_is_refused_rather_than_silently_matching(echo_suite):
    """The nonzero precondition is enforced, not just documented.

    At index 0 the predicate would match a message carrying neither field, which
    is the accidental match the global-settings guard exists to prevent. A future
    scene-1 test has to hit this assert instead of a false green.
    """
    for matches, value in ((echo_suite.matches_scene_label, ""),
                           (echo_suite.matches_scene_color, 0)):
        with pytest.raises(AssertionError, match="index 0"):
            matches(pa.SceneLabelMessage(), 0, value)


def test_the_write_constants_are_the_ones_the_predicates_are_tested_at(echo_suite):
    """Pin the coupling, so the shared constants cannot quietly diverge.

    These tests are only worth anything if they exercise the values the hardware
    call sites actually write.
    """
    assert echo_suite.SCENE_INDEX == 1
    assert pa.SceneLabelMessage.FromString(SCENE_LABEL_ECHO).label == (
        echo_suite.SCENE_LABEL_PROBE)
    assert pa.SceneColorMessage.FromString(SCENE_COLOR_ECHO).color == (
        echo_suite.SCENE_COLOR_PROBE)


def test_scene_index_has_no_presence_which_is_why_the_guard_was_wrong():
    """Pin the protocol fact the predicates depend on.

    If a schema revision ever moves ``index`` into a ``oneof``, this fails and the
    predicates can go back to asking about presence.
    """
    for message in (pa.SceneLabelMessage(index=1), pa.SceneColorMessage(index=1)):
        with pytest.raises(ValueError):
            message.HasField("index")
        assert field_present(message, "index") is False


# -- the probe's diagnosis ----------------------------------------------------


class _FakeTransport:
    def __init__(self):
        self._dispatch = lambda message, *a, **kw: None


class _FakeClient:
    def __init__(self):
        self._t = _FakeTransport()


def _probe(echo_suite):
    return echo_suite.EchoProbe(_FakeClient())


def test_a_raising_predicate_does_not_read_as_a_silent_device(echo_suite):
    """The failure this whole change exists to prevent, in one test.

    A predicate that raises is swallowed so the RX thread survives. It used to be
    swallowed without a trace, so it arrived as "produced no echo at all" - a
    claim about the guitar unit - and the investigation went looking at firmware.
    """
    probe = _probe(echo_suite)

    def raises(message):
        raise ValueError("Field SceneLabelMessage.index does not have presence.")

    ms = probe.measure(
        lambda: probe._tap(pa.SceneLabelMessage(index=1)), raises, timeout=0.05)

    assert ms is None
    why = probe.why_nothing_matched()
    assert "RAISED" in why, why
    assert "does not have presence" in why, why


def test_a_genuinely_quiet_device_reads_differently(echo_suite):
    """The other explanation for the same ``None``, and it must not look alike."""
    probe = _probe(echo_suite)

    ms = probe.measure(lambda: None, lambda message: True, timeout=0.05)

    assert ms is None
    why = probe.why_nothing_matched()
    assert "RAISED" not in why, why
    assert "0 seen" in why, why


def test_the_failure_message_does_not_blame_the_unit(echo_suite):
    """The assertion text itself, since that is what a human acts on.

    "produced no echo at all" sent the last investigation looking at firmware.
    What fires now has to carry the probe's account instead.
    """
    probe = _probe(echo_suite)
    probe.measure(lambda: None, lambda message: True, timeout=0.05)

    with pytest.raises(AssertionError) as caught:
        echo_suite._landed(None, "scene label", probe)

    message = str(caught.value)
    assert "produced no echo at all" not in message, message
    assert "0 seen" in message, message


def test_a_predicate_that_never_matches_reports_what_did_arrive(echo_suite):
    """The third explanation: traffic arrived, none of it satisfied the filter."""
    probe = _probe(echo_suite)

    def write():
        for _ in range(3):
            probe._tap(pa.SceneLabelMessage(index=7, label="something else"))

    ms = probe.measure(write, lambda message: False, timeout=0.05)

    assert ms is None
    assert "3 seen" in probe.why_nothing_matched()

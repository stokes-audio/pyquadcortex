"""How long the device takes to echo a host write, by write type.

The watcher that confirms a write landed needs a timeout, and until now that
timeout was set from two measurements: parameters (113-116 ms) and block
placement (290-420 ms). Every other write type was assumed to fit inside the
same envelope. This measures the rest so the assumption stops being one.

Each test snapshots what it touches and registers a restore before writing, so
the run is state-neutral whether it passes or fails (ADR-0005).
"""
import inspect
import threading
import time

import pytest

from pyquadcortex.protocol.client import QuadCortex, field_present
from pyquadcortex.protocol.enums import Input

#: Generous by design. The point is to MEASURE the echo, so a test that waits
#: three seconds and reports 400 ms is useful, while one that times out at the
#: value under test would only confirm its own assumption.
ECHO_TIMEOUT = 3.0

#: The library ships exactly one echo watcher - ``set_block``'s - and its timeout
#: is the thing these measurements exist to justify. Read from the signature
#: rather than copied, so lowering the default fails this suite instead of
#: quietly invalidating it.
WATCHER_TIMEOUT_MS = (
    inspect.signature(QuadCortex.set_block).parameters["timeout"].default * 1000.0)

#: How much of the watcher's budget an echo may use before this suite complains.
#: The slowest write type known - block placement at 290-420 ms - sits under a
#: tenth of the budget, so a fifth leaves room for firmware drift while still
#: failing long before a real write would start timing out.
WATCHER_BUDGET = WATCHER_TIMEOUT_MS * 0.2


class EchoProbe:
    """Times the gap between a host write and the device's echo of it."""

    def __init__(self, client):
        self._client = client
        self._lock = threading.Lock()
        self._want = None
        self._seen = None
        self._sent_at = None
        self._messages = 0
        self._errors = 0
        self._first_error = None
        self._inner = client._t._dispatch
        client._t._dispatch = self._tap

    def _tap(self, message, *args, **kwargs):
        with self._lock:
            if self._want is not None and self._seen is None:
                self._messages += 1
                try:
                    if self._want(message):
                        self._seen = (time.monotonic() - self._sent_at, message)
                except Exception as exc:             # noqa: BLE001 - the RX thread never dies
                    # Swallowed so the link survives, but COUNTED. Not recording
                    # it is what let a predicate that could never be true read as
                    # a silent device; see why_nothing_matched.
                    self._errors += 1
                    if self._first_error is None:
                        self._first_error = f"{type(exc).__name__}: {exc}"
        return self._inner(message, *args, **kwargs)

    def measure(self, write, matches, timeout=ECHO_TIMEOUT):
        """Run ``write()``, then wait for a broadcast satisfying ``matches``.

        Returns the latency in milliseconds, or ``None`` if nothing matched.
        ``None`` is not a finding on its own - see :meth:`why_nothing_matched`
        for the several things it can mean.
        """
        with self._lock:
            self._want, self._seen, self._sent_at = matches, None, time.monotonic()
            self._messages, self._errors, self._first_error = 0, 0, None
        write()
        deadline = time.monotonic() + timeout
        try:
            while time.monotonic() < deadline:
                with self._lock:
                    if self._seen is not None:
                        return self._seen[0] * 1000.0
                time.sleep(0.005)
            return None
        finally:
            with self._lock:
                self._want = None

    def why_nothing_matched(self):
        """What the probe actually saw, for a measurement that came back empty.

        A predicate that raises, a predicate that can never be true, and a device
        that stayed quiet all end as ``None``. This suite exists because two of
        those were confused once: a guard on a field with no presence was read as
        "the unit stopped echoing" while it was echoing in about 2 ms. So a miss
        reports what was observed and leaves the cause to the reader.
        """
        with self._lock:
            if self._errors:
                return (f"the predicate RAISED on {self._errors} of "
                        f"{self._messages} messages, so it never had a verdict "
                        f"to give (first: {self._first_error})")
            return (f"no message satisfied the predicate, out of "
                    f"{self._messages} seen during the window. Either the unit "
                    f"was silent or the predicate cannot match what it sends")

    def close(self):
        self._client._t._dispatch = self._inner


@pytest.fixture
def probe(qc):
    p = EchoProbe(qc)
    yield p
    p.close()


def _named(message, name):
    return type(message).__name__ == name


#: What the scene tests write. Shared with ``tests/test_scene_echo_predicates.py``
#: rather than repeated there: the offline test exists to exercise the predicates
#: at the values these call sites actually use, and two copies of a literal drift
#: apart silently, leaving the offline test green and covering nothing.
#:
#: The index must stay nonzero - see :func:`matches_scene_label`.
SCENE_INDEX = 1
SCENE_LABEL_PROBE = "echo probe"
SCENE_COLOR_PROBE = 4294911783


def matches_scene_label(message, index, label):
    """Whether ``message`` is the device's echo of a scene-label write.

    Deliberately no ``field_present`` guard on ``index``: it is a bare proto3
    scalar, so the field cannot answer a presence question at all and
    ``field_present`` says ``False`` for every message including the echo this is
    looking for. A guard here matched nothing and was read as "no echo at all"
    while the unit was echoing in about 2 ms. Contrast
    ``test_global_settings_echo_latency``, whose field sits in a ``oneof``, where
    the same guard is both valid and load-bearing.

    Dropping it is only safe while ``index`` is nonzero. An absent ``index``
    reads 0, so ``matches_scene_label(m, 0, "")`` would match a message that
    carried neither field - the accidental match the global-settings guard exists
    to prevent. That precondition is asserted rather than left to this paragraph.

    ``tests/test_scene_echo_predicates.py`` exercises this offline against the
    values the call sites use. That catches a predicate that cannot match; it
    does not catch a caller that stops using it, nor a firmware that stops
    sending what it matches on.
    """
    assert index, "scene index 0 is indistinguishable from an absent index"
    return (_named(message, "SceneLabelMessage")
            and message.index == index and message.label == label)


def matches_scene_color(message, index, color):
    """Whether ``message`` is the device's echo of a scene-colour write.

    Presence-free on ``index``, with the same reasoning and the same nonzero
    precondition as :func:`matches_scene_label`.
    """
    assert index, "scene index 0 is indistinguishable from an absent index"
    return (_named(message, "SceneColorMessage")
            and message.index == index and message.color == color)


def _landed(ms, what, probe):
    """Every measurement asserts the same two things, so say them once.

    Arrival alone is not the whole claim. These numbers are what the write
    watcher's timeout rests on, so a latency that has crept up toward that
    timeout has to fail here - otherwise the suite stays green while the figure
    it was written to defend quietly stops being true.

    A miss reports what the probe saw rather than blaming the unit. The old
    wording here was "produced no echo at all", which is a claim about the
    hardware, and it was wrong the one time it fired: the device was echoing and
    the predicate could not match.
    """
    assert ms is not None, (
        f"{what} write: nothing matched - {probe.why_nothing_matched()}")
    assert ms < WATCHER_BUDGET, (
        f"{what} echo took {ms:.1f} ms, past the {WATCHER_BUDGET:.0f} ms this "
        f"suite allows out of the watcher's {WATCHER_TIMEOUT_MS:.0f} ms. Either "
        f"the firmware got slower or the watcher's timeout got shorter; both "
        f"mean the documented latencies need remeasuring.")


def test_parameter_echo_latency_is_the_control(qc, probe, restores, record_property):
    """A known quantity, measured with the same harness as everything else.

    Parameter writes were measured at 113-116 ms by an earlier session. If this
    harness disagrees with that, the harness is wrong and every other number in
    this file is worthless - so it is asserted rather than merely recorded.
    """
    preset = qc.read_current_preset()
    column = next(c for c, m in enumerate(preset.chains[0].models) if m.hash)
    was = next(p.param_values[0].float_value
               for p in preset.chains[0].models[column].params if p.index == 0)
    restores("row 1 first block, parameter 0", lambda: qc.set_param(0, column, 0, was))

    target = 0.75 if abs(was - 0.75) > 0.05 else 0.25
    ms = probe.measure(
        lambda: qc.set_param(0, column, 0, target),
        lambda m: _named(m, "GridMessage") and any(
            mo.column == column and any(
                p.index == 0 and abs(p.param_values[0].float_value - target) < 1e-6
                for p in mo.params)
            for ch in m.preset.chains for mo in ch.models),
    )
    record_property("parameter_echo_ms", ms)
    _landed(ms, "parameter", probe)
    # The reference is the earlier session's 113-116 ms, taken by hand with a
    # different instrument; this harness reads the same write at 120-125 ms. The
    # few ms between them are the harness itself - the clock starts before the
    # USB write rather than after it - so the earlier figure stays the reference
    # and this one is expected to sit just above it.
    #
    # The floor is the load-bearing half. Everything this file measures lands
    # between 2 and 12 ms when the predicate matches the wrong message, so a
    # control that dropped into that band would be reporting the sidechain burst
    # and not the echo, and every other number here would be worthless.
    assert 50.0 < ms < 400.0, (
        f"parameter echo measured {ms:.1f} ms against a documented 113-116 ms "
        f"(120-125 ms through this harness). The harness is matching the wrong "
        f"message, so every other latency in this file is suspect.")


def test_bypass_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    row, column = 0, next(c for c, m in enumerate(preset.chains[0].models) if m.hash)
    was = qc.read_current_preset().bypass[row].colBypass
    entry = next((cb for cb in was if cb.column == column), None)

    # Only touch a block whose bypass state is already stored. The map is sparse,
    # so "no entry" and "entry set to false" read the same to a player and differ
    # on the wire; restoring an absent entry by writing the explicit default would
    # leave the preset holding a value it never held, and a state-neutral run
    # that is not quite neutral is worse than one that says it skipped.
    if entry is None:
        pytest.skip(
            f"row {row + 1} column {column + 1} has no stored bypass entry, so "
            f"this test cannot restore it exactly - load a preset whose first "
            f"block has been bypassed at least once")

    original = entry.sceneBypass[0].bypass
    restores("block bypass", lambda: qc.set_bypass(row, column, original))

    # Content-matched, not just "a GridMessage arrived": every structural edit
    # triggers a sidechain-bookkeeping burst of GridMessages that a loose
    # predicate happily matches, which would measure the burst and not the echo.
    ms = probe.measure(
        lambda: qc.set_bypass(row, column, not original),
        lambda m: _named(m, "GridMessage") and any(
            cb.column == column and any(
                sb.bypass == (not original) for sb in cb.sceneBypass)
            for b in m.preset.bypass for cb in b.colBypass),
    )
    record_property("bypass_echo_ms", ms)
    _landed(ms, "bypass", probe)


def test_routing_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    original = preset.chains[0].in_portid
    target = Input.RETURN_1 if original != Input.RETURN_1 else Input.INPUT_1
    restores("row 1 input port", lambda: qc.set_chain_input(0, original))

    ms = probe.measure(
        lambda: qc.set_chain_input(0, target),
        lambda m: _named(m, "GridMessage") and any(
            ch.in_portid == int(target) for ch in m.preset.chains),
    )
    record_property("routing_echo_ms", ms)
    _landed(ms, "routing", probe)


def test_scene_label_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    original = (preset.scene_labels[SCENE_INDEX]
                if len(preset.scene_labels) > SCENE_INDEX else "")
    restores("scene 2 label", lambda: qc.set_scene_label(SCENE_INDEX, original))

    # Write something the scene does not already hold. Every other test in this
    # file derives its target from the current value; these two used to write a
    # fixed constant, so a run that left the probe label in place - a killed
    # process, a failed restore - would make the next run a no-op write, and
    # whether a no-op echoes is unmeasured. That failure would arrive as "nothing
    # matched", which is exactly the message this suite already misread once.
    target = SCENE_LABEL_PROBE if original != SCENE_LABEL_PROBE else "echo probe 2"

    # Content-matched like the two above. A scene edit made ON THE UNIT
    # re-broadcasts all eight labels, so "a SceneLabelMessage arrived" would time
    # whichever of that burst landed first rather than the echo of this write. A
    # host write was observed to be narrower - two identical messages for the
    # written index alone - but matching on content costs nothing and keeps one
    # predicate honest for both cases.
    ms = probe.measure(
        lambda: qc.set_scene_label(SCENE_INDEX, target),
        lambda m: matches_scene_label(m, SCENE_INDEX, target),
    )
    record_property("scene_label_echo_ms", ms)
    _landed(ms, "scene label", probe)


def test_scene_color_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    original = (preset.scene_colors[SCENE_INDEX]
                if len(preset.scene_colors) > SCENE_INDEX else 0)
    restores("scene 2 colour", lambda: qc.set_scene_color(SCENE_INDEX, original))

    # Never a no-op write, for the reason given in the label test above.
    target = SCENE_COLOR_PROBE if original != SCENE_COLOR_PROBE else 4278233600

    ms = probe.measure(
        lambda: qc.set_scene_color(SCENE_INDEX, target),
        lambda m: matches_scene_color(m, SCENE_INDEX, target),
    )
    record_property("scene_color_echo_ms", ms)
    _landed(ms, "scene colour", probe)


def test_global_settings_echo_latency(qc, probe, restores, record_property):
    original = qc.settings().stomp_mode_auto_assign
    restores("stomp_mode_auto_assign",
             lambda: qc.update_settings(stomp_mode_auto_assign=original))

    # Presence matters more than value here: this is a boolean, so a message that
    # simply does not carry the field reads as False and would match a flip to
    # False by accident. GeneralSettings also arrives unsolicited on standby and
    # wake, which is exactly the sort of traffic a type-only match would time.
    ms = probe.measure(
        lambda: qc.update_settings(stomp_mode_auto_assign=not original),
        lambda m: (_named(m, "GeneralSettingsMessage")
                   and field_present(m, "stomp_mode_auto_assign")
                   and m.stomp_mode_auto_assign == (not original)),
    )
    record_property("global_settings_echo_ms", ms)
    _landed(ms, "global settings", probe)

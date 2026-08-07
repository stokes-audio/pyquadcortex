"""How long the device takes to echo a host write, by write type.

The watcher that confirms a write landed needs a timeout, and until now that
timeout was set from two measurements: parameters (113-116 ms) and block
placement (290-420 ms). Every other write type was assumed to fit inside the
same envelope. This measures the rest so the assumption stops being one.

Each test snapshots what it touches and registers a restore before writing, so
the run is state-neutral whether it passes or fails (ADR-0005).
"""
import threading
import time

import pytest

from pyquadcortex.enums import Input

#: Generous by design. The point is to MEASURE the echo, so a test that waits
#: three seconds and reports 400 ms is useful, while one that times out at the
#: value under test would only confirm its own assumption.
ECHO_TIMEOUT = 3.0


class EchoProbe:
    """Times the gap between a host write and the device's echo of it."""

    def __init__(self, client):
        self._client = client
        self._lock = threading.Lock()
        self._want = None
        self._seen = None
        self._sent_at = None
        self._inner = client._t._dispatch
        client._t._dispatch = self._tap

    def _tap(self, message, *args, **kwargs):
        with self._lock:
            if self._want is not None and self._seen is None:
                try:
                    if self._want(message):
                        self._seen = (time.monotonic() - self._sent_at, message)
                except Exception:                    # noqa: BLE001 - a bad predicate must not kill the link
                    pass
        return self._inner(message, *args, **kwargs)

    def measure(self, write, matches, timeout=ECHO_TIMEOUT):
        """Run ``write()``, then wait for a broadcast satisfying ``matches``.

        Returns the latency in milliseconds, or ``None`` if nothing matched -
        which is itself a result worth recording, since a write type that never
        echoes cannot be confirmed by a watcher at all.
        """
        with self._lock:
            self._want, self._seen, self._sent_at = matches, None, time.monotonic()
        write()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                if self._seen is not None:
                    return self._seen[0] * 1000.0
            time.sleep(0.005)
        return None

    def close(self):
        self._client._t._dispatch = self._inner


@pytest.fixture
def probe(qc):
    p = EchoProbe(qc)
    yield p
    p.close()


def _named(message, name):
    return type(message).__name__ == name


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
    assert ms is not None, "parameter write produced no echo - harness is broken"
    assert 50.0 < ms < 400.0, (
        f"parameter echo measured {ms:.1f} ms, but the documented figure is "
        f"113-116 ms. The harness is matching the wrong message, so every other "
        f"latency in this file is suspect.")


def test_bypass_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    row, column = 0, next(c for c, m in enumerate(preset.chains[0].models) if m.hash)
    was = qc.read_current_preset().bypass[row].colBypass
    original = next((cb.sceneBypass[0].bypass for cb in was if cb.column == column), False)
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
    assert ms is not None, "bypass write produced no echo at all"


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
    assert ms is not None, "routing write produced no echo at all"


def test_scene_label_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    original = preset.scene_labels[1] if len(preset.scene_labels) > 1 else ""
    restores("scene 2 label", lambda: qc.set_scene_label(1, original))

    ms = probe.measure(
        lambda: qc.set_scene_label(1, "echo probe"),
        lambda m: _named(m, "SceneLabelMessage"),
    )
    record_property("scene_label_echo_ms", ms)
    assert ms is not None, "scene label write produced no echo at all"


def test_scene_color_echo_latency(qc, probe, restores, record_property):
    preset = qc.read_current_preset()
    original = preset.scene_colors[1] if len(preset.scene_colors) > 1 else 0
    restores("scene 2 colour", lambda: qc.set_scene_color(1, original))

    ms = probe.measure(
        lambda: qc.set_scene_color(1, 4294911783),
        lambda m: _named(m, "SceneColorMessage"),
    )
    record_property("scene_color_echo_ms", ms)
    assert ms is not None, "scene colour write produced no echo at all"


def test_global_settings_echo_latency(qc, probe, restores, record_property):
    original = qc.settings().stomp_mode_auto_assign
    restores("stomp_mode_auto_assign",
             lambda: qc.update_settings(stomp_mode_auto_assign=original))

    ms = probe.measure(
        lambda: qc.update_settings(stomp_mode_auto_assign=not original),
        lambda m: _named(m, "GeneralSettingsMessage"),
    )
    record_property("global_settings_echo_ms", ms)
    assert ms is not None, "global settings write produced no echo at all"

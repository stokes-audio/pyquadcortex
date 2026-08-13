"""The state-snapshot instrument, checked offline.

``tests/hardware/test_tempo_mode.py`` only runs with a unit attached, and the
question it asks - where the unit keeps TEMPO MODE - has already been answered
wrongly once by an instrument nobody had checked. Three earlier tests reported
"MODE is not on the wire"; what they had measured was that it is never
broadcast, and one of them silently dropped 27 of the device's 72 message types
while doing it.

So the failure to guard against here is not "the device says nothing". It is
**the snapshot cannot see it even when the device does say it**, which reads
identically from the transcript. Each test below feeds the describer a message
carrying the thing it must not miss, and fails if the snapshot comes back empty:

* a field the recovered schema does not know at all - the likeliest hiding place
  in ``GeneralSettings``, whose 39 field numbers have no gaps;
* a presence-tracked field that is set to its zero value, which is the whole
  ``oneof`` distinction the model rests on (CLAUDE.md);
* a value that appears in only one of ``GlobalTempo``'s two alternating shapes.

What this file cannot do: prove the device sends any of it. Only a unit can.
"""
import importlib.util
from pathlib import Path

import pytest

from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.proto import Preset_pb2 as preset


@pytest.fixture(scope="module")
def snapshot():
    """The hardware suite's helper, imported as a plain module, not collected.

    ``tests/hardware/conftest.py`` refuses to COLLECT that directory without
    ``--hardware``; importing one module out of it is a different thing, and safe
    because nothing here touches a device.
    """
    path = Path(__file__).parent / "hardware" / "state_snapshot.py"
    spec = importlib.util.spec_from_file_location("qc_state_snapshot", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# -- describe: what the snapshot must not miss --------------------------------


def test_a_field_number_the_schema_does_not_know_is_recorded(snapshot):
    """The one that decides H2.

    ``GeneralSettingsMessage`` uses field numbers 1-39 with no gaps, so a MODE
    field in it is a number this schema - recovered from one Cortex Control
    build - has never seen. protobuf keeps such a field but decodes it to
    nothing, so a describer reading only named fields would report an empty
    difference and it would read as "the unit does not answer".
    """
    message = pa.GeneralSettingsMessage()
    message.MergeFromString(bytes([0xF8, 0x06, 0x07]))   # field 111, varint, 7

    described = snapshot.describe(message)

    assert described == {"<UNKNOWN field 111 wire 0>": 7}


def test_a_presence_tracked_field_set_to_zero_is_recorded(snapshot):
    """Absent and present-but-zero are different answers, and must stay so.

    Most of this schema sits in synthetic ``oneof``s precisely so the device can
    say "false" as distinct from saying nothing. A GLOBAL/PRESET switch is a
    two-valued thing, so one of its two values is very likely the zero - and a
    describer that dropped zeros would see the switch move and report silence.
    """
    with_zero = pa.GeneralSettingsMessage(swap_tempo_tuner_access=False)
    without = pa.GeneralSettingsMessage()

    assert snapshot.describe(with_zero) == {"swap_tempo_tuner_access": False}
    assert snapshot.describe(without) == {}


def test_repeated_params_are_recorded_by_position(snapshot):
    """``tempoProgramData`` and ``GlobalTempo.params`` are both lists of ``Param``.

    In the stored preset all 24 arrive with ``index`` ABSENT, so position is the
    index (``protocol.md``). Flattening them positionally is what makes "param 7
    differs" a readable line in the diff.
    """
    message = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    for value in (120.0, 4.0):
        message.params.add().param_values.add(float_value=value)

    described = snapshot.describe(message)

    assert described["params.<count>"] == 2
    assert described["params[0].param_values[0].float_value"] == 120.0
    assert described["params[1].param_values[0].float_value"] == 4.0
    assert described["action"] == "1:UPDATE"     # enums read as number:NAME


def test_the_grid_is_skipped_but_says_so(snapshot):
    """A preset dump minus ``chains`` is readable; minus a note it is a lie."""
    binary = preset.BinaryPreset(name="test", tempo=120)
    binary.chains.add()

    described = snapshot.preset_fields(binary)

    assert described["tempo"] == 120
    assert described["chains.<skipped, len 1>"] is True
    assert not any(path.startswith("chains.models") for path in described)


def test_an_absent_preset_tempo_is_absent_not_zero(snapshot):
    """H1 rests on this. ``BinaryPreset.tempo`` is field 10, presence-tracked."""
    assert "tempo" not in snapshot.preset_fields(preset.BinaryPreset(name="x"))


# -- the recorder: the producer behind every snapshot on disk ------------------


class _FakeListenerClient:
    """The listener half of ``QuadCortex``, and nothing else.

    Mirrors the contract ``Transport.add_listener`` documents: registration
    returns a zero-argument remover, listeners are notified in order, and a
    listener consumes nothing. Small enough to be obviously faithful, which is
    the point - the real one needs a device.
    """

    def __init__(self):
        self.listeners = []

    def add_listener(self, listener):
        self.listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        try:
            self.listeners.remove(listener)
        except ValueError:
            return False
        return True

    def deliver(self, message):
        for listener in list(self.listeners):
            listener(message)


def test_the_tap_keeps_two_shapes_apart_and_counts_arrivals(snapshot):
    """``_Tap`` is the producer, and nothing else in the suite touched it.

    The diff tests below hand-build the snapshot shape - ``shapes[name] ->
    [{count, fields}]`` - so they encode an assumption about this class and could
    all stay green while it emitted something else entirely. That is the same
    "instrument nobody had checked" failure the rest of this file guards against,
    one layer down.

    What must hold: the two ``GlobalTempo`` shapes stay DISTINCT (they are keyed
    by a fingerprint of their fields), repeat arrivals are COUNTED rather than
    collapsed, noisy types are censused instead of valued, and the real dispatch
    still runs so the transport keeps working while the tap is installed.
    """
    client = _FakeListenerClient()
    tap = snapshot._Tap(client)

    clock = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    clock.metronome_status.current_beat = 3
    params = pa.GlobalTempoMessage(action=pa.MessageAction.UPDATE)
    params.params.add().param_values.add(float_value=1.0)

    client.deliver(clock)
    client.deliver(params)
    client.deliver(clock)
    client.deliver(pa.CPULoadMessage())
    tap.stop()

    shapes = tap.shapes["GlobalTempoMessage"]
    assert len(shapes) == 2, "the clock and params shapes must not merge"
    assert sorted(s["count"] for s in shapes.values()) == [1, 2], "arrivals counted"
    assert "CPULoadMessage" in tap.census, "a noisy type is censused, not valued"
    assert "CPULoadMessage" not in tap.shapes
    assert client.listeners == [], "stop() unsubscribed"
    assert not tap.errors

    client.deliver(params)                       # nothing arrives after stop()
    assert sorted(s["count"] for s in shapes.values()) == [1, 2]


def test_the_tap_survives_a_message_it_cannot_describe(snapshot):
    """CLAUDE.md: the RX thread never dies. A counted error, not a lost link.

    The class comment says a ``describe()`` that raises would otherwise look
    exactly like that type never arriving - which is the failure this whole
    investigation exists to undo - so the swallow-and-count has to be real, and
    the message has to keep flowing.
    """
    client = _FakeListenerClient()
    tap = snapshot._Tap(client)
    original = snapshot.describe
    snapshot.describe = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    try:
        client.deliver(pa.GlobalTempoMessage())      # must not propagate
    finally:
        snapshot.describe = original
        tap.stop()

    assert len(tap.errors) == 1 and "boom" in tap.errors[0], "the failure was RECORDED"
    assert not tap.shapes, "and nothing half-recorded was kept"


# -- diff: what the comparison must not miss ----------------------------------


def _snap(label, shapes, preset_fields=None, census=None):
    """A snapshot in the on-disk shape, without needing a device."""
    return {
        "label": label,
        "window_seconds": 0,
        "preset": preset_fields or {},
        "shapes": {name: [{"count": 1, "fields": fields} for fields in shape_list]
                   for name, shape_list in shapes.items()},
        "census": census or {},
        "tap_errors": [],
    }


def test_a_field_appearing_in_one_mode_only_is_signal(snapshot):
    before = _snap("global", {"GeneralSettingsMessage": [{}]})
    after = _snap("preset", {"GeneralSettingsMessage": [{"<UNKNOWN field 40 wire 0>": 1}]})

    signal, noise = snapshot.diff(before, after)

    assert signal == [
        "GeneralSettingsMessage.<UNKNOWN field 40 wire 0>: <absent> -> 1"]
    assert noise == []


def test_a_value_present_in_only_one_of_two_shapes_still_diffs(snapshot):
    """``GlobalTempo`` alternates two shapes, one push each.

    A comparison that sampled one message per type would compare a clock reply
    against a params reply and call the difference real. Values are collected as
    a SET per path across the whole window, so the clock-only shape contributes
    nothing to the params path and vice versa.
    """
    clock = {"metronome_status.<present>": True, "metronome_status.current_beat": 1}
    before = _snap("global", {"GlobalTempoMessage": [
        clock, {"params.<count>": 25, "params[7].param_values[0].float_value": 0.0}]})
    after = _snap("preset", {"GlobalTempoMessage": [
        clock, {"params.<count>": 25, "params[7].param_values[0].float_value": 1.0}]})

    signal, _ = snapshot.diff(before, after)

    assert signal == [
        "GlobalTempoMessage.params[7].param_values[0].float_value: 0.0 -> 1.0"]


def test_the_running_clock_is_named_noise_not_dropped(snapshot):
    """It moves every beat by design, and it is still reported.

    Filtering is how the previous answer went wrong, so a path that looks noisy
    is printed under its own heading rather than discarded.
    """
    before = _snap("global", {"GlobalTempoMessage": [{"metronome_status.current_beat": 1}]})
    after = _snap("preset", {"GlobalTempoMessage": [{"metronome_status.current_beat": 3}]})

    signal, noise = snapshot.diff(before, after)

    assert signal == []
    assert noise == ["GlobalTempoMessage.metronome_status.current_beat: 1 -> 3"]


@pytest.mark.parametrize("path", [
    "GlobalEQMessage.parameters[0].gain",       # "meter" is inside "parameters"
    "SetlistPositionMessage.position",          # the preset-changed confound
    "TunerMessage.enable_meter",                # a setting, not a moving value
])
def test_a_real_field_is_not_swallowed_by_a_noise_substring(snapshot, path):
    """Noise is matched on whole path SEGMENTS, never as a substring.

    The first version matched substrings, and the collisions were exactly the
    wrong ones: ``GlobalEQ`` is a type this harness READs and its params field is
    literally ``parameters``, and ``SetlistPosition.position`` is the field that
    would reveal the operator changed preset between two captures - the one
    confound that invalidates the whole comparison. Nothing was dropped, but
    "known-noisy, shown for completeness" is an invitation to skip.
    """
    assert not snapshot._is_noise(path)


def test_a_preset_field_that_moves_is_signal(snapshot):
    """H1: presence itself may be the discriminator."""
    before = _snap("global", {}, preset_fields={"name": "x"})
    after = _snap("preset", {}, preset_fields={"name": "x", "tempo": 120})

    signal, _ = snapshot.diff(before, after)

    assert signal == ["preset.tempo: <absent> -> 120"]


def test_a_type_missing_from_one_capture_is_not_reported_as_a_difference(snapshot):
    """Coverage varies run to run, and the diff must not dress that as a finding.

    Measured on hardware: two captures of the same window length saw 23 and 12
    message types, because the connect handshake's reply burst lands lazily (the
    File enumeration takes 10-25 s) and a fixed window catches a different tail
    each time. Rendering an uncaptured type field-by-field as "<absent> -> value"
    manufactures exactly the kind of discovery this harness exists to prevent.
    """
    before = _snap("a", {"GlobalTempoMessage": [{"params[1]": 0.0}],
                         "FileMessage": [{"folder.files[0].name": "Preset"}]})
    after = _snap("b", {"GlobalTempoMessage": [{"params[1]": 1.0}]})

    signal, noise = snapshot.diff(before, after)

    assert signal == ["GlobalTempoMessage.params[1]: 0.0 -> 1.0"], (
        "the real difference must still be the only signal")
    assert len(noise) == 1 and noise[0].startswith("FileMessage: NOT CAPTURED")


def test_two_identical_snapshots_diff_to_nothing(snapshot):
    """The negative result has to be readable as one, or it is worthless."""
    fields = {"GeneralSettingsMessage": [{"midi_channel": 1}]}
    signal, noise = snapshot.diff(_snap("a", fields), _snap("b", fields))

    assert (signal, noise) == ([], [])

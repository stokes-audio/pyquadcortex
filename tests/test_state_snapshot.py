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


def test_a_preset_field_that_moves_is_signal(snapshot):
    """H1: presence itself may be the discriminator."""
    before = _snap("global", {}, preset_fields={"name": "x"})
    after = _snap("preset", {}, preset_fields={"name": "x", "tempo": 120})

    signal, _ = snapshot.diff(before, after)

    assert signal == ["preset.tempo: <absent> -> 120"]


def test_two_identical_snapshots_diff_to_nothing(snapshot):
    """The negative result has to be readable as one, or it is worthless."""
    fields = {"GeneralSettingsMessage": [{"midi_channel": 1}]}
    signal, noise = snapshot.diff(_snap("a", fields), _snap("b", fields))

    assert (signal, noise) == ([], [])

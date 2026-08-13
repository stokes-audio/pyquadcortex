"""Snapshot the unit's readable state, so two snapshots can be diffed.

Written for the TEMPO MODE investigation, and deliberately general: the method
is *diff, do not hunt*. Every earlier attempt at MODE looked for a field it
expected and concluded "not on the wire" when that field did not appear. This
records EVERY set field of every message the device answers with - names it has
a schema for and field numbers it does not - so the question becomes "what
differs between the two menu positions" rather than "is it the field I guessed".

Three things here exist because of specific past mistakes:

* **Unknown field numbers are recorded.** The schema is recovered from Cortex
  Control, so a field the firmware sends and that build never had would decode
  as nothing at all. ``GeneralSettingsMessage`` uses field numbers 1-39 with no
  gaps, so if MODE rides there it rides in a number the schema does not know.
* **Values are collected as a SET per field path, over a window**, not sampled
  once. ``GlobalTempo`` alternates two shapes (``protocol.md`` section 8), so a
  single reply proves nothing about the other shape.
* **Nothing is filtered out.** Fields known to move on their own - the running
  clock, meters, request ids - are LABELLED as noise in the diff, not dropped.
  A filter that hides the answer is exactly how this question got its previous
  wrong answer.
"""
import json
import time

from google.protobuf.unknown_fields import UnknownFieldSet

#: State types to READ. Each is a subscription the device answers with its
#: current value. ``Updater`` is deliberately absent (CLAUDE.md: never send
#: anything to the firmware surface), as are the cloud types.
READ_TYPES = (
    "GlobalTempo",          # H3: the only type seen carrying global tempo params
    "GeneralSettings",      # H2: the global settings bag
    "Mode", "IOSettings", "MasterVolume", "GlobalEQ", "ShowGigView",
    "Tuner", "Looper", "SetlistPosition", "Scene", "PresetDirty",
)

#: Arrives constantly and carries per-sample values. Recorded as a field-path
#: census - has a field appeared or gone? - rather than as values, which would
#: bury the snapshot in numbers that differ every time by design.
NOISY_TYPES = frozenset({
    "GridModelMeterMessage", "IOMeterMessage", "CPULoadMessage",
    "ModuleStatsMessage", "KeepAliveMessage", "SystemTimeSyncMessage",
    "ModelRepoMessage",
})

#: Handled separately by :func:`preset_fields` - a full grid dump is enormous
#: and the interesting part of it is small.
PRESET_TYPES = frozenset({"RecallPresetMessage", "GridMessage"})

#: Substrings marking a path that moves on its own. NOT a filter: the diff
#: prints these under their own heading, below everything else.
NOISE_PATHS = (
    "request_id", "current_beat", "current_bar", "current_tick",
    "available_disk_space", "cpu", "meter", "timestamp", "session_id",
    "elapsed", "position",
)


def _scalar(field, value):
    """One leaf value, rendered so a diff of two snapshots reads plainly."""
    if field.type == field.TYPE_ENUM:
        entry = field.enum_type.values_by_number.get(value)
        return f"{value}:{entry.name}" if entry is not None else value
    if field.type == field.TYPE_BYTES:
        return value.hex()
    return value


def _is_map(field):
    return (field.message_type is not None
            and field.message_type.GetOptions().map_entry)


def describe(message, prefix="", skip=()):
    """Every SET field of ``message``, flattened to ``path -> value``.

    ``ListFields`` is the presence-correct reading of this schema: a field in a
    synthetic ``oneof`` appears only when the device actually sent it, which is
    the distinction the whole model rests on (CLAUDE.md). A field that is absent
    is absent from the result, so a diff shows it as a key appearing rather than
    as a zero that could mean either thing.

    ``skip`` names top-level fields to leave out - ``chains`` on a preset, which
    is most of the payload and none of the question.
    """
    out = {}
    for field, value in message.ListFields():
        if not prefix and field.name in skip:
            out[f"{field.name}.<skipped, len {len(value)}>"] = True
            continue
        name = f"{prefix}{field.name}"
        if _is_map(field):
            for key in sorted(value):
                item = value[key]
                if hasattr(item, "ListFields"):
                    out.update(describe(item, f"{name}[{key!r}]."))
                else:
                    out[f"{name}[{key!r}]"] = item
        elif field.is_repeated:
            if field.message_type is not None:
                out[f"{name}.<count>"] = len(value)
                for index, item in enumerate(value):
                    out.update(describe(item, f"{name}[{index}]."))
            else:
                out[name] = [_scalar(field, v) for v in value]
        elif field.message_type is not None:
            # Recorded even when empty: a present-but-empty submessage is a
            # real answer, and without this it would vanish from the diff.
            out[f"{name}.<present>"] = True
            out.update(describe(value, f"{name}."))
        else:
            out[name] = _scalar(field, value)
    for unknown in UnknownFieldSet(message):
        data = unknown.data
        out[f"{prefix}<UNKNOWN field {unknown.field_number} wire {unknown.wire_type}>"] = (
            data.hex() if isinstance(data, bytes) else data)
    return out


def preset_fields(binary_preset):
    """The preset's non-grid fields, which is where H1 lives.

    ``chains`` is skipped: it is nearly the whole payload and the tempo question
    is not in it. What is kept is ``tempo`` (field 10, presence-tracked),
    ``tempoProgramData`` (field 19, the ``TempoControl`` block), and every other
    preset-level field, so a difference anywhere outside the grid shows up
    whether or not it was the one being looked for.
    """
    return describe(binary_preset, skip=("chains",))


class _Tap:
    """Records every decoded inbound message for the life of the capture."""

    def __init__(self, transport):
        self._transport = transport
        self._inner = transport._dispatch
        self.shapes = {}        # type name -> {fingerprint: {"count", "fields"}}
        self.census = {}        # type name -> {"count", "paths"}
        self.errors = []
        transport._dispatch = self._tap

    def _tap(self, message, *args, **kwargs):
        try:
            self._record(message)
        except Exception as exc:                # noqa: BLE001 - the RX thread never dies
            # Counted rather than swallowed. A describe() that raises on one
            # message type would otherwise read as that type never arriving,
            # which is the exact failure mode this whole investigation exists
            # to undo.
            self.errors.append(f"{type(message).__name__}: {type(exc).__name__}: {exc}")
        return self._inner(message, *args, **kwargs)

    def _record(self, message):
        name = type(message).__name__
        if name in PRESET_TYPES:
            return                              # captured separately, not here
        fields = describe(message)
        if name in NOISY_TYPES:
            entry = self.census.setdefault(name, {"count": 0, "paths": set()})
            entry["count"] += 1
            entry["paths"].update(fields)
            return
        entry = self.shapes.setdefault(name, {})
        fingerprint = json.dumps(fields, sort_keys=True, default=repr)
        shape = entry.setdefault(fingerprint, {"count": 0, "fields": fields})
        shape["count"] += 1

    def stop(self):
        self._transport._dispatch = self._inner


def capture(qc, label, window=14.0, spacing=0.15):
    """READ everything readable, watch for ``window`` seconds, return a snapshot.

    Read-only: every message sent is a ``READ``. Nothing here writes to the
    unit, so the run needs no restore (ADR-0005 is satisfied trivially).

    ``window`` has to span several beats, because ``GlobalTempo`` alternates its
    two shapes one per push and only one of them has ever been seen carrying
    parameters. At 40 bpm a pair arrives every 1.5 s, so 14 s is roughly nine
    pairs at the slowest tempo the unit offers.
    """
    from pyquadcortex.protocol import registry
    from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

    tap = _Tap(qc._t)
    try:
        for name in READ_TYPES:
            cls = registry.class_for(pa.CortexMessageType.Enum.Value(name))
            qc._t.send(cls(action=pa.MessageAction.READ))
            time.sleep(spacing)
        time.sleep(window)
        preset = qc.read_current_preset()
        fields = preset_fields(preset)
    finally:
        tap.stop()

    return {
        "label": label,
        "window_seconds": window,
        "preset": fields,
        "shapes": {name: sorted(shapes.values(), key=lambda s: -s["count"])
                   for name, shapes in sorted(tap.shapes.items())},
        "census": {name: {"count": entry["count"], "paths": sorted(entry["paths"])}
                   for name, entry in sorted(tap.census.items())},
        "tap_errors": tap.errors,
    }


def _values_by_path(snapshot):
    """``type -> path -> sorted set of every value seen for it in the window``.

    Per-path value SETS, not one sample: the answer may be a field that takes
    one value in one shape of a message and another in the other shape.
    """
    out = {}
    for name, shapes in snapshot["shapes"].items():
        paths = out.setdefault(name, {})
        for shape in shapes:
            for path, value in shape["fields"].items():
                paths.setdefault(path, set()).add(json.dumps(value, default=repr))
    return out


def _is_noise(path):
    lowered = path.lower()
    return any(marker in lowered for marker in NOISE_PATHS)


def _compare(name, before, after, signal, noise):
    for path in sorted(set(before) | set(after)):
        left = sorted(before.get(path, ()))
        right = sorted(after.get(path, ()))
        if left == right:
            continue
        line = f"{name}.{path}: {_render(left)} -> {_render(right)}"
        (noise if _is_noise(path) else signal).append(line)


def _render(values):
    if not values:
        return "<absent>"
    return values[0] if len(values) == 1 else "{" + ", ".join(values) + "}"


def diff(before, after):
    """What moved between two snapshots, signal first and noise named.

    Returns ``(signal, noise)``: two lists of lines. ``noise`` holds paths whose
    names say they move on their own - the running clock, meters, request ids.
    They are reported, not discarded, because a filter is how a previous answer
    to this question went wrong.
    """
    signal, noise = [], []

    left, right = _values_by_path(before), _values_by_path(after)
    for name in sorted(set(left) | set(right)):
        _compare(name, left.get(name, {}), right.get(name, {}), signal, noise)

    _compare("preset",
             {p: {json.dumps(v, default=repr)} for p, v in before["preset"].items()},
             {p: {json.dumps(v, default=repr)} for p, v in after["preset"].items()},
             signal, noise)

    for name in sorted(set(before["census"]) | set(after["census"])):
        was = set(before["census"].get(name, {}).get("paths", ()))
        now = set(after["census"].get(name, {}).get("paths", ()))
        for path in sorted(was ^ now):
            line = f"census {name}.{path}: {'gone' if path in was else 'appeared'}"
            (noise if _is_noise(path) else signal).append(line)

    return signal, noise

"""What the model tracks, and how each part of it stays current.

This is ``docs/domain-model.md`` section 9's table written as data. Each entry
names one part of the cache and says three things about it: which message types
carry it, which of their fields the model keeps, and how to ask the unit for it
when we have nothing or have stopped trusting what we have.

**Why the fields are listed one by one.** A push is often partial - the standby
announcement carries only ``power_option`` - so applying it means merging the
fields it names and leaving the rest alone. The failure that makes a cache worse
than no cache is applying the half of a message we understand and dropping the
rest, because the result is confidently wrong rather than obviously stale. So the
rule here is per FIELD: a field we keep is applied, a field we do not keep makes
the whole entry untrusted and the next read goes to the unit. There is no third
option and nothing is silently ignored, which is why nothing here is a category
called "harmless".

That is deliberately conservative. A push that mentions one field we do not keep
costs one read, whether or not that field could really have made our copy wrong.
The alternative - declaring, field by field, which changes cannot affect what we
hold - is a judgement call per field with no way to check it, and the whole point
of section 9 is that the model does not guess.

**Adding an entry.** Write it here, give it a read, list the fields, and add its
tests to ``tests/test_state.py``. The structural tests in that file hold every
entry to the same standard: a field is only read without a presence check if the
schema really gives it no presence, and every field named has to exist.
"""

import dataclasses
import typing

from google.protobuf.message import Message

from pyquadcortex import protocol
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: Fields the per-field check skips, on every entry. They are on nearly every
#: message in this schema, and treating them as unkept state would mark every
#: entry for re-reading on every push - the thrash section 9 exists to avoid.
#: ``tests/test_state.py`` checks both are really on every feeding type, so the
#: skip cannot quietly forgive a field that never arrives.
#:
#: ``request_id`` is the transport's, always. **``action`` is not always.** It
#: says nothing about state on the plans that merge field by field, which is why
#: it is skipped globally - but on ``Grid`` it is load-bearing state: an
#: ``UPDATE`` carrying ``hash: 0`` is transmitted and ignored, while the same
#: payload with ``action: DELETE`` removes the block
#: (``QuadCortex.remove_block``). So the ``Grid`` feed - which ``PRESET`` carries
#: - cannot inherit this skip: two pushes with identical payloads and opposite
#: meanings would apply identically and mark nothing. That feed makes its own
#: decision about ``action`` (:data:`_GRID_MOVED`) rather than widening this
#: set, and see ADR-0011.
#:
#: The cache's arrival count leans on this too. A message carrying nothing but
#: these two said nothing, so it is not counted as having landed during a read
#: (``_Slot.witnessed`` in ``device/state.py``). An entry whose meaning lives in
#: ``action`` therefore has to declare ``invalidates`` - as ``Grid`` does - or it
#: would be both unapplied and uncounted.
SCAFFOLDING = frozenset({"action", "request_id"})


@dataclasses.dataclass(frozen=True)
class FieldPlan:
    """What one message type carries for one entry.

    Args:
        kept: fields the model holds, applied when the message says so. Each has
            field presence in the schema, so "absent" is a fact rather than a
            guess and the model can tell "not mentioned" from "set to zero".
        no_presence: fields the wire cannot report as absent, applied on every
            message of this type. Proto3 gives a plain scalar no presence, so its
            default value and "unset" are the same bytes and there is nothing to
            check - which means each of these needs recorded evidence for what
            the default MEANS, on the entry that declares it. Empty for most.
        invalidates: every message of this type makes the entry untrusted,
            whatever it carries, and the next read goes to the unit. For a type
            the model does not merge - and for one the per-field check CANNOT
            see. ``Grid`` carries its meaning in ``action``, which has no
            presence and is skipped globally, so an ``UPDATE`` and a ``DELETE``
            with the same payload look identical to it; ``SceneLabel`` gives
            ``index`` and ``label`` no presence either, so renaming scene A to a
            blank label sets nothing at all in ``ListFields()``. An entry fed by
            either has to decide for itself, and this flag is that decision
            written down. It does not widen :data:`SCAFFOLDING`, and it is set
            per entry and per message type rather than globally.
    """

    kept: frozenset = frozenset()
    no_presence: frozenset = frozenset()
    invalidates: bool = False

    def voids_the_copy(self) -> bool:
        """Whether a message of this type makes the entry untrusted on its own.

        Separate from the per-field check, and deliberately so: this answer does
        not depend on what the message carried, because for these types what it
        carried cannot be seen.
        """
        return self.invalidates


@dataclasses.dataclass(frozen=True, eq=False)
class StateEntry:
    """One part of the cache: what feeds it, and how to ask for it.

    Args:
        name: how the rest of the model refers to this entry.
        read: ``callable(client)`` returning a mapping of field name to value -
            the unit's whole answer for this entry. Runs on the CALLER's thread,
            never the RX thread.
        feeds: message class -> :class:`FieldPlan`.

    Every entry's :attr:`read` is one request and one ANSWER, which the read
    path relies on to tell its own answer apart from a push that arrived while
    it was waiting. One answer is not the same as one message: a ``Version``
    READ is answered by the unit's reply and then by a question of the unit's
    own, and the read path survives that because a message that said nothing is
    not counted. Said nothing means it applied no field this entry keeps AND
    named none it does not - a plan with :attr:`FieldPlan.invalidates` set is
    never in that case, because every message of its type makes the copy
    untrusted whatever it carried. ``device/state.py``'s ``_apply_one`` decides
    it. An entry whose read provokes a STREAM OF ANSWERS instead - a ``File``
    enumeration, a preset dump - has to say how many messages that is, and this
    class does not carry that yet because nothing needs it. It lands with the
    first such entry, along with the test that a number other than one works.
    """

    name: str
    read: typing.Callable
    feeds: typing.Mapping
    #: Entries whose copies stop being true when THIS entry's value moves.
    #: Applied only on a real change, which is what makes it different from
    #: listing the same message type on each of them: the model's own READ of
    #: this entry reports the slot that is already loaded, and telling three
    #: other entries the unit had changed would be the model reporting its own
    #: question as news.
    resets: tuple = ()

    def fields(self) -> frozenset:
        """Every field name this entry holds, across all the types that feed it."""
        found = set()
        for plan in self.feeds.values():
            found |= plan.kept | plan.no_presence
        return frozenset(found)


def _held(value):
    """A value the cache can still trust after the RX thread has moved on.

    A scalar is copied by value and needs nothing. A SUBMESSAGE does: ``getattr``
    hands back a container living inside the message the RX thread just decoded,
    which every other listener was handed too. Storing that reference means the
    model reports whatever anyone else does to it afterwards, from a thread the
    model does not control - and a preset payload is exactly the kind of thing a
    caller pokes at.

    So it is copied once, on the way in. The preset entry is the only thing this
    costs anything for, and it costs it on a `RecallPreset` push, which arrives
    on a recall or a read rather than continuously.
    """
    if isinstance(value, Message):
        copy = type(value)()
        copy.CopyFrom(value)
        return copy
    return value


def fields_applied(message, plan: FieldPlan) -> dict:
    """The fields of ``message`` this plan keeps, as a mapping.

    A kept field appears only if the message actually carries it, so merging the
    result leaves everything the push did not mention alone. A presence-free
    field always appears, because there is no such thing as a message of its
    type that does not carry it.

    A submessage is copied rather than referenced - see :func:`_held`.
    """
    found = {}
    for name in plan.kept:
        if protocol.field_present(message, name):
            found[name] = _held(getattr(message, name))
    for name in plan.no_presence:
        found[name] = _held(getattr(message, name))
    return found


def unkept_fields(message, plan: FieldPlan) -> list:
    """Everything ``message`` sets that this plan does NOT keep, named.

    Two kinds, and the second is the reason this is not a set difference over
    the descriptor:

    * a field in the schema that the entry does not keep. Tomorrow's field, or a
      part of the unit this story does not model yet;
    * a field number the schema does not have at all. The schema here is
      recovered rather than published (ADR-0010), so a field the unit really
      sends and our bindings have never heard of is ordinary. It decodes into
      nothing, which makes it the quietest possible way to drop half a message,
      and the only way to see it is to notice the bytes.

    Detecting the second costs a copy of the message per push, which is why this
    runs once per entry rather than once per field. It is cheap for the small
    keyed pushes an edit produces; an entry fed by whole preset dumps should
    measure it before assuming the same.
    """
    named = {field.name for field, _ in message.ListFields()}
    found = sorted(named - plan.kept - plan.no_presence - SCAFFOLDING)
    if _carries_unknown_fields(message):
        found.append("a field number this schema does not have")
    return found


def _carries_unknown_fields(message) -> bool:
    """Whether ``message`` decoded with bytes our schema could not place.

    Asked by subtraction: discard the unknown fields from a copy and see whether
    the message got smaller. The copy is why the caller does this once per
    entry, not once per field.

    **The reason to keep it that way is recursion**, and this paragraph exists
    because the obvious simplification loses it silently.
    ``DiscardUnknownFields`` descends into submessages;
    ``google.protobuf.unknown_fields.UnknownFieldSet`` reports only the top
    level. Measured on protobuf 7.35.1: for an unknown field nested inside a
    known submessage, subtraction says yes and ``UnknownFieldSet`` counts zero.
    Nested is the case that will matter most, because the entries fed by whole
    preset dumps are the ones with submessages in them.

    (``message.UnknownFields()``, the third way to ask, raises
    ``NotImplementedError`` outright on the C implementation this project runs
    on. That is why it is not used, but it is not the reason for the choice
    between the other two.)
    """
    probe = type(message)()
    probe.CopyFrom(message)
    before = probe.ByteSize()
    probe.DiscardUnknownFields()
    return probe.ByteSize() != before


# -- the entries -------------------------------------------------------------


#: The unit's identity. Firmware and serial cannot change while a connection is
#: up: the only thing that changes either is a firmware update, and the firmware
#: `Updater` surface is permanently out of scope for this library (repo-root
#: ``CLAUDE.md``). That is an inference from scope rather than a measurement,
#: which is why the read is still the fallback rather than a one-time fill.
#:
#: The unit does not announce this. The one ``Version`` the connect burst carries
#: is the unit's answer to our version announce: it sets
#: ``cortex_control_version_valid`` and none of the unit's own fields. So the
#: burst does not warm this entry - and, because that field is one the entry does
#: not keep, it MARKS it untrusted on every connect and never answers it. Either
#: way first access reads, which is the case section 9's third column exists for:
#: where the unit does not tell us, we ask.
#:
#: **The read costs two messages, and only one of them says anything.** The
#: protocol is symmetric, so the unit answers a ``Version`` READ and then asks
#: one of its own, wanting Cortex Control's version: measured 2026-08-27 on
#: d14e, ten reads out of ten came back as a ``Version{UPDATE}`` of fifteen
#: fields followed 0.5-0.8 ms later by a ``Version{READ}`` carrying ``action``
#: alone. The question is not news about the unit and the cache does not count
#: it - see ``_apply_one`` in ``device/state.py``, where counting it cost a
#: second round trip whenever it landed before the reading thread woke.
_VERSION_FOR_IDENTITY = FieldPlan(
    kept=frozenset({"app_fw_version", "device_serial_number"}),
)


def _read_identity(client) -> dict:
    """``Version{READ}``: the unit's own firmware and serial."""
    return fields_applied(client.version(), _VERSION_FOR_IDENTITY)


IDENTITY = StateEntry(
    name="identity",
    read=_read_identity,
    feeds={pa.VersionMessage: _VERSION_FOR_IDENTITY},
)


#: Whether the live grid has edits nobody has saved. Section 9's table puts this
#: behind ``preset.has_unsaved_changes``, which is built: it lives in
#: ``device/preset.py`` and answers from this entry's copy. The cache holds it
#: because it is the entry the unit pushes most plainly - the connect burst
#: delivers one, and every CHANGE of the flag produces another. Not every edit:
#: an edit to an already-dirty preset sends nothing, measured 2026-08-14, which
#: is why the property reads a cached fact rather than counting notifications.
#:
#: ``is_dirty`` is the model's one presence-free field. Proto3 gives a plain bool
#: no presence, so a clean grid and an unmentioned grid are the same bytes and
#: there is nothing to check. The evidence for reading it anyway is the protocol
#: layer's: ``QuadCortex.preset_dirty`` records that it reads true after an edit
#: and false after a clean save, watched flipping across a save on hardware, and
#: says in as many words that absent simply IS false. Treating an unset message
#: as "not mentioned" would leave the model stuck dirty for the life of the
#: connection.
_PRESET_DIRTY = FieldPlan(no_presence=frozenset({"is_dirty"}))


def _read_dirty(client) -> dict:
    """``PresetDirty{READ}``: 2-11 ms on every measured poll.

    Goes through ``QuadCortex.preset_dirty``, which unwraps the reply to a bool,
    so this builds the mapping by hand rather than through
    :func:`fields_applied`. There is one field and it has no presence, so the two
    routes agree by construction - and using the published reader keeps the
    model off the transport.
    """
    return {"is_dirty": client.preset_dirty()}


#: What a change of loaded slot resets, and why each one is here.
#:
#: ``dirty`` is the one that had to be. A recall clears the unsaved-changes flag
#: on the unit and the unit says NOTHING about it - measured 2026-08-15, no
#: ``PresetDirty`` follows a recall. Without this the model would go on
#: reporting edits the recall discarded.
#:
#: ``scene`` is belt and braces. A recall does push a ``Scene`` carrying the new
#: value, so this mark is usually cleared moments later by the push that answers
#: it. It costs nothing when that arrives and saves a wrong answer if it ever
#: does not.
#:
#: ``preset`` is deliberately absent. A recall pushes eight to thirteen ``Grid``
#: messages and a whole ``RecallPreset``, either of which puts the preset entry
#: right on its own.
_A_RECALL_RESETS = ("dirty", "scene")


#: What a recall really pushes, measured on hardware 2026-08-15 across two host
#: recalls. Within about 120 ms of the request::
#:
#:     Grid x 8-13      the new grid, block by block
#:     RecallPreset     the whole new preset
#:     Scene            the new active scene
#:     SetlistPosition  which slot is loaded now
#:
#: and **no PresetDirty at all**. Each of the three plans below follows from
#: that list rather than from the same guess applied three times, which is what
#: this was before somebody measured it.
#:
#: WHICH preset is loaded, which is a different question from what is in it.
#: The two used to be one entry, with an invented counter standing in for the
#: unit's own answer. They are separate now because the unit reports this
#: directly and ``SetlistPosition{READ}`` really does answer - confirmed on
#: hardware 2026-08-15, in 3 ms, echoing the request id. Section 9's table said
#: so and nobody had checked.
#:
#: ``preset.is_current`` compares this rather than counting events, so it is
#: answering with a fact the unit stated rather than with the model's own
#: bookkeeping.
#:
#: The PRESET entry is deliberately not fed by this type at all. A recall
#: pushes the whole new preset in a ``RecallPreset``, which replaces our copy
#: outright, and eight to thirteen ``Grid`` pushes about 90 ms before this one
#: arrives. Marking the preset here would throw away what the connect burst had
#: just delivered, for a message that says nothing about contents.
_LOADED = FieldPlan(kept=frozenset({"folder_key", "position", "is_factory"}))


def _read_loaded(client) -> dict:
    """``SetlistPosition{READ}``: 3 ms, measured."""
    return fields_applied(client.loaded_position(), _LOADED)


LOADED = StateEntry(
    name="loaded",
    read=_read_loaded,
    feeds={pa.SetlistPositionMessage: _LOADED},
    resets=_A_RECALL_RESETS,
)


DIRTY = StateEntry(
    name="dirty",
    read=_read_dirty,
    feeds={
        pa.PresetDirtyMessage: _PRESET_DIRTY,
    },
)


#: The preset on the grid right now. Read from the LIVE grid rather than from a
#: stored slot: ``RecallPreset{READ}`` answers with what is on the grid including
#: unsaved edits, has no side effects, and leaves the active scene alone - where
#: ``read_preset`` RECALLS a slot, discards unsaved edits, resets the active
#: scene and interrupts the audio every time, including when it recalls the
#: preset already loaded.
#:
#: ``reason`` is kept, and the read below answers for it, because every
#: ``RecallPreset`` carries it. Measured on hardware 2026-08-15: the connect
#: burst's seed push sets ``action``, ``preset`` and ``reason``, and so does the
#: push a recall produces. An entry that did not keep it would therefore be
#: marked for re-reading by the very burst that warmed it, and the first read of
#: ``device.preset`` would pay for a round trip the unit had already made.
#:
#: This was tried the other way first. Keeping a field an entry cannot read back
#: is worse than paying for the read - once marked, it would be gone for good -
#: so the answer was to make it readable, which is what
#: ``QuadCortex.read_current_preset_push`` is for. Nothing reads ``reason`` yet;
#: it gets a property when the Directory story gives it one to hang off.
_RECALL_FOR_PRESET = FieldPlan(kept=frozenset({"preset", "reason"}))

#: A ``Grid`` push is a sparse, keyed delta into a deeply nested structure. The
#: model does NOT merge it: it notes that the grid moved and re-reads the whole
#: live preset on the next access. One edit on the touchscreen produces about
#: forty of these and costs exactly one re-read, because this is a flag rather
#: than a queue, and the read has no side effects.
#:
#: **What merging would take, if it is ever worth doing.** Each push would have
#: to be applied BY KEY into the stored ``BinaryPreset`` - chain by row, model by
#: column, parameter by index - and, to stay honest, the per-field "did this
#: mention something we do not model" check would have to walk that structure
#: recursively rather than reading ``ListFields()`` at the top level. The prize
#: is that reads stay instant while somebody is editing on the unit. The reason
#: it is not here is that the recursive check is where the whole risk of it sits,
#: and it would have sat next to the objects three other stories are blocked on.
#: A caller who needs the fresh value sooner subscribes to ``device.events`` and
#: reads it themselves, which is what that surface is for.
#:
#: ``action`` is deliberately not consulted, and that IS this entry's own
#: decision about it (see :data:`SCAFFOLDING`): an ``UPDATE`` and a ``DELETE``
#: mean opposite things, and both of them mean the grid moved, which is all this
#: entry needs to know.
_GRID_MOVED = FieldPlan(invalidates=True)

#: Scene labels and colours live inside the preset payload, so a change to
#: either makes our copy of it wrong. Neither message can be read by the
#: per-field check: ``index`` and ``label`` have no presence, so renaming scene A
#: to a blank label sets nothing in ``ListFields()`` at all. Colours are not
#: modelled, and that is not a reason to ignore them - ``scene_colors`` is in the
#: payload we hold, and there is no harmless-field category.
_SCENE_TEXT_CHANGED = FieldPlan(invalidates=True)


def _read_preset(client) -> dict:
    """``RecallPreset{READ}``: the live grid, unsaved edits included.

    One request and one reply, like every entry's read. The reply is the unit's
    whole answer, so it REPLACES what we hold rather than merging into it.

    Goes through ``read_current_preset_push`` rather than ``read_current_preset``
    so that ``reason`` comes back with the preset. Same request, same match, same
    wire - that method is where ``read_current_preset`` does its work.
    """
    return fields_applied(client.read_current_preset_push(), _RECALL_FOR_PRESET)


PRESET = StateEntry(
    name="preset",
    read=_read_preset,
    feeds={
        pa.RecallPresetMessage: _RECALL_FOR_PRESET,
        pa.GridMessage: _GRID_MOVED,
        pa.SceneLabelMessage: _SCENE_TEXT_CHANGED,
        pa.SceneColorMessage: _SCENE_TEXT_CHANGED,
    },
)


#: Which scene is active. ``Scene{READ}`` answers with ``selected_scene`` and
#: echoes the request id; confirmed live by switching scenes between reads.
_SCENE = FieldPlan(kept=frozenset({"selected_scene"}))


def _read_scene(client) -> dict:
    """``Scene{READ}``: the active scene, as a ``protocol.Scene``.

    Goes through ``QuadCortex.active_scene``, which unwraps the reply to the
    enum, so this builds the mapping by hand rather than through
    :func:`fields_applied` - the same shape :func:`_read_dirty` uses, and for the
    same reason: using the published reader keeps the model off the transport.
    """
    return {"selected_scene": client.active_scene()}


SCENE = StateEntry(
    name="scene",
    read=_read_scene,
    feeds={
        pa.SceneMessage: _SCENE,
    },
)


#: Everything the cache tracks. Section 9's table still has more rows than this
#: - the setlists, recents and favourites, and the device-level settings - and
#: each arrives with the surface that reads it. An entry with no reader would be
#: a plan, not a fact, and every push mentioning a field it did not keep would
#: mark it for a read nobody had asked for.
#:
#: The Directory's rows are the ones that need something this class does not
#: have. Every read here is one request and one answer, which is how the read path
#: tells its own answer apart from a push that arrived while it was waiting. A
#: setlist listing is a STREAM - one `File` READ makes the unit enumerate its
#: whole tree, several hundred messages over about fifteen seconds - so those
#: entries land with the change to `StateEntry` that lets a read say how many
#: messages it expects.
ENTRIES = (IDENTITY, DIRTY, PRESET, SCENE, LOADED)

ENTRY_BY_NAME = {entry.name: entry for entry in ENTRIES}


def _by_message_class():
    """message class -> the entries it feeds, with each one's plan."""
    found = {}
    for entry in ENTRIES:
        for message_class, plan in entry.feeds.items():
            found.setdefault(message_class, []).append((entry, plan))
    return {cls: tuple(pairs) for cls, pairs in found.items()}


#: Which entries a decoded message could touch. A type absent from here feeds
#: nothing and is ignored outright, which is what makes the metronome's tempo
#: stream free: it arrives in pairs on every beat of every connection and the
#: model has no tempo surface yet, so there is nothing for it to churn.
FEEDS = _by_message_class()

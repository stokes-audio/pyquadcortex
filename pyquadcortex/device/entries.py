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

from pyquadcortex import protocol
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: Fields the per-field check skips, on every entry. They are on nearly every
#: message in this schema, and treating them as unkept state would mark every
#: entry for re-reading on every push - the thrash section 9 exists to avoid.
#: ``tests/test_state.py`` checks both are really on every feeding type, so the
#: skip cannot quietly forgive a field that never arrives.
#:
#: ``request_id`` is the transport's, always. **``action`` is not always.** It
#: says nothing on the two message types tracked today, which is why it is
#: skipped globally - but on ``Grid`` it is load-bearing state: an
#: ``UPDATE`` carrying ``hash: 0`` is transmitted and ignored, while the same
#: payload with ``action: DELETE`` removes the block
#: (``QuadCortex.remove_block``). So a ``Grid`` entry - issue #12 - cannot
#: inherit this skip: two pushes with identical payloads and opposite meanings
#: would apply identically and mark nothing. Give that entry its own decision
#: about ``action`` rather than widening this set, and see ADR-0011.
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
    """

    kept: frozenset = frozenset()
    no_presence: frozenset = frozenset()


@dataclasses.dataclass(frozen=True, eq=False)
class StateEntry:
    """One part of the cache: what feeds it, and how to ask for it.

    Args:
        name: how the rest of the model refers to this entry.
        read: ``callable(client)`` returning a mapping of field name to value -
            the unit's whole answer for this entry. Runs on the CALLER's thread,
            never the RX thread.
        feeds: message class -> :class:`FieldPlan`.

    Every entry's :attr:`read` is one request and one reply, which the read path
    relies on to tell its own answer apart from a push that arrived while it was
    waiting. An entry whose read provokes a STREAM instead - a ``File``
    enumeration, a preset dump - has to say how many messages that is, and this
    class does not carry that yet because nothing needs it. It lands with the
    first such entry, along with the test that a number other than one works.
    """

    name: str
    read: typing.Callable
    feeds: typing.Mapping

    def fields(self) -> frozenset:
        """Every field name this entry holds, across all the types that feed it."""
        found = set()
        for plan in self.feeds.values():
            found |= plan.kept | plan.no_presence
        return frozenset(found)


def fields_applied(message, plan: FieldPlan) -> dict:
    """The fields of ``message`` this plan keeps, as a mapping.

    A kept field appears only if the message actually carries it, so merging the
    result leaves everything the push did not mention alone. A presence-free
    field always appears, because there is no such thing as a message of its
    type that does not carry it.
    """
    found = {}
    for name in plan.kept:
        if protocol.field_present(message, name):
            found[name] = getattr(message, name)
    for name in plan.no_presence:
        found[name] = getattr(message, name)
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
#: The unit does not announce this. It sends a ``Version`` READ of its own during
#: the connect handshake - asking US for Cortex Control's version - and that
#: message carries none of the unit's own fields, so the burst does not warm this
#: entry and first access reads. That is the case section 9's third column exists
#: for: where the unit does not tell us, we ask.
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
#: behind ``preset.has_unsaved_changes``, which arrives with the preset surface
#: (issue #12); the cache holds it now because it is the entry the unit pushes
#: most plainly - the connect burst delivers one, and every edit produces one.
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


DIRTY = StateEntry(
    name="dirty",
    read=_read_dirty,
    feeds={pa.PresetDirtyMessage: _PRESET_DIRTY},
)


#: Everything the cache tracks. Section 9's table has more rows than this - the
#: preset on the grid, the active scene, the setlists, recents and favourites,
#: the device-level settings - and each arrives with the surface that reads it
#: (#12 and after). An entry with no reader would be a plan, not a fact, and
#: every push mentioning a field it did not keep would mark it for a read nobody
#: had asked for.
ENTRIES = (IDENTITY, DIRTY)

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

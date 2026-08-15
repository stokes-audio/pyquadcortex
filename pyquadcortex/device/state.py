"""The model's copy of what the unit is doing: ``docs/domain-model.md`` section 9.

Someone turns a knob on the touchscreen while a script is connected. The library
should not be wrong about it. That is the whole job of this module.

Three rules do it, and they are section 9's:

1. **The unit tells us when things change**, so we listen and store what it says.
   Reading a value later costs nothing.
2. **A message that mentions something we do not keep makes us stop trusting our
   copy** of that part, and the next read goes to the unit. The check is per
   FIELD, because applying the half of a message we understand and dropping the
   rest is the one failure that leaves the cache confidently wrong rather than
   obviously stale.
3. **A write updates our copy immediately** and the unit's echo confirms it in
   the background (:mod:`pyquadcortex.device.watch`).

Two things follow from where the code runs.

**Pushes are applied as data, not as invalidation triggers.** The metronome
clock always runs, so the unit pushes ``GlobalTempo`` in pairs on every beat of
every connection. A cache that re-read on every inbound message would spend its
life re-reading. Applying pushes as data does not care, and a message type no
entry tracks is ignored outright.

**The RX thread never asks the unit for anything.** :meth:`DeviceState.apply_push`
runs on it (ADR-0009) and only ever merges and marks; the caller's thread does
any reading. The transport enforces the other half - a read from the RX thread
raises rather than stalling the read loop for a timeout it could never survive.

What is here is what the model reads today. Section 9's table is longer, and each
remaining row arrives with the surface that reads it - see
:mod:`pyquadcortex.device.entries`.
"""

import itertools
import logging
import threading
import time

from pyquadcortex.device import entries
from pyquadcortex.device.entries import fields_applied, unkept_fields
from pyquadcortex.device.watch import (WATCH_PATIENCE, WatchOutcome, Watchdog,
                                       WriteWatch)

log = logging.getLogger(__name__)

#: What the write watchdog's thread is called, so a caller reading a stack dump
#: knows whose it is.
WATCHDOG_THREAD_NAME = "pyquadcortex-watchdog"


class _Slot:
    """One entry's copy of the unit's state, and how much we trust it."""

    __slots__ = ("fields", "needs_read", "witnessed", "_arrivals")

    def __init__(self):
        #: field name -> value, holding only what the unit has actually said.
        #: A field that is missing was never mentioned, which is not the same as
        #: the unit reporting it empty - so it is read rather than answered.
        self.fields = {}
        #: Set when a message named something this entry does not keep. Cleared
        #: by a read, not by another push.
        self.needs_read = False
        #: How many messages for this entry the listener has handled. The read
        #: path uses the difference across a read to tell its own answer apart
        #: from a push that arrived while it was waiting.
        #:
        #: Drawn from a counter rather than written `+= 1`, which is the
        #: transport's idiom for the same job (`Transport._ids`). It also keeps
        #: the package's one-translation-boundary check honest: that check
        #: refuses every spelling of index arithmetic outside `translate.py`
        #: precisely because it cannot tell a counter from an off-by-one on a
        #: row, and a rule with an exemption for "but mine is fine" is not a
        #: rule. Do not simplify this back.
        self._arrivals = itertools.count(1)
        self.witnessed = 0

    def arrived(self) -> None:
        """Note that one more message for this entry has been handled."""
        self.witnessed = next(self._arrivals)


class DeviceState:
    """The cache one connection's worth of model reads through.

    Built by :func:`pyquadcortex.connect`, which registers it before the connect
    handshake so it hears the handshake's burst of state - about 400 messages
    covering nearly every state type, which is what makes the cache warm for
    free. A `Device` built on a connection somebody else opened starts cold and
    reads on first access, because the burst is already over by then.

    Valid only while its connection is. :meth:`close` stops it listening and
    makes it refuse reads it could still have served from its copy, because a
    model that reports the unit's state through an object with no unit behind it
    is the failure this whole layer exists to avoid.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._slots = {entry.name: _Slot() for entry in entries.ENTRIES}
        #: One per entry, so two threads asking for the same cold value make one
        #: round trip rather than two. Always taken BEFORE :attr:`_lock`, and
        #: never held by anything that holds :attr:`_lock`.
        self._reading = {entry.name: threading.Lock() for entry in entries.ENTRIES}
        self._client = None
        self._detach = None
        self._closed = False
        self._watches = {entry.name: [] for entry in entries.ENTRIES}
        self._watchdog = Watchdog(self._gave_up_on, WATCHDOG_THREAD_NAME)

    # -- wiring ---------------------------------------------------------------

    def listen_on(self, hub) -> None:
        """Subscribe to every message ``hub`` decodes, for the connection's life.

        ``hub`` is anything with the transport's ``add_listener`` - the
        `Transport` itself, which is what ``protocol.connect(before_handshake=)``
        hands over, or a `QuadCortex` for a connection already up.

        Registering before the handshake is the only way to hear its burst, so
        this is called with the transport rather than the client on the path
        that owns the connection.
        """
        if self._detach is not None:
            raise RuntimeError(
                "this DeviceState is already listening - a second registration "
                "would apply every message twice")
        self._detach = hub.add_listener(self.apply_push)

    def bind(self, client) -> None:
        """Use ``client`` for the reads this cache issues on a caller's thread."""
        with self._lock:
            self._client = client

    def close(self) -> None:
        """Stop listening, stop watching writes, and answer nothing further.

        Safe to call more than once. It does not close the connection: whoever
        opened it closes it.
        """
        with self._lock:
            self._closed = True
            self._slots = {name: _Slot() for name in self._slots}
            self._client = None
            in_flight = [watch for watches in self._watches.values()
                         for watch in watches]
            self._watches = {name: [] for name in self._watches}
        detach, self._detach = self._detach, None
        if detach is not None:
            detach()
        # Outside the lock: the watchdog takes it to mark an entry, and stopping
        # it joins its thread.
        self._watchdog.stop()
        for watch in in_flight:
            # Released with no outcome, not timed out. Nothing can settle these
            # now, so anybody waiting has to be let go - and calling them timed
            # out would be a claim about the unit rather than a fact.
            log.debug("watch.abandoned %s %s - the connection closed first",
                      watch.entry, sorted(watch.sent))
            watch.publish()

    # -- what the unit tells us (RX thread) -----------------------------------

    def apply_push(self, message) -> None:
        """Merge one decoded message into the cache. Runs on the RX THREAD.

        Section 9's rules 1 and 2, in the order they matter: apply the fields
        this entry keeps, then - if the message named anything it does not -
        mark the entry so the next read goes to the unit. Both, not either: the
        value between the push and that read is otherwise the old one, which is
        confidently wrong for a shorter while rather than not at all.

        A message type no entry tracks returns immediately. Each entry is
        applied inside its own guard, so a bug in one costs that entry this
        message and no more - the RX thread has to survive whatever happens
        here, and the transport's own guard would skip every remaining entry.

        Never reads from the unit, and never can: the transport refuses a read
        from this thread outright (ADR-0009).
        """
        for entry, plan in entries.FEEDS.get(type(message), ()):
            try:
                self._apply_one(entry, plan, message)
            except Exception:
                # Logged, not raised: "the RX thread never dies" outranks
                # surfacing this here, and there is no caller to surface it to.
                log.exception("cache.push_failed %s from %s", entry.name,
                              type(message).__name__)

    def _apply_one(self, entry, plan, message) -> None:
        applied = fields_applied(message, plan)
        unkept = unkept_fields(message, plan)
        with self._lock:
            if self._closed:
                return
            slot = self._slots[entry.name]
            slot.arrived()
            was_empty = not slot.fields
            slot.fields.update(applied)
            if was_empty and slot.fields:
                log.debug("cache.filled %s from a %s", entry.name,
                          type(message).__name__)
            if applied:
                log.debug("push.applied %s %s", entry.name, sorted(applied))
            if unkept:
                slot.needs_read = True
                log.info("push.forced_reread %s - a %s named %s, which the "
                         "model does not keep", entry.name,
                         type(message).__name__, ", ".join(unkept))
            settled = [(watch, watch.absorb(applied))
                       for watch in self._watches[entry.name]]
            self._watches[entry.name] = [watch for watch, outcome in settled
                                         if outcome is None]
        for watch, outcome in settled:
            if outcome is None:
                continue
            self._report(watch)
            if outcome is WatchOutcome.DIFFERENT:
                # The echo we just applied put the unit's own answer in the
                # cache for the field it disagreed about - but a write the unit
                # contradicted is a write we did not understand, and any OTHER
                # field it carried is still sitting there on our say-so, never
                # confirmed by anything. Section 10 only asks for a log line
                # here; leaving it at that is how the one path that means "we
                # have a bug" ends up the one that cleans up least.
                self.mark_for_reread(
                    watch.entry,
                    f"the unit disagreed with a write of {sorted(watch.sent)}")
            watch.publish()

    # -- what we hand back (the caller's thread) ------------------------------

    def value(self, entry_name: str, field: str):
        """This entry's ``field``, reading from the unit if we cannot answer.

        Answers from the cache when the unit has told us and nothing has said
        our copy is wrong. Otherwise it reads - on THIS thread, which is the
        rule: the RX thread notes what needs re-reading and the caller's thread
        does the reading.

        Raises:
            KeyError: if no entry keeps ``field``. A programming error, not a
                question about the unit, so it never becomes a round trip.
            RuntimeError: if this cache is closed, is not bound to a connection,
                or if the unit's answer did not carry ``field``. That last one
                is deliberate: an absent string decodes as ``""`` and reporting
                that would be a guess. Nothing is cached for it, so asking again
                can still succeed.
        """
        entry = self._entry(entry_name)
        if field not in entry.fields():
            raise KeyError(
                f"the model does not keep {field!r} on the {entry_name} entry - "
                f"it keeps {sorted(entry.fields())}")
        with self._reading[entry_name]:
            with self._lock:
                self._check_open()
                slot = self._slots[entry_name]
                if not slot.needs_read and field in slot.fields:
                    return slot.fields[field]
                witnessed_before = slot.witnessed
                client = self._client
            if client is None:
                raise RuntimeError(
                    f"this model is not connected to a unit, so {entry_name} "
                    f"cannot be read")
            log.debug("read.proactive %s", entry_name)
            answer = entry.read(client)
            with self._lock:
                self._check_open()
                slot = self._slots[entry_name]
                # A read is the unit's whole answer, so it REPLACES rather than
                # merging: a field it did not carry is one the unit did not
                # confirm, and leaving an older value there would report
                # something no read has ever returned.
                slot.fields = dict(answer)
                # Our own answer came back through the listener too - listeners
                # see a reply before the thread that asked for it wakes (ADR-
                # 0009) - so exactly one message is expected here, every entry's
                # read being one request and one reply. Anything beyond that is
                # a push that landed while we waited, and clearing the mark
                # regardless would throw it away with nothing left to recover it
                # from. An entry whose read provokes a stream will have to say
                # how many messages that is; see `StateEntry`.
                extra = slot.witnessed - witnessed_before
                slot.needs_read = extra > 1
                if slot.needs_read:
                    log.info("push.forced_reread %s - %d message(s) arrived "
                             "while it was being read", entry_name, extra)
                if field in slot.fields:
                    return slot.fields[field]
        raise RuntimeError(
            f"the unit's answer for {entry_name} did not carry {field}, so it "
            f"cannot be reported. Nothing was cached for it, so asking again "
            f"can still succeed.")

    def cached(self, entry_name: str) -> dict:
        """What this entry holds right now, without asking the unit.

        A snapshot for a caller that wants to know what the model knows -
        logging, a health check, a test - rather than what the unit is doing.
        Use :meth:`value` for that.

        Refuses once this cache is closed, like every other read here. An empty
        mapping is an answer, and "the unit has told us nothing" is not what a
        connection that has gone away means.
        """
        with self._lock:
            self._check_open()
            return dict(self._slots[self._entry(entry_name).name].fields)

    def needs_read(self, entry_name: str) -> bool:
        """Whether the next :meth:`value` on this entry will go to the unit.

        Refuses once closed, for the same reason: on a closed cache every slot
        is empty, so this would answer "no" - "the next read is free" about a
        read that raises.
        """
        with self._lock:
            self._check_open()
            return self._slots[self._entry(entry_name).name].needs_read

    def mark_for_reread(self, entry_name: str, why: str) -> None:
        """Stop trusting this entry's copy; the next read goes to the unit."""
        with self._lock:
            self._check_open()
            self._slots[self._entry(entry_name).name].needs_read = True
        log.info("cache.forced_reread %s - %s", entry_name, why)

    # -- what we tell the unit ------------------------------------------------

    def write_through(self, entry_name: str, fields: dict, send,
                      patience: float = WATCH_PATIENCE) -> WriteWatch:
        """Apply ``fields`` to the cache, send the write, and watch for the echo.

        Section 9's third rule: our copy is updated immediately, because waiting
        for the echo would make every write pay for information we almost always
        already have. The echo confirms it in the background and the caller need
        not look - a matching echo changes nothing, which is one code path
        rather than two.

        Args:
            entry_name: the part of the cache this write changes.
            fields: what is being written, field name to value. This is the set
                the watcher holds the unit to: every one of them must come back
                with the value here.
            send: ``callable()`` performing the protocol write. Called after the
                cache is updated. If it raises, the write never reached the unit,
                so the entry is marked for re-reading and the exception is passed
                on - our copy would otherwise be the only place that value exists.
            patience: seconds to wait for the echo before giving up.

        Returns:
            A :class:`~pyquadcortex.device.watch.WriteWatch`. Ignoring it is
            fine and normal; the outcomes are logged either way.

        Raises:
            ValueError: if a field is not one this entry keeps. A write the
                cache cannot hold would be applied nowhere and confirmed against
                nothing.
            RuntimeError: if this cache is closed.

        Nothing in M1 writes through here yet - ``momentary`` (#14) is the first
        - but the cache is write-through by design, so the path exists and is
        tested rather than being added under the pressure of a feature.
        """
        entry = self._entry(entry_name)
        unknown = sorted(set(fields) - entry.fields())
        if unknown:
            raise ValueError(
                f"the {entry_name} entry does not keep {unknown}, so writing "
                f"them through the cache would apply them nowhere")
        watch = WriteWatch(entry_name, fields, time.monotonic() + patience)
        with self._lock:
            self._check_open()
            self._slots[entry_name].fields.update(fields)
            self._watches[entry_name].append(watch)
        self._watchdog.add(watch)
        try:
            send()
        except BaseException:
            with self._lock:
                self._watches[entry_name] = [
                    w for w in self._watches[entry_name] if w is not watch]
            # Settled here rather than left for the watchdog: nothing is coming
            # back, and a watcher that fired at its deadline would mark the
            # entry a second time, long after a caller had put it right.
            watch.time_out()
            self.mark_for_reread(
                entry_name, f"the write of {sorted(fields)} never reached the unit")
            watch.publish()
            raise
        return watch

    def _gave_up_on(self, watch: WriteWatch) -> None:
        """The watchdog's callback: no echo arrived. Runs on ITS thread.

        Returns quietly if the connection went away while it was deciding.
        There is no copy left to mark, and raising here would land on a thread
        with nobody to catch it.
        """
        with self._lock:
            if self._closed:
                return
            self._watches[watch.entry] = [
                w for w in self._watches[watch.entry] if w is not watch]
        log.warning("watch.timeout %s - the unit never echoed %s", watch.entry,
                    sorted(watch.sent))
        self.mark_for_reread(
            watch.entry, f"the unit never echoed a write of {sorted(watch.sent)}")

    def _report(self, watch: WriteWatch) -> None:
        """Log a settled write. Runs on whichever thread settled it."""
        if watch.disagreement is not None:
            field, sent, returned = watch.disagreement
            log.warning("watch.different %s - we sent %s=%r and the unit "
                        "returned %r", watch.entry, field, sent, returned)
        else:
            log.debug("watch.confirmed %s %s", watch.entry, sorted(watch.sent))

    # -- internals ------------------------------------------------------------

    def _entry(self, entry_name: str):
        try:
            return entries.ENTRY_BY_NAME[entry_name]
        except KeyError:
            raise KeyError(
                f"the model tracks no state called {entry_name!r} - it tracks "
                f"{sorted(entries.ENTRY_BY_NAME)}") from None

    def _check_open(self) -> None:
        if self._closed:
            raise RuntimeError(
                "this model's connection is closed, so nothing it remembers is "
                "still true of the unit - open a new one with "
                "pyquadcortex.connect()")

    def __repr__(self) -> str:
        # Says nothing about the unit, only about this object: repr() is called
        # by debuggers and logging and must never trigger a device read.
        with self._lock:
            state = "closed" if self._closed else "open"
            warm = sorted(name for name, slot in self._slots.items()
                          if slot.fields and not slot.needs_read)
        return f"<DeviceState {state}, warm on {warm or 'nothing'}>"

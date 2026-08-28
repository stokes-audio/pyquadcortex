"""Knowing a write landed: ``docs/domain-model.md`` section 10.

The unit accepts writes it does not understand and silently does nothing, so "no
error" proves nothing. What we have instead is the echo, and each write gets a
watcher that compares the echo against what we sent.

**The bar is exactly one sentence.**

    Every field we sent must come back with the value we sent.

Not "the echo equals what we sent". The unit legitimately changes things nobody
asked about - a gain-reduction meter, a mirrored parameter, NaN in unused slots,
dropdown values recomputed on rows we never touched - and all four are things we
did not send, so comparing only what we sent needs no exception for any of them.

The watcher never blocks the write. It reports one of three outcomes, and the
caller can ignore all three: a confirmation changes nothing, because section 9's
third rule already applied the write to our copy.
"""

import enum
import threading
import time

#: What one watcher waits before giving up. Measured echo latency is 113-116 ms
#: for a parameter write and 290-420 ms for a block placement (section 10), and
#: ``PresetDirty`` answers in 2-11 ms, so this is several times the slowest thing
#: measured. It is a ceiling on how long a silently ignored write stays in our
#: copy, not a latency anybody waits on, so generous is the safe direction.
WATCH_PATIENCE = 2.0


class WatchOutcome(enum.Enum):
    """How a write ended. Section 10's three, and there is no fourth."""

    #: Every field we sent came back with the value we sent. Our copy was
    #: already right; nothing to do.
    CONFIRMED = "confirmed"

    #: A field we sent came back with another value. That is a bug in our code,
    #: now with a name and a location. The echo has been applied, so our copy
    #: holds the unit's answer rather than our losing write.
    DIFFERENT = "different"

    #: Nothing came back. The part of our copy the write touched is marked for
    #: re-reading, so a silently ignored write self-corrects instead of
    #: poisoning the cache.
    TIMED_OUT = "timed out"


class WriteWatch:
    """One write, and what the unit said about it.

    Built by ``DeviceState.write_through``; a caller holds one only to find out
    how the write ended. Safe to read from any thread: :meth:`absorb` runs on the
    RX thread and :meth:`time_out` on the watchdog, while the caller reads
    :attr:`outcome` on its own.
    """

    def __init__(self, entry: str, sent: dict, deadline: float):
        if not sent:
            raise ValueError(
                "a write with no fields has nothing to confirm - name the "
                "fields being written, or do not go through the cache")
        self.entry = entry
        self.sent = dict(sent)
        self.deadline = deadline
        self._lock = threading.Lock()
        self._settled = threading.Event()
        self._outcome: WatchOutcome | None = None
        self._disagreement: tuple[str, object, object] | None = None
        self._confirmed: set[str] = set()

    @property
    def outcome(self):
        """The :class:`WatchOutcome`, or ``None`` while the write is in flight."""
        with self._lock:
            return self._outcome

    @property
    def disagreement(self):
        """``(field, what we sent, what came back)``, or ``None``.

        Set only for :attr:`WatchOutcome.DIFFERENT`, and it is the whole point of
        that outcome: it turns "a write did not stick" into a field name, a value
        and a place to look.
        """
        with self._lock:
            return self._disagreement

    def settled(self, timeout=None) -> bool:
        """Wait for an outcome; return whether there is one.

        Nothing in the model waits on this - the write already returned and the
        cache is already right. It is here so a test, or a caller that wants to
        be sure, can ask.

        When it returns TRUE, everything the outcome causes has already
        happened: the log line is written and a timed-out write has already
        marked its entry for re-reading. That is why :meth:`publish` is a
        separate step from the two methods that decide the outcome.

        It returns FALSE either because the wait ran out or because the
        connection closed with this write still in flight - see
        :meth:`publish`. Nothing can settle a write once the connection that
        would have echoed it is gone, so the wait ends rather than running to
        the caller's timeout, which by default is forever.
        """
        self._settled.wait(timeout)
        return self.outcome is not None

    def absorb(self, applied: dict):
        """Take one echo into account; return the outcome if this settles it.

        ``applied`` is what the cache took out of the echo, which is the same
        thing the cache now holds - so a field the echo did not carry is simply
        not yet confirmed, and a later echo can still confirm it. A write is
        confirmed only when every field it sent has come back matching.

        Does NOT publish: the caller acts on the outcome, then calls
        :meth:`publish`.
        """
        with self._lock:
            if self._outcome is not None:
                return None
            for field, value in self.sent.items():
                if field not in applied:
                    continue
                if applied[field] != value:
                    self._disagreement = (field, value, applied[field])
                    self._outcome = WatchOutcome.DIFFERENT
                    break
                self._confirmed.add(field)
            else:
                if self._confirmed == set(self.sent):
                    self._outcome = WatchOutcome.CONFIRMED
            return self._outcome

    def time_out(self) -> bool:
        """Give up on this write; return whether this call is what ended it.

        False when an echo got there first, which is the race the watchdog runs
        into every time a write is confirmed near its deadline. Does NOT
        publish - see :meth:`absorb`.
        """
        with self._lock:
            if self._outcome is not None:
                return False
            self._outcome = WatchOutcome.TIMED_OUT
        return True

    def publish(self) -> None:
        """Release anyone waiting in :meth:`settled`. Idempotent.

        Called after an outcome's consequences have been applied, so a waiter
        never wakes ahead of them. Also called with NO outcome when the
        connection closes on a write still in flight: there is nothing left that
        could answer it, so the honest thing is to stop the waiting rather than
        to invent a third party's verdict. :meth:`settled` reports that as
        false, which is what it says it reports.
        """
        self._settled.set()

    def __repr__(self) -> str:
        outcome = self.outcome
        return (f"<WriteWatch {self.entry} {sorted(self.sent)} "
                f"{outcome.value if outcome else 'in flight'}>")


class Watchdog:
    """One thread that gives up on writes the unit never echoed.

    One thread for the whole connection, not one per write, and it does not
    start until something is written - a connection that only reads never has it.
    It sleeps until the earliest deadline and wakes when a write is added.

    It marks and logs; it never reads from the unit. Section 9's rule is about
    the RX thread, but the reason behind it - the thread that notices is not the
    thread that asks - is why a read here would be just as wrong: it would put a
    device round trip on a thread no caller knows exists.
    """

    def __init__(self, on_timeout, name: str):
        self._on_timeout = on_timeout
        self._name = name
        self._wake = threading.Condition()
        self._watches: list[WriteWatch] = []
        self._running = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    def add(self, watch: WriteWatch) -> None:
        """Start watching ``watch``, starting the thread if this is the first.

        Once :meth:`stop` has run this watches nothing and starts nothing. That
        is the race a write can lose: ``write_through`` finds the cache open,
        the connection closes, and only then does the write reach here. Starting
        a thread for it would leave one waiting out a deadline on a connection
        nobody can reach, so the write is released with no outcome instead -
        which is what the connection closing under a write means anyway.
        """
        with self._wake:
            if self._stopped:
                late = watch
            else:
                late = None
                self._watches.append(watch)
                if self._thread is None:
                    self._running = True
                    self._thread = threading.Thread(target=self._loop,
                                                    name=self._name, daemon=True)
                    self._thread.start()
                self._wake.notify_all()
        if late is not None:
            late.publish()          # outside the lock: it wakes other threads

    def stop(self, join_timeout: float = 2.0) -> None:
        """Stop the thread for good and forget every outstanding write.

        Idempotent, and permanent - a :class:`Watchdog` belongs to one
        connection and a stopped one never watches again.

        The watches are dropped rather than timed out: the connection is going
        away, so "the unit never answered" would be a claim about the unit
        rather than a fact. Releasing whoever is waiting on them is the caller's
        job, because the caller is the one that knows the connection has gone -
        see ``DeviceState.close``.
        """
        with self._wake:
            self._running = False
            self._stopped = True
            self._watches = []
            thread, self._thread = self._thread, None
            self._wake.notify_all()
        if thread is not None and thread is not threading.current_thread():
            thread.join(join_timeout)

    def _loop(self) -> None:
        while True:
            with self._wake:
                while True:
                    if not self._running:
                        return
                    now = time.monotonic()
                    self._watches = [w for w in self._watches
                                     if w.outcome is None]
                    due = [w for w in self._watches if w.deadline <= now]
                    if due:
                        self._watches = [w for w in self._watches
                                         if w.deadline > now]
                        break
                    # Computed and waited on inside one hold of the lock, so an
                    # add() cannot slip in between and have its notify missed -
                    # which with no other watch outstanding would be a wait with
                    # no timeout, and a write that never times out.
                    delay = (min(w.deadline for w in self._watches) - now
                             if self._watches else None)
                    self._wake.wait(delay)
            for watch in due:
                if watch.time_out():
                    try:
                        self._on_timeout(watch)
                    finally:
                        # Published last, so a caller woken by settled() finds
                        # the entry already marked rather than racing us to it.
                        watch.publish()

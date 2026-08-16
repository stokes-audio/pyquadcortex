"""What the model noticed, for a caller who wants to know as it happens.

The model keeps a copy of what the unit is doing and re-reads whatever it stops
trusting - but it only re-reads when somebody asks for a value. A script
following the unit closely wants to know sooner than that, so this is where it
finds out::

    def watch(event):
        print(event)

    with pyquadcortex.connect() as device:
        device.events.subscribe(watch)

Two events, both about the model's copy rather than about the wire:

* :class:`Changed` - a push moved a value we hold.
* :class:`Invalidated` - we stopped trusting our copy of something. The next
  read of it goes to the unit. A subscriber who wants that to happen now can
  simply read it.

**Why there is a thread in here.** The unit's messages arrive on the transport's
receiving thread, and that thread may not read from the unit: the transport
refuses it outright, because a read there would stall the very loop that has to
collect the reply (ADR-0009). Handing an event straight over on that thread would
therefore make the obvious response - go and re-read it - raise. So the receiving
thread only puts the event in a queue, and a thread this module owns hands it to
subscribers, where reading is allowed and expected.

The costs are worth stating plainly. An event can lag the unit by however long
the subscribers ahead of it take; subscribers are served one at a time, in the
order they subscribed; and a subscriber that blocks forever holds up every event
behind it. None of that can delay the unit or the receiving thread, which is the
property being bought.
"""

import dataclasses
import logging
import queue
import threading

log = logging.getLogger(__name__)

#: What the delivery thread is called, so a caller reading a stack dump knows
#: whose it is - and so a test can prove delivery is not on the caller's thread.
EVENT_THREAD_NAME = "pyquadcortex-events"

#: Seconds :meth:`EventStream.close` waits for the delivery thread to finish the
#: event in its hands. A subscriber that never returns is the caller's bug, and
#: hanging their ``close()`` on it would turn their bug into ours.
CLOSE_PATIENCE = 2.0


@dataclasses.dataclass(frozen=True)
class ModelEvent:
    """Something the model noticed. Subscribe on ``device.events``."""


@dataclasses.dataclass(frozen=True)
class Changed(ModelEvent):
    """A push moved a value the model holds.

    Only when the value really moved. The unit restates things it has already
    said - a ``PresetDirty`` arrives on every edit whether or not the answer is
    new - and reporting those as changes would make the stream useless for the
    thing it is for.
    """

    part: str            #: which part of the model's copy, e.g. ``"preset"``
    fields: tuple        #: the field names that moved


@dataclasses.dataclass(frozen=True)
class Invalidated(ModelEvent):
    """The model stopped trusting its copy of ``part``.

    The next read of it goes to the unit. Fired on the change from trusted to
    untrusted only, so one edit on the touchscreen - which produces about forty
    ``Grid`` pushes - produces one of these rather than forty.
    """

    part: str
    why: str


class _Stop:
    """The sentinel that ends the delivery loop.

    A class rather than ``None`` so that it can never be confused with an event,
    and never with a caller publishing nothing by mistake.
    """


_STOP = _Stop()


class EventStream:
    """Where :class:`Changed` and :class:`Invalidated` reach a caller.

    Reached as ``device.events``. One per `Device`, closed with it.
    """

    def __init__(self, thread_name: str = EVENT_THREAD_NAME):
        self._lock = threading.Lock()
        self._listeners = []
        self._queue = queue.SimpleQueue()
        self._thread = None
        self._closed = False
        self._thread_name = thread_name

    def subscribe(self, listener):
        """Call ``listener(event)`` for everything published from now on.

        Returns a callable that unsubscribes; calling it twice is harmless.

        The listener runs on this stream's own thread, one event at a time, in
        the order they were published. It MAY read from the device - that is
        what the thread is for. If it raises, the exception is logged and every
        other subscriber still gets the event.

        Events published BEFORE the first subscriber are not kept. This is a
        stream of what is happening, not a log of what happened.
        """
        if not callable(listener):
            raise TypeError(
                f"a subscriber is called with one event, so it has to be "
                f"callable; got {type(listener).__name__}")
        with self._lock:
            if self._closed:
                raise RuntimeError(
                    "this event stream is closed, so nothing will ever be "
                    "published on it again - open a new connection with "
                    "pyquadcortex.connect()")
            self._listeners.append(listener)
            self._start()

        def unsubscribe():
            with self._lock:
                if listener in self._listeners:
                    self._listeners.remove(listener)

        return unsubscribe

    def publish(self, event) -> None:
        """Queue one event. Safe on the receiving thread, and it does not block.

        Does nothing at all when nobody has subscribed, so a script that never
        asks for events pays nothing for them. That matters more than it looks:
        the unit pushes its tempo on every beat of every connection, so a queue
        that filled regardless would grow for the life of the process.
        """
        with self._lock:
            if self._closed or not self._listeners:
                return
        self._queue.put(event)

    def close(self) -> None:
        """Stop delivering and drop every subscriber. Safe to call twice."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._listeners = []
            thread, self._thread = self._thread, None
        if thread is not None:
            self._queue.put(_STOP)
            thread.join(timeout=CLOSE_PATIENCE)
            if thread.is_alive():
                log.warning(
                    "events.close_timed_out - a subscriber has not returned "
                    "after %.1fs, so the delivery thread is still inside it",
                    CLOSE_PATIENCE)

    def __len__(self) -> int:
        """How many subscribers there are."""
        with self._lock:
            return len(self._listeners)

    def __repr__(self) -> str:
        with self._lock:
            state = "closed" if self._closed else "open"
            count = len(self._listeners)
        return f"<EventStream {state}, {count} subscriber(s)>"

    # -- internals ------------------------------------------------------------

    def _start(self) -> None:
        """Start the delivery thread. Called with the lock held.

        A daemon thread, so a caller who forgets to close cannot leave the
        interpreter waiting on it at exit. `close` is still what stops it
        properly, and `Device.close` calls that.
        """
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._deliver,
                                        name=self._thread_name, daemon=True)
        self._thread.start()

    def _deliver(self) -> None:
        while True:
            event = self._queue.get()
            if event is _STOP:
                return
            with self._lock:
                listeners = list(self._listeners)
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    # Logged, not raised. There is nobody to raise to here, and
                    # one subscriber's bug must not cost the others their event
                    # or stop the stream for good.
                    log.exception("events.subscriber_failed on %r", event)

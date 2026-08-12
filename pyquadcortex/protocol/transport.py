"""Framed protobuf transport over an hidapi-like device.

``Transport`` moves logical protobuf messages across a USB-HID device that
speaks the Quad Cortex framed protocol. It:

  * frames outbound messages into 129-byte HID output reports (via ``framing``)
    and writes them;
  * runs a background RX thread that reads input reports, reassembles
    multi-report messages, decodes them, and correlates responses to callers by
    ``request_id``;
  * hands every decoded message to any persistent listener (``add_listener``),
    which is how a long-lived caller sees the unsolicited pushes no waiter is
    expecting;
  * runs a background keepalive thread that periodically pokes the device so it
    keeps the session alive.

The transport deals only in ``framing`` (bytes <-> ``(type, payload)``) and
``registry`` (type tag <-> protobuf class). The envelope is confirmed against
a real Cortex Control session (see docs/protocol.md).
Write errors are EXPECTED: the QC stalls every SET_REPORT's status stage after
consuming the data (see ``_write_report``), so writes that "fail" succeeded.

Thread-safety / robustness notes:
  * ``_pending`` (request_id -> waiter) is guarded by ``_lock``; each request is
    popped by exactly one of the RX thread (on reply) or the requesting thread
    (on timeout), so no entry leaks and no double-delivery occurs.
  * The RX thread must never die: every per-message decode/parse is wrapped so a
    malformed frame or unknown message type is logged and skipped, and the
    reassembly buffer is reset so one bad frame cannot wedge the stream.
  * Listeners run ON the RX thread, so the same rule covers them: one that raises
    is logged and skipped, and one may not issue a correlated read (``request``,
    ``await_broadcast``, ``collect`` refuse to run on that thread - see
    ``_refuse_read_from_rx``).
  * The keepalive thread swallows send failures and keeps going.
"""

import gzip
import itertools
import logging
import math
import threading
import time

from google.protobuf.message import DecodeError

from pyquadcortex.protocol import framing, registry
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

log = logging.getLogger(__name__)

# hidapi ``read`` takes a max size and a timeout in milliseconds. The size is a
# ceiling; a single input report is returned. The timeout bounds how long the RX
# thread blocks, which is also how quickly it notices ``stop()``.
_READ_SIZE = 1024
_READ_TIMEOUT_MS = 200

# After a request wait() times out, if the RX thread has already claimed the
# pending entry it is about to populate the response slot; wait this long (a
# thread-scheduling grace, normally satisfied in microseconds) for it to finish
# before declaring a timeout.
_DELIVERY_GRACE = 0.5

# Hard upper bound on how many reports a single logical message can legitimately
# span. The confirmed envelope has NO total-length field (completion is purely
# flag-driven), so the bound is a policy choice: 1 MiB of reassembled body is
# comfortably above the largest message observed in the Windows capture (the
# ModelRepo reply, a ~47 KB gzipped blob spanning 371 reports) while still small
# enough that a wedged stream resets promptly.
#
# Defense-in-depth: if a lost LAST-flagged frame ever leaves the reassembly
# buffer unable to complete, capping at this bound lets the RX loop reset the
# buffer instead of accumulating forever and wedging the stream. No legitimate
# message ever reaches the cap, so it never triggers on good traffic.
_MAX_MESSAGE_BODY = 1 << 20  # bytes of reassembled body tolerated per message
_MAX_REPORTS_PER_MESSAGE = math.ceil(_MAX_MESSAGE_BODY / framing.CHUNK_SIZE)


class DeviceLostError(ConnectionError):
    """The USB device is gone: two consecutive HID reads failed.

    Raised by every transport entry point once loss is confirmed, carrying the
    second read error's message. **Do not branch on that text.** It is often the
    stale write-STALL lookalike ("IOHIDDeviceSetReport failed: (0xE0005000)")
    rather than anything honest - measured across four loss transitions: after
    one reboot both attempts carried the misleading text, after another both
    were honest, and a shutdown gave one of each. The retry exists for blip
    immunity, not better messages; the reliable signal is that a read raised
    AT ALL.

    The asymmetry worth remembering: **a READ raising means the device is gone;
    a WRITE raising means nothing at all.** Every write to a healthy QC "fails"
    (the status-stage STALL; 91 write errors and 0 read errors over one measured
    145-second healthy session), and the two can carry identical text - the
    distinguishing fact is which call raised, never the message.
    """


class Transport:
    """Correlated request/response transport over an hidapi-like device."""

    def __init__(self, device, keepalive_interval=5.0):
        self._dev = device
        self._keepalive_interval = keepalive_interval
        self._ids = itertools.count(1)
        self._pending = {}  # request_id -> (Event, [response|None], request class)
        # Waiters for UNSOLICITED device broadcasts, matched by message class
        # (e.g. the RecallPreset push the device emits when a preset is
        # recalled - it carries no request_id to correlate on). List of
        # (expected_class, Event, [response|None]).
        self._type_waiters = []
        self._collectors = []
        # Persistent listeners: called with EVERY decoded inbound message until
        # removed. Unlike the three above, not scoped to one trigger or one
        # reply. See add_listener.
        self._listeners = []
        # Guards every registry above plus _ids. State only: never held across
        # blocking device I/O, and never held while calling a listener.
        self._lock = threading.Lock()
        # Serializes device writes so each logical message's reports are written
        # as an atomic group (a keepalive can't slip between a multi-report
        # message's header and its continuation reports). SEPARATE from _lock:
        # the state lock is never held across blocking device I/O.
        self._write_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        # Set to the confirming exception once the device is lost. Written only
        # by the RX thread; read everywhere. See device_lost / _confirm_lost.
        self._device_lost = None
        self._rx_buffer = []  # reports accumulated for the in-progress message
        self._rx = threading.Thread(
            target=self._read_loop, name="qcctl-rx", daemon=True
        )
        self._ka = threading.Thread(
            target=self._keepalive_loop, name="qcctl-keepalive", daemon=True
        )

    # -- lifecycle -----------------------------------------------------------

    def start(self):
        """Start the background RX and keepalive threads."""
        self._running = True
        self._stop_event.clear()
        self._rx.start()
        self._ka.start()

    def stop(self, join_timeout=1.0):
        """Signal the background threads to exit and wait for them to finish.

        Joins the RX and keepalive threads (bounded by ``join_timeout`` each)
        so a caller can safely close the device afterwards: closing the hidapi
        handle while the RX thread is still inside ``read()`` crashes the
        process on Windows. Idempotent. ``join_timeout=0`` skips the join for
        callers that only want to signal (e.g. from within a background thread).
        """
        self._running = False
        self._stop_event.set()
        if join_timeout:
            for thread in (self._rx, self._ka):
                if thread.is_alive() and thread is not threading.current_thread():
                    thread.join(timeout=join_timeout)

    # -- device loss -----------------------------------------------------------

    @property
    def device_lost(self):
        """The exception that confirmed device loss, or ``None`` while healthy."""
        return self._device_lost

    def _check_lost(self):
        if self._device_lost is not None:
            raise DeviceLostError(
                f"the USB device is gone ({self._device_lost}); reconnect and "
                f"build a new Transport"
            ) from self._device_lost

    def _confirm_lost(self, error):
        """Record loss and fail every blocked waiter fast (RX thread only)."""
        self._device_lost = error
        log.warning("device lost: %s", error)
        with self._lock:
            pending, self._pending = self._pending, {}
            waiters, self._type_waiters = self._type_waiters, []
        # Slots stay None; the woken callers see device_lost set and raise
        # DeviceLostError instead of returning None or waiting out a timeout.
        for ev, _slot, _cls in pending.values():
            ev.set()
        for _cls, _match, ev, _slot in waiters:
            ev.set()

    # -- the RX thread may not read --------------------------------------------

    def _refuse_read_from_rx(self, what):
        """Refuse a correlated wait attempted from the RX thread.

        The RX thread is the only thread that delivers a message to a waiter, so a
        wait issued from inside it can never be satisfied: it sits out its whole
        window with the read loop stopped behind it, which is the "the RX thread
        never blocks" rule broken in the worst way available. ``request`` and
        ``await_broadcast`` would time out; ``collect`` would return empty, having
        stalled the link for its full duration. Listeners
        (:meth:`add_listener`) are the only caller code that runs on that thread,
        so this guard is what makes the listener contract enforced rather than
        merely requested (ADR-0008).

        Cheap enough to leave in every entry point: one identity comparison.
        """
        if threading.current_thread() is self._rx:
            raise RuntimeError(
                f"{what}() was called from the RX thread, which is the thread "
                f"that would have to deliver the answer - so the wait can never "
                f"be satisfied, and the read loop stops for its whole duration. "
                f"A listener applies what a push carries and notes what needs "
                f"re-reading; the caller's thread does the re-reading "
                f"(docs/domain-model.md section 9)."
            )

    # -- outbound ------------------------------------------------------------

    def send(self, message):
        """Frame and write ``message`` to the device (fire-and-forget).

        The message's reports are written as an atomic group under
        ``_write_lock`` so a concurrent send (e.g. a keepalive) cannot interleave
        its reports between this message's header and continuation reports - which
        would corrupt both, since continuation reports carry no header. Encoding
        happens outside the lock to keep the critical section to device I/O only.
        """
        self._check_lost()
        msg_type = registry.type_for(type(message))
        reports = framing.encode_message(msg_type, message.SerializeToString())
        with self._write_lock:
            for report in reports:
                self._write_report(report)

    def _write_report(self, report):
        """Write one HID output report, tolerating the QC's status-stage STALL.

        Confirmed by capture and live probe: the QC
        accepts the 128-byte data stage of every SET_REPORT and then STALLs the
        status stage - for Cortex Control too (all 273 of its writes in the
        capture completed with USBD_STATUS_STALL_PID, yet every one was acted
        on). Host HID stacks surface that stall as a write error (hidapi
        returns -1 on Windows; IOKit raises 0xE0005000 on macOS), so a "failed"
        write here is EXPECTED and means the report was delivered. Errors are
        logged at debug and swallowed. A genuinely dead device is detected by the
        RX loop (a READ raising twice) and surfaces as :class:`DeviceLostError`
        on the next transport call - never by write errors, whose text can be
        byte-identical to loss.
        """
        try:
            self._dev.write(report)
        except Exception:
            log.debug("HID write reported an error (expected QC stall)", exc_info=True)

    def next_request_id(self):
        """Draw a fresh request_id from the same counter :meth:`request` uses.

        Lets a caller (e.g. ``read_preset``) tag a fire-and-forget message with
        an id it can later correlate a broadcast against, without colliding with
        ids handed out by :meth:`request`.
        """
        with self._lock:
            return next(self._ids)

    def request(self, message, timeout=5.0):
        """Send ``message`` and block until the matching response arrives.

        Assigns a fresh ``request_id``, registers a waiter BEFORE writing (so a
        reply can never race ahead of registration), then waits up to ``timeout``
        seconds. Raises ``TimeoutError`` if no correlated response arrives.

        Correlation is BY MESSAGE TYPE, checked against ``request_id`` when the
        reply carries one (confirmed by capture): the device
        answers a request with a message of the SAME type, but READ replies
        (e.g. Version) carry no ``request_id`` echo, and a state-changing
        request triggers a cascade of OTHER-type messages that all echo the
        request's id (recalling a preset emits UndoRedo/Grid/Scene/... all with
        the same request_id before the SetlistPosition echo). So the reply is
        the first inbound message whose TYPE matches the request's, and whose
        request_id - if present on both sides - matches too.

        Refused when called from the RX thread, where it could only ever time out
        (see ``_refuse_read_from_rx``).
        """
        self._refuse_read_from_rx("request")
        self._check_lost()
        ev = threading.Event()
        slot = [None]
        with self._lock:
            rid = next(self._ids)
            self._pending[rid] = (ev, slot, type(message))
        message.request_id = rid
        try:
            self.send(message)
        except Exception:
            with self._lock:
                self._pending.pop(rid, None)
            raise
        if not ev.wait(timeout):
            # wait() timed out, but a reply can still land in the race window
            # between the timeout and our removing the pending entry. Whoever
            # pops the entry "wins": if the RX thread already popped it, it is
            # committed to delivering a response (it sets the slot, then the
            # event), so wait briefly for that to complete rather than dropping
            # a reply that actually arrived.
            with self._lock:
                delivered_by_rx = self._pending.pop(rid, None) is None
            if delivered_by_rx:
                ev.wait(_DELIVERY_GRACE)
            if slot[0] is not None:
                return slot[0]
            self._check_lost()
            raise TimeoutError(f"no response for request_id={rid}")
        if slot[0] is None:
            self._check_lost()   # woken by _confirm_lost, not by a reply
        return slot[0]

    def collect(self, expected_class, trigger, seconds, match=None):
        """Fire ``trigger()`` and gather EVERY matching message for ``seconds``.

        The counterpart of :meth:`await_broadcast` for the case where one request
        provokes many pushes rather than one: a single ``File`` READ makes the
        device enumerate every folder it knows about, several hundred of them on
        the observed unit, arriving over ten to twenty seconds.

        Returns the messages in arrival order. Unlike a waiter, a collector does
        not consume messages - they still reach any waiter or other collector.

        Refused when called from the RX thread, which would stall the read loop
        for the whole window and so collect nothing (see
        ``_refuse_read_from_rx``).
        """
        self._refuse_read_from_rx("collect")
        self._check_lost()
        got = []
        entry = (expected_class, match, got)
        with self._lock:
            self._collectors.append(entry)
        try:
            trigger()
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                if self._device_lost is not None:
                    break        # nothing more is coming; return what arrived
                time.sleep(0.1)
        finally:
            with self._lock:
                if entry in self._collectors:
                    self._collectors.remove(entry)
        return got

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        """Fire ``trigger()`` and block for the next matching ``expected_class``.

        For device broadcasts that answer an action rather than a request: the
        RecallPreset push emitted after a preset is recalled is delivered lazily
        and can't be correlated with :meth:`request`. Register a type waiter
        FIRST, then run ``trigger`` (e.g. a recall ``send``), then wait.

        ``match``, if given, is a predicate ``(message) -> bool`` that further
        filters candidates of ``expected_class``: a broadcast of the right type
        for which ``match`` returns False is IGNORED (left undelivered), so the
        waiter keeps waiting for the right one. This is how ``read_preset``
        skips a stale/seed RecallPreset push (no request_id) and accepts only the
        push echoing its own recall's request_id (confirmed: host recalls echo
        the id on the push). The device services large pushes lazily (10-25s
        observed), hence the generous default timeout. Raises ``TimeoutError``
        on no matching broadcast.

        Refused when called from the RX thread, where it could only ever time out
        (see ``_refuse_read_from_rx``).
        """
        self._refuse_read_from_rx("await_broadcast")
        self._check_lost()
        ev = threading.Event()
        slot = [None]
        entry = (expected_class, match, ev, slot)
        with self._lock:
            self._type_waiters.append(entry)
        try:
            trigger()
        except Exception:
            with self._lock:
                if entry in self._type_waiters:
                    self._type_waiters.remove(entry)
            raise
        if not ev.wait(timeout):
            with self._lock:
                if entry in self._type_waiters:
                    self._type_waiters.remove(entry)
            if slot[0] is not None:
                return slot[0]  # landed in the timeout/removal race window
            self._check_lost()
            raise TimeoutError(
                f"no {expected_class.__name__} broadcast within {timeout}s"
            )
        if slot[0] is None:
            self._check_lost()   # woken by _confirm_lost, not by a broadcast
        return slot[0]

    # -- persistent listeners --------------------------------------------------

    def add_listener(self, listener):
        """Register ``listener`` to see EVERY decoded inbound message.

        The transport's other three inbound hooks are one-shot and scoped to a
        trigger: :meth:`request` correlates one reply, :meth:`await_broadcast`
        waits for one push, :meth:`collect` gathers for a fixed number of
        seconds. A listener is none of those. It stays registered until it is
        removed and sees every message the RX thread decodes - including the
        unsolicited pushes no waiter is expecting, which :meth:`_dispatch` would
        otherwise drop at debug level. That is what a push-fed cache needs (see
        ``docs/domain-model.md`` section 9).

        ``listener`` is called as ``listener(message)`` with the parsed protobuf.

        Additive by construction: a listener does not CONSUME a message. It is
        notified first, and the message then reaches every collector and waiter
        exactly as it would have with no listener registered.

        **Treat the message as read-only.** It is not a copy: the object handed to
        a listener is the same one the next listener and the waiter receive, so a
        listener that normalizes or tidies it in place changes what they see.
        Read what you need out of it and merge that into your own state.

        Registration and removal are safe while the RX thread is running.
        Returns a zero-argument callable that removes this registration;
        :meth:`remove_listener` does the same job for a caller who kept the
        listener rather than the callable.

        **Listeners run on the RX thread**, synchronously, in registration order,
        before the message reaches its waiter (so a cache fed by a listener is
        already current when the blocked caller wakes). Two consequences:

        * **A listener must not block.** It spends the RX thread's time: whatever
          it does delays the next report being read. Apply the push and return.
        * **A listener may not read from the device**, and that is enforced
          rather than asked for: :meth:`request`, :meth:`await_broadcast` and
          :meth:`collect` raise ``RuntimeError`` when called from the RX thread
          (see ``_refuse_read_from_rx``). Such a call could never have worked -
          the RX thread is the one that delivers replies, so a wait from inside
          it can never be satisfied - and the rule it breaks is older than this
          method: the RX thread applies pushes and notes what needs re-reading,
          and the caller's thread does the re-reading
          (``docs/domain-model.md`` section 9). :meth:`send` is NOT refused,
          being fire-and-forget, but a listener that writes owns the delay it
          adds to the read loop.

        A listener that raises is logged and skipped: the RX thread survives, the
        other listeners still see that message, and the message still reaches its
        waiter. Same contract as every other step in this module's RX path, and
        wider than the rest of it - ``BaseException``, not just ``Exception``,
        because ``pytest.fail()`` and ``sys.exit()`` are ordinary things for
        caller code to do and neither may cost the connection its read loop.

        A listener lives only as long as the connection. Device loss neither
        removes nor notifies listeners - there is simply nothing further to
        deliver - and a new connection means a new ``Transport`` and a new
        registration.

        Evidence: the mechanism is proven offline against ``FakeHid``
        (``tests/test_transport.py``). That a listener registered before the
        connect handshake sees the handshake's state burst is confirmed on
        hardware (``tests/hardware/test_broadcast_listener.py``). Registering
        that early needs ``protocol.connect(before_handshake=...)``, because
        ``connect`` runs the handshake before it hands the client back.

        Raises:
            DeviceLostError: if the device is already known to be gone.
        """
        self._check_lost()
        with self._lock:
            self._listeners.append(listener)
        return lambda: self.remove_listener(listener)

    def remove_listener(self, listener):
        """Unregister ``listener``; return True if it had been registered.

        Never raises and is safe to call twice, so a teardown path can call it
        unconditionally. Removal takes the FIRST registration equal to
        ``listener``, which is what makes ``remove_listener(self._apply)`` work
        for a bound method: each attribute access builds a new object, and those
        compare equal. Two consequences of that, neither of them a problem unless
        it is a surprise: registering the same callable twice registers it twice
        and it is then called twice per message, needing one removal each; and a
        listener whose class defines ``__eq__`` can have an equal-but-different
        registration removed instead of the one passed.

        Safe to call from inside a listener, though the message being delivered
        may still reach the listener being removed - notification runs over a
        snapshot taken before the first listener was called.
        """
        with self._lock:
            try:
                self._listeners.remove(listener)
            except ValueError:
                return False
        return True

    def _notify_listeners(self, message):
        """Hand ``message`` to every listener (RX thread).

        Snapshot under the lock, call outside it: a listener that registers or
        removes a listener would otherwise deadlock on the non-reentrant state
        lock, and holding that lock across arbitrary caller code is exactly what
        the rest of this module avoids.
        """
        with self._lock:
            listeners = list(self._listeners)
        for listener in listeners:
            try:
                listener(message)
            except BaseException:
                # BaseException, not Exception, and this is the only place in the
                # module that goes that wide. Everything else on the RX path is
                # our own code, where a BaseException means something genuinely
                # fatal; a listener is arbitrary caller code, and the ways it can
                # raise outside Exception are ordinary rather than exotic -
                # pytest.fail() and sys.exit() both do. Letting one of those
                # through kills the RX thread, and the failure the caller sees is
                # a TimeoutError on the next request with device_lost unset: the
                # connection is dead and nothing says why. "The RX thread never
                # dies" is absolute, so it outranks the usual rule about not
                # swallowing BaseException.
                log.exception(
                    "inbound listener %r raised on %s; skipping it for this "
                    "message", listener, type(message).__name__
                )

    # -- inbound -------------------------------------------------------------

    def _read_loop(self):
        while self._running:
            try:
                report = self._dev.read(_READ_SIZE, _READ_TIMEOUT_MS)
            except Exception as first:
                # A READ raising is the device-loss signal (writes raise on every
                # healthy message - the QC's status-stage STALL - so a write error
                # means nothing; measured: 91 write errors, 0 read errors, in one
                # healthy 145 s session). Retry once: a success means a transient
                # blip; a second failure confirms loss. The retry does NOT
                # reliably improve the error text - across four measured loss
                # transitions the second message was honest twice and the stale
                # 0xE0005000 stall-lookalike twice - so nothing anywhere may
                # branch on the message; "a read raised" is the whole signal.
                try:
                    report = self._dev.read(_READ_SIZE, _READ_TIMEOUT_MS)
                except Exception as second:
                    self._confirm_lost(second)
                    # The RX thread never dies - but there is nothing left to
                    # read, so wait quietly instead of spinning on a dead handle.
                    self._stop_event.wait()
                    return
                log.debug("HID read failed once, then recovered; treating as a "
                          "transient blip and resetting the reassembly buffer "
                          "(first error: %s)", first)
                self._rx_buffer = []

            if not report:
                continue

            report = bytes(report)
            # A FIRST-flagged report always begins a new logical message: drop
            # any stale partial buffer (e.g. from joining mid-stream or a lost
            # LAST frame) so the new message reassembles cleanly.
            if (
                len(report) >= 3
                and report[2] & framing.FLAG_FIRST
                and self._rx_buffer
            ):
                # Debug, not warning: this is routine. The device interleaves
                # bursts of pushes (one File READ produces dozens of folder
                # listings), so a new message beginning while a partial is still
                # buffered is expected, and recovery is automatic. A partial that
                # someone was actually waiting for surfaces as a request timeout,
                # which is the real failure signal - the same principle applied to
                # the benign write stall.
                log.debug(
                    "FIRST-flagged report with %d buffered report(s) pending; "
                    "dropping stale partial message",
                    len(self._rx_buffer),
                )
                self._rx_buffer = []

            self._rx_buffer.append(report)
            try:
                complete = framing.is_complete(self._rx_buffer)
            except Exception:
                # A malformed leading report (e.g. too short to hold a header)
                # cannot be reassembled; drop the buffer and resync.
                log.exception("malformed report header; resetting reassembly buffer")
                self._rx_buffer = []
                continue

            if not complete:
                if len(self._rx_buffer) > _MAX_REPORTS_PER_MESSAGE:
                    # A legitimate message never spans this many reports, so the
                    # in-progress buffer must be wedged by a lost or corrupt
                    # frame (e.g. a declared payload_len that will never be
                    # reached). Reset so the stream can resync instead of
                    # accumulating forever.
                    log.warning(
                        "reassembly buffer exceeded %d reports without "
                        "completing; resetting to unwedge the RX stream",
                        _MAX_REPORTS_PER_MESSAGE,
                    )
                    self._rx_buffer = []
                continue

            reports, self._rx_buffer = self._rx_buffer, []
            try:
                self._handle_message(reports)
            except Exception:
                # Backstop: _handle_message catches the expected decode/parse
                # errors, but the RX thread must never die, so swallow anything
                # unexpected (including from dispatch) and keep reading. The
                # buffer was already reset above.
                log.exception("unexpected error handling inbound message")

    def _handle_message(self, reports):
        """Decode, parse, and dispatch one fully-buffered logical message.

        Any failure (unknown type, bad frame, protobuf parse error) is logged and
        swallowed so the RX thread keeps running. The buffer was already reset by
        the caller, so a bad frame cannot wedge the stream.
        """
        try:
            msg_type, payload = framing.decode_reports(reports)
            # Confirmed by capture: the device gzip-compresses some
            # payloads at the frame level (e.g. RecallPreset pushes carrying a
            # full BinaryPreset). The decompressed bytes are the ordinary
            # protobuf message for that type.
            if payload[:2] == b"\x1f\x8b":
                payload = gzip.decompress(payload)
            cls = registry.class_for(msg_type)
            message = cls()
            message.ParseFromString(payload)
        except (KeyError, DecodeError, ValueError, OSError):
            # Expected on some device broadcasts: unknown/unregistered types
            # and non-protobuf "raw payload" pushes (e.g. License, CloudLogin -
            # their trailer flags a non-protobuf body). OSError covers
            # gzip.BadGzipFile. The RX thread survives; this is normal device
            # chatter we don't consume, so log at debug to avoid stderr noise.
            log.debug("skipping undecodable inbound message", exc_info=True)
            return
        self._dispatch(message)

    def _dispatch(self, message):
        """Route a decoded message to a waiter of the same message TYPE.

        A waiter matches when its request's protobuf class equals the inbound
        message's class AND, if the inbound message echoes a ``request_id``,
        that id belongs to the waiter. Cascade messages of other types (which
        echo the id of the request that caused them) and unsolicited broadcasts
        simply find no waiter and are dropped at debug level.

        Persistent listeners (:meth:`add_listener`) are notified FIRST, before any
        collector or waiter, and consume nothing: the routing below runs exactly
        as it would with no listener registered.
        """
        self._notify_listeners(message)
        with self._lock:
            collectors = [c for c in self._collectors
                          if c[0] is type(message) and (c[1] is None or c[1](message))]
        for _cls, _match, bucket in collectors:
            bucket.append(message)
        rid = message.request_id if _has_request_id(message) else None
        with self._lock:
            entry = None
            if rid is not None and rid in self._pending:
                _ev, _slot, cls = self._pending[rid]
                if cls is type(message):
                    entry = self._pending.pop(rid)
            if entry is None and rid is None:
                # No id on the reply (READ replies): first same-type waiter wins.
                for pending_rid in sorted(self._pending):
                    _ev, _slot, cls = self._pending[pending_rid]
                    if cls is type(message):
                        entry = self._pending.pop(pending_rid)
                        break
        if entry is not None:
            ev, slot, _cls = entry
            slot[0] = message
            ev.set()
            return
        # No request_id waiter matched: try the unsolicited-broadcast waiters.
        # A waiter's optional match predicate must also accept the message; a
        # right-type message its predicate rejects is left for a later broadcast
        # (this is what lets read_preset ignore a stale/seed RecallPreset push).
        with self._lock:
            for i, (cls, match, ev, slot) in enumerate(self._type_waiters):
                if cls is type(message) and (match is None or match(message)):
                    del self._type_waiters[i]
                    slot[0] = message
                    ev.set()
                    return
        log.debug("no waiter for %s (request_id=%s)", type(message).__name__, rid)

    # -- keepalive -----------------------------------------------------------

    def _keepalive_loop(self):
        while self._running:
            # Interruptible sleep: wakes immediately on stop() instead of
            # blocking for the full interval.
            if self._stop_event.wait(self._keepalive_interval):
                return
            if not self._running:
                return
            if self._device_lost is not None:
                # Nothing to keep alive; wait for stop() rather than raising a
                # DeviceLostError into the log every interval.
                self._stop_event.wait()
                return
            try:
                self.send(pa.KeepAliveMessage(action=pa.MessageAction.UPDATE))
            except DeviceLostError:
                self._stop_event.wait()
                return
            except Exception:
                # A keepalive failure must not kill the keepalive thread.
                log.exception("keepalive send failed")


def _has_request_id(message) -> bool:
    """True if ``message`` carries a set ``request_id`` field.

    ``request_id`` is a proto3 optional (synthetic oneof) on the messages this
    transport handles, so ``HasField`` distinguishes set from unset. Guarded so a
    message class lacking the field never raises.
    """
    try:
        return message.HasField("request_id")
    except ValueError:
        return False

"""Framed protobuf transport over an hidapi-like device.

``Transport`` moves logical protobuf messages across a USB-HID device that
speaks the Quad Cortex framed protocol. It:

  * frames outbound messages into 129-byte HID output reports (via ``framing``)
    and writes them;
  * runs a background RX thread that reads input reports, reassembles
    multi-report messages, decodes them, and correlates responses to callers by
    ``request_id``;
  * runs a background keepalive thread that periodically pokes the device so it
    keeps the session alive.

The transport deals only in ``framing`` (bytes <-> ``(type, payload)``) and
``registry`` (type tag <-> protobuf class). The envelope is CONFIRMED against
a real Cortex Control session (framing_spec.md "Phase 2 CONFIRMED framing").
Write errors are EXPECTED: the QC stalls every SET_REPORT's status stage after
consuming the data (see ``_write_report``), so writes that "fail" succeeded.

Thread-safety / robustness notes:
  * ``_pending`` (request_id -> waiter) is guarded by ``_lock``; each request is
    popped by exactly one of the RX thread (on reply) or the requesting thread
    (on timeout), so no entry leaks and no double-delivery occurs.
  * The RX thread must never die: every per-message decode/parse is wrapped so a
    malformed frame or unknown message type is logged and skipped, and the
    reassembly buffer is reset so one bad frame cannot wedge the stream.
  * The keepalive thread swallows send failures and keeps going.
"""

import gzip
import itertools
import logging
import math
import threading

from google.protobuf.message import DecodeError

from pyquadcortex import framing, registry
from pyquadcortex.proto import ProductionAutomation_pb2 as pa

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
        self._lock = threading.Lock()  # guards _pending / _ids (state only)
        # Serializes device writes so each logical message's reports are written
        # as an atomic group (a keepalive can't slip between a multi-report
        # message's header and its continuation reports). SEPARATE from _lock:
        # the state lock is never held across blocking device I/O.
        self._write_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
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

    # -- outbound ------------------------------------------------------------

    def send(self, message):
        """Frame and write ``message`` to the device (fire-and-forget).

        The message's reports are written as an atomic group under
        ``_write_lock`` so a concurrent send (e.g. a keepalive) cannot interleave
        its reports between this message's header and continuation reports - which
        would corrupt both, since continuation reports carry no header. Encoding
        happens outside the lock to keep the critical section to device I/O only.
        """
        msg_type = registry.type_for(type(message))
        reports = framing.encode_message(msg_type, message.SerializeToString())
        with self._write_lock:
            for report in reports:
                self._write_report(report)

    def _write_report(self, report):
        """Write one HID output report, tolerating the QC's status-stage STALL.

        CONFIRMED (Windows capture + live Windows probe, 2026-07-22): the QC
        accepts the 128-byte data stage of every SET_REPORT and then STALLs the
        status stage - for Cortex Control too (all 273 of its writes in the
        capture completed with USBD_STATUS_STALL_PID, yet every one was acted
        on). Host HID stacks surface that stall as a write error (hidapi
        returns -1 on Windows; IOKit raises 0xE0005000 on macOS), so a "failed"
        write here is EXPECTED and means the report was delivered. Errors are
        logged at debug and swallowed; a genuinely dead device shows up as
        request() timeouts, not write errors.
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
        reply carries one (CONFIRMED, Windows capture 2026-07-22): the device
        answers a request with a message of the SAME type, but READ replies
        (e.g. Version) carry no ``request_id`` echo, and a state-changing
        request triggers a cascade of OTHER-type messages that all echo the
        request's id (recalling a preset emits UndoRedo/Grid/Scene/... all with
        the same request_id before the SetlistPosition echo). So the reply is
        the first inbound message whose TYPE matches the request's, and whose
        request_id - if present on both sides - matches too.
        """
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
            raise TimeoutError(f"no response for request_id={rid}")
        return slot[0]

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
        push echoing its own recall's request_id (CONFIRMED: host recalls echo
        the id on the push). The device services large pushes lazily (10-25s
        observed), hence the generous default timeout. Raises ``TimeoutError``
        on no matching broadcast.
        """
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
            raise TimeoutError(
                f"no {expected_class.__name__} broadcast within {timeout}s"
            )
        return slot[0]

    # -- inbound -------------------------------------------------------------

    def _read_loop(self):
        while self._running:
            try:
                report = self._dev.read(_READ_SIZE, _READ_TIMEOUT_MS)
            except Exception:
                # A device read failure must not kill the RX thread; log, drop
                # any partial buffer, and try again.
                log.exception("HID read failed; resetting reassembly buffer")
                self._rx_buffer = []
                continue

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
                log.warning(
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
            # CONFIRMED (session-03 capture): the device gzip-compresses some
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
        """
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
            try:
                self.send(pa.KeepAliveMessage(action=pa.MessageAction.UPDATE))
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

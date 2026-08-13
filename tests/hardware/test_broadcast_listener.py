"""What a persistent listener actually hears from a real unit.

The offline tests prove the wiring: a listener is called, it consumes nothing,
and neither a raise nor an attempted device read can hurt the read loop
(``tests/test_transport.py``). Only the unit can prove there is anything to hear.
Two facts are checked here:

* a listener registered BEFORE the connect handshake sees the handshake's state
  burst - the one moment the unit volunteers nearly everything it knows, and the
  reason ``protocol.connect(before_handshake=...)`` exists;
* a listener registered on a live connection keeps receiving, takes nothing away
  from an ordinary request, and stops the moment it is removed.

State-neutral by construction (ADR-0005): this file only listens. It sends no
write of any kind, so there is nothing to snapshot and nothing to restore.
"""
import threading
import time

from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: The metronome clock always runs, so the unit pushes GlobalTempo in pairs, one
#: pair per beat, on every connection - 1.5 s apart at the slowest tempo the unit
#: offers (40 bpm). Ten seconds is several beats even there.
UNSOLICITED_PATIENCE = 10.0



class Recorder:
    """Collects messages from the RX thread for the calling thread to read."""

    def __init__(self):
        self._lock = threading.Lock()
        self._messages = []

    def __call__(self, message):
        with self._lock:
            self._messages.append(message)

    def messages(self):
        with self._lock:
            return list(self._messages)

    def names(self):
        return [type(m).__name__ for m in self.messages()]


def _wait_until(predicate, timeout):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def test_a_listener_registered_before_connecting_sees_the_handshake_burst(
        handshake_burst):
    """The burst is what makes a push-fed cache warm for free.

    Note what the wait below says about the hook: ``connect()`` returns about 2 s
    in, with only the ResetCommsBuffers echo and the unit's own Version READ
    recorded, and the state burst starts arriving about 5 s in. So a listener
    registered on the client ``connect()`` hands back is already too late, which
    is the whole reason ``before_handshake`` exists.

    Confirmed on this unit (2026-08-12, CorOS 4.0.1 / d14e): 474 messages of 24
    distinct types by 15 s. The floors below sit well under that, because a test
    pinned to the exact tally would fail on a unit with a different number of
    presets rather than on a real regression.

    The recorder is already closed by the time any test runs - the connection
    fixture waits for the burst and then stops it - so this reads the burst itself
    and not the traffic other tests have provoked since.
    """
    names = handshake_burst.names()
    counted = {name: names.count(name) for name in sorted(set(names))}
    report = (f"recorded {len(names)} message(s), settled in "
              f"{handshake_burst.settled_in}s: {counted}")

    assert handshake_burst.closed, "the recorder was still running - see conftest"
    assert handshake_burst.settled_in is not None, (
        f"the seed preset never arrived, so the burst was cut off by the "
        f"fixture's patience rather than by finishing - {report}")
    assert len(names) >= 100, report
    assert len(counted) >= 15, f"too few distinct state types in the burst - {report}"
    # Nothing in the handshake REQUESTS these. The subscription is a burst of
    # fire-and-forget READs, so almost every message here is one _dispatch would
    # have dropped for want of a waiter.
    assert "FileMessage" in counted, report          # the folder enumeration
    assert "RecallPresetMessage" in counted, report  # the preset on the grid now


def test_a_listener_on_a_live_connection_hears_pushes_nobody_asked_for(qc):
    """The tempo stream is the cheapest proof: the unit sends it unprompted."""
    listening = Recorder()
    qc.add_listener(listening)
    try:
        assert _wait_until(lambda: listening.messages(), UNSOLICITED_PATIENCE), (
            f"nothing arrived in {UNSOLICITED_PATIENCE}s with no host request "
            f"outstanding. The metronome's tempo stream should be enough on its "
            f"own")
    finally:
        qc.remove_listener(listening)


def test_a_listener_does_not_take_the_reply_away_from_the_caller(qc):
    listening = Recorder()
    qc.add_listener(listening)
    try:
        reply = qc.version()
        assert reply.app_fw_version, "version() lost its reply to the listener"
        assert _wait_until(
            lambda: "VersionMessage" in listening.names(), 2.0), (
            f"the listener never saw the reply it did not consume; it saw "
            f"{listening.names()}")
    finally:
        qc.remove_listener(listening)


def test_removing_a_listener_stops_it_while_the_unit_keeps_pushing(qc):
    """Removal has to stop delivery, and the proof has to survive a quiet unit.

    A second listener registered at the moment the first is removed is what makes
    this honest: if it hears nothing either, the unit went quiet and the test says
    so instead of crediting the removal.
    """
    removed, still_listening = Recorder(), Recorder()
    qc.add_listener(removed)
    try:
        assert _wait_until(lambda: removed.messages(), UNSOLICITED_PATIENCE), \
            "the unit pushed nothing at all, so this test cannot say anything"
        qc.add_listener(still_listening)
        assert qc.remove_listener(removed) is True
        after_removal = len(removed.messages())

        assert _wait_until(lambda: len(still_listening.messages()) >= 2,
                           UNSOLICITED_PATIENCE), (
            "the unit stopped pushing during the window, so a frozen count "
            "proves nothing about removal")
        assert len(removed.messages()) == after_removal, \
            "a removed listener was still being called"
    finally:
        qc.remove_listener(removed)
        qc.remove_listener(still_listening)


def test_a_listener_may_not_read_from_the_device_on_a_real_link(qc):
    """The rule the design doc states, on the real RX thread.

    Offline this is a thread-identity check against a fake. Here it is the actual
    read loop of an actual connection, which is where a listener that tried to
    re-read would take the link down with it.
    """
    refusals = {}
    done = threading.Event()

    def tries_to_read(message):
        if refusals:
            return
        for name, attempt in (
            ("request", lambda: qc.version(timeout=0.5)),
            ("await_broadcast", lambda: qc._t.await_broadcast(
                pa.SceneMessage, lambda: None, timeout=0.5)),
            ("collect", lambda: qc._t.collect(pa.SceneMessage, lambda: None, 0.5)),
        ):
            try:
                attempt()
                refusals[name] = None
            except Exception as exc:               # noqa: BLE001 - the type is the point
                refusals[name] = exc
        done.set()

    qc.add_listener(tries_to_read)
    try:
        assert done.wait(UNSOLICITED_PATIENCE), \
            "no push arrived, so the listener never ran"
    finally:
        qc.remove_listener(tries_to_read)

    for name, exc in refusals.items():
        assert isinstance(exc, RuntimeError), f"{name} was not refused: {exc!r}"
        assert not isinstance(exc, TimeoutError), f"{name} waited instead of refusing"
        assert "RX thread" in str(exc)
    # The link is unharmed: a refusal costs one message's listener call, not the
    # connection.
    assert qc.version().app_fw_version

"""The hardware-in-the-loop suite's fixtures, and its restore contract.

ADR-0005: a successful run is **state-neutral** - everything the suite changed is
put back. A failed run restores as best it can and NAMES what it could not, so
the owner knows what to fix by hand. This is not a nicety: the only unit this
project has is one somebody gigs with.

Run it with::

    pytest tests/hardware --hardware

Without the flag nothing here is collected, so the offline suite stays honest
with no unit attached.
"""
import threading
import time

import pytest

def pytest_ignore_collect(collection_path, config):
    # Not merely skipped - not collected. A hardware test that silently "passes"
    # as a skip in an offline run is a test nobody notices has stopped running.
    return not config.getoption("--hardware")


class HandshakeBurst:
    """Records the type of every message the unit pushes DURING the connect burst.

    Attached by the connection fixture through
    ``protocol.connect(before_handshake=...)``, which is the only moment early
    enough to catch the burst - by the time ``connect`` returns, the burst has not
    even started.

    It stops recording and takes itself off the transport as soon as the burst is
    over, which is what makes the recording mean "the burst" rather than "the
    traffic so far". The metronome's tempo stream never stops, so a recorder left
    running would hold the whole run, and a test asserting on it would really be
    asserting on whatever other tests had provoked first. Stopping also keeps it
    out of the read path of the latency measurements in ``test_write_echo.py``,
    which are calibrated numbers.

    Removing a listener from inside a listener is safe by contract - see
    ``Transport.add_listener`` and ADR-0008.

    Runs on the RX thread, so it does the least it can: append and return.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._names = []
        self._detach = None
        self.closed = False
        self.settled_in = None  # seconds the burst took, or None if it timed out

    def attach(self, transport):
        """Register on ``transport``. Called before the handshake runs."""
        self._detach = transport.add_listener(self)

    def __call__(self, message):
        with self._lock:
            if self.closed:
                # The RX thread notifies from a snapshot, so a message can still
                # arrive after removal. It must not reopen the recording.
                return
            self._names.append(type(message).__name__)

    def record_until(self, sentinel, patience):
        """Record until a ``sentinel``-typed message arrives, then stop.

        The seed ``RecallPresetMessage`` is the tail of the burst - measured
        2026-08-12 on d14e: ModelRepo at 4.9 s, the folder listings and settings
        at 5.1 s, the current preset at 10.1 s - so waiting for it means the whole
        burst has been recorded, however long the unit takes about it.

        Stops on ``patience`` seconds regardless, so a unit that never sends it
        cannot hang the run. ``settled_in`` says which of the two happened.
        """
        started = time.monotonic()
        deadline = started + patience
        while time.monotonic() < deadline:
            if sentinel in self.names():
                self.settled_in = time.monotonic() - started
                break
            time.sleep(0.1)
        self.close()

    def close(self):
        """Stop recording and come off the transport. Idempotent."""
        with self._lock:
            already = self.closed
            self.closed = True
        if not already and self._detach is not None:
            self._detach()

    def names(self):
        """A snapshot of what has been recorded, in arrival order."""
        with self._lock:
            return list(self._names)


@pytest.fixture(scope="session")
def _connection():
    """The run's single connection, with the handshake burst recorded.

    One connection, because the handshake is expensive - and because the unit
    only lets one process hold the HID interface, so a test that opened a second
    one would fail on whatever order it ran in.

    This is a PROTOCOL-level suite, so it connects through
    :mod:`pyquadcortex.protocol` and gets a ``QuadCortex``.
    ``pyquadcortex.connect()`` returns the model's ``Device`` instead (ADR-0006).

    The burst recorder is attached for every run, not just the tests that read
    it, because it cannot be attached later on demand: the burst happens during
    ``connect``.

    The fixture then waits for the burst to finish before handing the connection
    over, so the recording is exactly the burst whatever order the tests run in.
    It costs about 8 s once per run and buys more than it costs: `connect()`
    returns roughly 3 s before the unit starts streaming several hundred messages,
    so without the wait every latency measurement in this suite would be taken on
    a link that is still busy answering the handshake.
    """
    from pyquadcortex import protocol
    burst = HandshakeBurst()
    with protocol.connect(before_handshake=burst.attach) as client:
        burst.record_until("RecallPresetMessage", patience=30.0)
        yield client, burst


@pytest.fixture(scope="session")
def qc(_connection):
    """The connected ``QuadCortex`` every test in this suite drives."""
    return _connection[0]


@pytest.fixture(scope="session")
def handshake_burst(_connection):
    """The :class:`HandshakeBurst` that listened through the connect handshake."""
    return _connection[1]


@pytest.fixture
def restores():
    """Register undo callables; they run in reverse, failure or not.

    Each entry is ``(description, callable)``. Anything that raises while
    restoring is collected and re-raised at the end as one failure naming every
    unrestored item, rather than the first one aborting the rest of the restore.
    """
    undo = []
    yield lambda description, fn: undo.append((description, fn))

    failed = []
    for description, fn in reversed(undo):
        try:
            fn()
            time.sleep(0.3)
        except Exception as exc:                     # noqa: BLE001 - reported, not swallowed
            failed.append(f"{description}: {exc!r}")
    if failed:
        raise AssertionError(
            "COULD NOT RESTORE THE UNIT - fix these by hand:\n  "
            + "\n  ".join(failed))

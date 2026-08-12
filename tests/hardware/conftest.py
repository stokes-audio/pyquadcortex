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
    """Records the type name of every message the unit pushes, from the start.

    Attached by the connection fixture through
    ``protocol.connect(before_handshake=...)``, which is the only moment early
    enough to catch the handshake's burst - by the time ``connect`` returns, the
    burst is over.

    Records NAMES rather than messages, and stops at ``LIMIT``. The metronome's
    tempo stream never stops, so an unbounded recorder on a connection that lives
    for the whole run would keep growing all run; the burst is only the first few
    hundred messages of it.

    Runs on the transport's RX thread, so it does the least it can: take the
    lock, append, return.
    """

    LIMIT = 4000

    def __init__(self):
        self._lock = threading.Lock()
        self._names = []
        self.dropped = 0

    def __call__(self, message):
        with self._lock:
            if len(self._names) < self.LIMIT:
                self._names.append(type(message).__name__)
            else:
                self.dropped += 1

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
    it: registering it costs one list append per message, and it cannot be
    attached later on demand, because the burst happens during ``connect``.
    """
    from pyquadcortex import protocol
    burst = HandshakeBurst()
    with protocol.connect(
        before_handshake=lambda transport: transport.add_listener(burst)
    ) as client:
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

"""The hardware-in-the-loop suite's fixtures, and its restore contract.

ADR-0005: a successful run is **state-neutral** - everything the suite changed is
put back. A failed run restores as best it can and NAMES what it could not, so
the owner knows what to fix by hand. This is not a nicety: the only unit this
project has is one somebody gigs with.

Run it with::

    pytest tests/hardware --hardware

Without the flag nothing here runs, so the offline suite stays honest with no
unit attached. That takes two hooks, not one: ``pytest_ignore_collect`` for the
paths pytest REACHES by walking the tree, and ``pytest_collection_modifyitems``
for a path named on the command line, which pytest never offers to
``pytest_ignore_collect`` at all.
"""
import pathlib
import threading
import time

import pytest

#: This directory. Everything under it drives the unit and is gated on the flag.
SUITE = pathlib.Path(__file__).resolve().parent
ROOT = SUITE.parent.parent


def pytest_ignore_collect(collection_path, config):
    # Not merely skipped - not collected. A hardware test that silently "passes"
    # as a skip in an offline run is a test nobody notices has stopped running.
    #
    # pytest consults this only for paths it reaches by RECURSION: `Dir.collect`
    # skips the call for anything `Session.isinitpath` claims, which is every
    # path given on the command line (`_pytest/main.py`, pytest 9.1.1). So this
    # covers `pytest`, `pytest tests/` and `pytest tests/hardware` - the last one
    # because the DIRECTORY is the initial path and its files still come through
    # here - and does nothing at all for `pytest tests/hardware/test_scales.py`.
    # The hook below catches that one.
    return not config.getoption("--hardware")


def _resolved(item):
    """An item's file, resolved, or ``None`` if it has no file.

    Resolved on both sides of the comparison below, because pytest builds a
    node's path with ``absolutepath``, which does NOT follow symlinks. An
    ABSOLUTE argument naming this directory through a link would then compare
    unequal to ``SUITE`` and the gate would quietly stop firing - measured, with
    this line left out: 28 hardware tests collected, exit 0. A relative argument
    is joined to the working directory, which the OS has already resolved, so
    that shape was never at risk. ``tests/test_hardware_gate.py`` runs the one
    that was.
    """
    path = getattr(item, "path", None)
    return None if path is None else path.resolve()


def pytest_collection_modifyitems(session, config, items):
    """Stop the run when a hardware test is named directly without the flag.

    pytest does not consult ``pytest_ignore_collect`` for a path given as a
    command-line argument - only for paths reached by walking a directory - so
    narrowing a run to one file used to walk straight past the gate. With a unit
    attached those tests RAN and drove it; with none attached they failed rather
    than being absent. The exemption is deliberate on pytest's side, so it is
    not a version quirk to wait out. ``--hardware`` is the flag that means "yes,
    touch my unit", and losing it without being told is the one thing this suite
    must not do.

    This hook does see explicitly-named paths, which is why the gate lives here
    as well. It raises rather than deselecting quietly: the developer asked for
    these tests by name, so the reason they did not run is owed to them, and a
    deselected count in a summary line is not that reason. Nothing here executes
    either way.
    """
    if config.getoption("--hardware"):
        return
    gated = sorted({
        str(path.relative_to(ROOT))
        for path in map(_resolved, items)
        if path is not None and path.is_relative_to(SUITE)
    })
    if not gated:
        return
    raise pytest.UsageError(
        "these tests drive a real Quad Cortex and need --hardware:\n  "
        + "\n  ".join(gated)
        + "\nRe-run with --hardware to drive the unit, or leave the path out."
    )


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
            if self._recorded(sentinel):
                self.settled_in = time.monotonic() - started
                break
            time.sleep(0.1)
        self.close()

    def close(self):
        """Stop recording and come off the transport. Idempotent.

        Runs on the caller's thread, from :meth:`record_until`. If you ever move
        the stop into :meth:`__call__` - closing the moment the sentinel lands,
        which is tempting - it has to happen OUTSIDE that method's ``with
        self._lock`` block: ``_lock`` is not reentrant, so closing from inside it
        deadlocks the RX thread permanently.
        """
        with self._lock:
            already = self.closed
            self.closed = True
        if not already and self._detach is not None:
            self._detach()

    def _recorded(self, name):
        """Whether a message of type ``name`` has been recorded.

        Scans in place rather than going through :meth:`names`, which would copy
        the whole recording on every poll, briefly contending with the RX thread
        at the busiest moment it has.
        """
        with self._lock:
            return name in self._names

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

    Two things are attached before the handshake, and neither can be attached
    later on demand, because the burst happens during ``connect``:

    * the burst recorder, for every run rather than only the tests that read it;
    * the model's state layer, which is what ``pyquadcortex.connect()`` does at
      exactly this point. It stays attached for the whole run, which costs the
      RX thread one small message copy per ``Version`` or ``PresetDirty`` push
      and nothing at all for anything else - orders of magnitude under the
      hundred-millisecond latencies ``test_write_echo.py`` measures. Its own
      tests are in ``test_model_state.py``.

    The fixture then waits for the burst to finish before handing the connection
    over, so the recording is exactly the burst whatever order the tests run in.
    It costs about 8 s once per run and buys more than it costs: `connect()`
    returns roughly 3 s before the unit starts streaming several hundred messages,
    so without the wait every latency measurement in this suite would be taken on
    a link that is still busy answering the handshake.
    """
    from pyquadcortex import protocol
    from pyquadcortex.device import entries
    from pyquadcortex.device.state import DeviceState

    burst = HandshakeBurst()
    cache = DeviceState()

    def subscribe(transport):
        burst.attach(transport)
        cache.listen_on(transport)

    with protocol.connect(before_handshake=subscribe) as client:
        cache.bind(client)
        burst.record_until("RecallPresetMessage", patience=30.0)
        # Taken here, before any test can read through the cache, so "the burst
        # warmed this" cannot later be confused with "some test read it".
        warmed = {entry.name: cache.cached(entry.name) for entry in entries.ENTRIES}
        try:
            yield client, burst, cache, warmed
        finally:
            cache.close()


@pytest.fixture(scope="session")
def qc(_connection):
    """The connected ``QuadCortex`` every test in this suite drives."""
    return _connection[0]


@pytest.fixture(scope="session")
def handshake_burst(_connection):
    """The :class:`HandshakeBurst` that listened through the connect handshake."""
    return _connection[1]


@pytest.fixture(scope="session")
def model_cache(_connection):
    """The model's ``DeviceState``, subscribed since before the handshake."""
    return _connection[2]


@pytest.fixture(scope="session")
def burst_warmed(_connection):
    """What each cache entry held once the burst finished, before any test ran."""
    return _connection[3]


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

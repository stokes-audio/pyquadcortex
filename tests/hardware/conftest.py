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

from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa

#: This directory. Everything under it drives the unit and is gated on the flag.
SUITE = pathlib.Path(__file__).resolve().parent
ROOT = SUITE.parent.parent

#: ``nodeid -> operation names`` for every collected hardware test, filled at
#: collection. ``pytest_runtest_logreport`` is handed a report and not an item,
#: so the markers have to be looked up by node id; building the map at
#: collection also means the report and the ``--verifies`` selection read the
#: same names.
_VERIFIES = {}

#: ``operation -> list of outcomes``, one entry per phase that decided a test.
_OUTCOMES = {}
#: Node ids that produced at least one report - the tests that actually RAN,
#: whatever deselected the rest (our --verifies, or pytest's own -k, which runs
#: after this conftest's hook). Claims are read from these at report time.
_RAN = set()


def pytest_ignore_collect(collection_path, config):
    # Not merely skipped - not collected. A hardware test that silently "passes"
    # as a skip in an offline run is a test nobody notices has stopped running.
    #
    # pytest consults this only for paths it reaches by RECURSION: `Dir.collect`
    # skips the call for anything `Session.isinitpath` claims, which is every
    # path given on the command line (`_pytest/main.py`, pytest 9.1.1). So this
    # covers `pytest`, `pytest tests/` and `pytest tests/hardware` - the last one
    # because the DIRECTORY is the initial path and its files still come through
    # here - and nothing at all for a file named outright, such as
    # `pytest tests/hardware/test_scales_on_unit.py`.
    # The hook below catches that one.
    return not config.getoption("--hardware")


def _resolved(item):
    """An item's file, resolved, or ``None`` if it has no file.

    Resolved on both sides of the comparison below, because pytest builds a
    node's path with ``absolutepath``, which does NOT follow symlinks. An
    ABSOLUTE argument naming this directory through a link would then compare
    unequal to ``SUITE`` and the gate would quietly stop firing - measured on
    ``tests/hardware/test_scales_on_unit.py`` with this line left out: its 28
    collected, exit 0. A relative argument
    is joined to the working directory, which the OS has already resolved, so
    that shape was never at risk. ``tests/test_hardware_gate.py`` runs the one
    that was.
    """
    path = getattr(item, "path", None)
    return None if path is None else path.resolve()


def _claims(items, operations, wanted, deselect):
    """What the tests that will RUN say they verify, keyed by node id.

    Narrows ``items`` in place to the tests naming ``wanted`` (when there is
    one), hands the rest to ``deselect``, and returns the claims of what is
    left. Narrowing here is what stops the other tests RUNNING under
    ``--verifies``; the report's ``claimed`` set is then built at the end from
    the tests that produced a report (:func:`_claimed`), because pytest's own
    ``-k`` deselects after this hook and a deselected test measured nothing.
    Held offline by ``tests/test_hardware_report.py``.
    """
    marks = {}
    for item in items:
        names = {n for m in item.iter_markers("verifies") for n in m.args}
        # Checked here rather than at run time so a renamed operation is a
        # collection error on every run, including the offline one in
        # tests/test_hardware_gate.py, rather than a marker that quietly
        # stops naming anything. Checked over EVERY collected test, not just
        # the surviving ones: a stale marker is a mistake in the suite, and
        # --verifies must not hide it.
        for name in sorted(names):
            if name not in operations:
                raise pytest.UsageError(
                    f"{item.nodeid}: verifies({name!r}) is not an operation")
        marks[item.nodeid] = names
    if wanted:
        # Both refusals are loud for the same reason the gate above is: a
        # `--verifies` nobody matches deselects EVERY test, and pytest reports
        # that as a green run of nothing. A typo and an untested operation look
        # identical from there, so each says which of the two it is and where
        # the answer is written down.
        if wanted not in operations:
            raise pytest.UsageError(
                f"--verifies {wanted!r} is not an operation; the names are the "
                f"ones in QuadCortex.operations()")
        keep = [item for item in items if wanted in marks[item.nodeid]]
        drop = [item for item in items if wanted not in marks[item.nodeid]]
        if not keep:
            raise pytest.UsageError(
                f"--verifies {wanted!r} is an operation, and no collected test "
                f"names it - this run would measure nothing. It should be in "
                f"UNMARKED_OPERATIONS in tests/test_hardware_markers.py, with "
                f"the reason no test drives it, or you named a path that "
                f"excludes the test carrying it")
        items[:] = keep
        deselect(drop)
    return {item.nodeid: marks[item.nodeid] for item in items}


def pytest_collection_modifyitems(session, config, items):
    """Stop the run when a hardware test is named directly without the flag.

    With ``--hardware`` it does the profile bookkeeping instead (ADR-0020):
    every ``verifies()`` name is checked against ``QuadCortex.operations()``,
    ``--verifies`` narrows the run to the tests that name one operation, and the
    names of what SURVIVES that are recorded for the end-of-run report (see
    :func:`_claims`). The check runs at COLLECTION
    so a marker naming an operation that no longer exists stops the run before
    the unit is touched, and ``tests/test_hardware_gate.py`` - which collects
    this tree offline with ``hid`` poisoned - sees it too.

    pytest does not consult ``pytest_ignore_collect`` for a path given as a
    command-line argument - only for paths reached by walking a directory - so
    narrowing a run to one file used to walk straight past the gate. With a unit
    attached those tests RAN and drove it; with none attached they failed rather
    than being absent. ``--hardware`` is the flag that means "yes, touch my
    unit", and losing it without being told is the one thing this suite must not
    do.

    That exemption is OBSERVED, not promised: it is in pytest's code (the
    ``isinitpath`` checks in ``Dir.collect``) and not in its hookspec, which says
    the hook is consulted for all files and directories. Read as behaviour rather
    than contract - and the direction of the risk is fine either way. If pytest
    ever matches its code to its docs, a named path becomes uncollected, this hook
    sees no items, and ``tests/test_hardware_gate.py`` fails on the exit code
    while the gate itself gets STRONGER.

    This hook does see explicitly-named paths, which is why the gate lives here
    as well. It raises rather than deselecting quietly: the developer asked for
    these tests by name, so the reason they did not run is owed to them, and a
    deselected count in a summary line is not that reason.

    No test runs either way. The named modules are imported first, since that is
    what collecting them means, and that is safe by a standing constraint rather
    than by luck: the hardware modules must stay import-safe offline (STEERING
    § 6), and nothing at their module scope touches a device.
    """
    if config.getoption("--hardware"):
        from pyquadcortex.protocol.client import QuadCortex

        wanted_profile = config.getoption("--profile")
        if wanted_profile:
            # Same reasoning as the --verifies checks above: fail at
            # collection, once, rather than in the `_connection` fixture where
            # every single test in the run would hit the same UsageError in
            # turn. The fixture still calls `_profile_named` itself - it is
            # cheap, and it is the one that must hand back the actual class -
            # this call exists only to fail the whole run before anything else
            # happens.
            _profile_named(wanted_profile)

        _VERIFIES.update(_claims(
            items, QuadCortex.operations(), config.getoption("--verifies"),
            lambda dropped: config.hook.pytest_deselected(items=dropped)))
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

    #: The four state messages that CLOSE the connect burst, in the order the
    #: unit sends them. The recording is complete when all four have arrived.
    #:
    #: They are one group, not a sequence with a last member to watch for:
    #: re-measured 2026-09-14 on d14e over three sessions, they land at about
    #: 11.1 s inside 3.6, 5.8 and 6.0 ms respectively, always in this order
    #: (``docs/protocol.md``, "The connect burst"). ``RecallPreset`` is
    #: the FIRST of them. Waiting for that one alone is what this used to do,
    #: and it cost two hardware tests a few runs in a hundred - most often by
    #: losing ``Scene``, which arrives last. See
    #: ``tests/test_handshake_burst_recorder.py``.
    #:
    #: Measured on ONE profile. ADR-0020 puts what differs by firmware or model
    #: on the profile class, and this has not earned that yet because there is
    #: one measurement of it; a ``--profile`` run against a Mini or a 4.1 that
    #: closes its burst differently will time out and name the message it never
    #: saw, which is the evidence that would move it.
    BURST_TAIL = ("RecallPresetMessage", "SetlistPositionMessage",
                  "PresetDirtyMessage", "SceneMessage")

    def __init__(self):
        self._lock = threading.Lock()
        self._names = []
        #: ``(action, frozenset of field names)`` for every ``Version`` seen,
        #: because since ADR-0020 a connect carries three of them and a test
        #: that only counts cannot tell a retried identity read from a changed
        #: handshake. Shapes only - the values are not this recorder's business.
        self._versions = []
        #: Type names seen, kept alongside ``_names`` so :meth:`missing` is a set
        #: difference rather than a walk of the whole recording. The recording
        #: reaches several hundred names and is polled while the RX thread is
        #: delivering ~1490 reports/s and wants this same lock to append.
        self._seen_types = set()
        self._detach = None
        self._sentinels = frozenset()
        self._missing_at_giveup = frozenset()
        self._patience = None
        self._called = False
        self._gave_up = False
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
            name = type(message).__name__
            self._names.append(name)
            self._seen_types.add(name)
            if isinstance(message, pa.VersionMessage):
                self._versions.append(
                    (message.action, frozenset(f.name for f, _ in message.ListFields())))

    def versions(self):
        """The shape of every ``Version`` recorded: ``(action, field names)``."""
        with self._lock:
            return list(self._versions)

    def record_until(self, sentinels, patience):
        """Record until EVERY type in ``sentinels`` has arrived, then stop.

        :data:`BURST_TAIL` is what the fixture passes. All four of them, not the
        first: the burst's shape is ModelRepo at 4.9 s, the folder listings and
        settings at 5.1 s, then those four together at about 11 s, and they are a
        group six milliseconds wide rather than a sequence. This loop polls at
        100 ms, so a stop condition naming only the first of the group stops at
        a uniformly random point in the 100 ms after it, and lands inside the
        group a few times in a hundred. When that happened the recorder came off the
        transport and the fixture snapshotted ``burst_warmed`` before the other
        three reached the cache, and the two tests reading that snapshot failed
        together while every test reading the live cache passed.

        Stops on ``patience`` seconds regardless, so a unit that never sends one
        cannot hang the run. ``settled_in`` says which of the two happened, and
        :meth:`unfinished` says it in words for a failure message.

        Both refusals below close the recorder on the way out, which is what the
        ``finally`` is for: this runs with the listener already attached, so a
        raise that skipped it would leave the recording on the transport for the
        whole session with nothing left to stop it.
        """
        self._called = True
        try:
            if isinstance(sentinels, str):
                raise TypeError(
                    "record_until takes a collection of type names, not one "
                    "name - the burst is over when all of them have arrived, "
                    "and a single name is the bug this signature replaced. A "
                    "str is a collection of its letters, so this would "
                    "otherwise wait for message types called 'R', 'e' and 'c' "
                    "and never settle.")
            # Materialized BEFORE the scan below: `sentinels` may be a
            # generator, and scanning it first would leave frozenset() an
            # exhausted one - refused as empty, for a non-empty argument.
            self._sentinels = frozenset(sentinels)
            wrong = sorted(repr(s) for s in self._sentinels
                           if not isinstance(s, str))
            if wrong:
                raise TypeError(
                    f"record_until matches NAMES against what the recorder "
                    f"stores, which is type(message).__name__; it was handed "
                    f"{', '.join(wrong)}. A message class never equals its own "
                    f"name, so the wait would burn the whole patience and then "
                    f"name that class as something the unit never sent.")
            if not self._sentinels:
                raise ValueError(
                    "record_until needs at least one type name. An empty "
                    "collection is satisfied by the first poll, so the recorder "
                    "would come off the transport before the burst began and "
                    "then report it as finished - which is the misattribution "
                    "this whole method exists to prevent.")
            self._patience = patience
            started = time.monotonic()
            deadline = started + patience
            while True:
                # ONE read of the condition per pass, used for both the settle
                # and the give-up. Reading it again after the deadline would let
                # the RX thread empty it in between, and :meth:`unfinished`
                # would then name nothing at all - a sentence with a blank where
                # the missing types belong, on a recording that is in fact
                # whole. Checking it BEFORE the deadline is what credits a tail
                # that lands during the last sleep.
                outstanding = self.missing()
                if not outstanding:
                    self.settled_in = time.monotonic() - started
                    break
                if time.monotonic() >= deadline:
                    # One last look, and this read decides BOTH branches. The
                    # RX thread can have emptied it since the read above, and
                    # freezing that stale set would name a message the recording
                    # holds. Whatever this read says is what gets reported.
                    outstanding = self.missing()
                    if not outstanding:
                        self.settled_in = time.monotonic() - started
                    else:
                        self._missing_at_giveup = outstanding
                        self._gave_up = True
                    break
                time.sleep(0.1)
        finally:
            self.close()

    def missing(self):
        """The sentinel types :meth:`record_until` has not seen yet.

        Reads the set :meth:`__call__` maintains rather than walking the
        recording: this is polled ten times a second at the busiest moment the
        link has, under the lock the RX thread needs to append.
        """
        with self._lock:
            return self._sentinels - self._seen_types

    def unfinished(self):
        """Why the recording is short of the whole burst, or ``None`` if it is not.

        The two tests that read ``burst_warmed`` for entries the burst delivers
        put this in front of their own assertions. Without it, "the cache never
        got ``PresetDirty``" and "the recorder stopped before ``PresetDirty``
        arrived" read identically from the snapshot, and they want opposite
        responses.

        Which of the two this sentence describes changed with the stop
        condition, and the wording follows it. The recorder now waits for every
        message in :data:`BURST_TAIL`, so it can no longer stop early - which
        leaves the unit as the only thing that can make this non-``None``.
        """
        if not self._called:
            return "the burst was never recorded: record_until was not called"
        if self.settled_in is not None:
            return None
        if not self._sentinels:
            return ("the burst was never recorded: record_until was called and "
                    "refused its argument - see the error it raised")
        if not self._gave_up:
            # Neither settled nor timed out, so the wait did not finish: a
            # KeyboardInterrupt during the burst is the way this happens. Said
            # plainly, because the branch below would otherwise name nothing at
            # all and blame the unit for it.
            return (f"the wait for the connect burst did not run to an end - "
                    f"interrupted, most likely. The recording holds what had "
                    f"arrived by then and is still short of "
                    f"{', '.join(sorted(self.missing())) or 'nothing'}; it says "
                    f"nothing about the unit either way.")
        return (
            f"the connect burst did not finish within {self._patience}s: "
            f"{', '.join(sorted(self._missing_at_giveup))} never arrived, of "
            f"the {len(self._sentinels)} messages that close it. The recorder "
            f"waits for all of them, so it did not stop early - what is absent "
            f"from the recording is absent from the unit's burst. Read it as a "
            f"finding about the unit or the link.")

    def tail_positions(self):
        """How far from the END of the recording each sentinel first appeared.

        The stop condition proves the four ARRIVED. It cannot prove they are the
        LAST four, which is what the fixture's snapshot on the next line
        actually rests on - and a firmware that appended a fifth closing message
        would re-open this bug with nothing to catch it. Recorded rather than
        asserted, because the number that would be asserted is one this project
        has measured on one profile: a sentinel sitting well back from the end
        shows up in the run's report before it shows up as a flake.
        """
        with self._lock:
            total = len(self._names)
            first = {}
            for index, name in enumerate(self._names):
                if name in self._sentinels and name not in first:
                    first[name] = total - index
            return first

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

    def names(self):
        """A snapshot of what has been recorded, in arrival order."""
        with self._lock:
            return list(self._names)


def _profile_named(name):
    """The profile class ``--profile NAME`` asks for (ADR-0020).

    This suite IS the instrument that measures a profile, so it has to be able
    to run against a unit the registry would refuse - a firmware in nobody's
    ``MEASURED_ON``, or a Mini. ``connect(profile=...)`` is the deliberate way
    to do that, and without an option for it the one suite that could produce
    the measurement was the one thing that could not be pointed at the unit.

    Resolved by class name over ``QuadCortex`` and everything registered under
    it, which is every profile there is: a subclass registers itself, and
    importing :mod:`pyquadcortex.protocol.profiles` is what puts the shipped
    ones in that list. An unknown name stops the run naming the valid ones,
    rather than connecting to somebody's unit as the wrong profile.
    """
    from pyquadcortex.protocol import profiles

    candidates = profiles._all_profiles()
    for cls in candidates:
        if cls.__name__ == name:
            return cls
    raise pytest.UsageError(
        f"--profile {name!r} is not a profile class; the profiles are: "
        + ", ".join(sorted(c.__name__ for c in candidates)))


@pytest.fixture(scope="session")
def _connection(request):
    """The run's single connection, with the handshake burst recorded.

    One connection, because the handshake is expensive - and because the unit
    only lets one process hold the HID interface, so a test that opened a second
    one would fail on whatever order it ran in.

    This is a PROTOCOL-level suite, so it connects through
    :mod:`pyquadcortex.protocol` and gets a ``QuadCortex``.
    ``pyquadcortex.connect()`` returns the model's ``Device`` instead (ADR-0006).

    Two things are attached before the handshake, and neither can be attached
    later on demand, because the burst happens during ``connect``:

    * the model's state layer FIRST - see ``subscribe`` below, where the order
      is load-bearing - which is what ``pyquadcortex.connect()`` does at
      exactly this point. It stays attached for the whole run, which costs the
      RX thread one small message copy per ``Version`` or ``PresetDirty`` push
      and nothing at all for anything else - orders of magnitude under the
      hundred-millisecond latencies ``test_write_echo.py`` measures. Its own
      tests are in ``test_model_state.py``;
    * the burst recorder, for every run rather than only the tests that read it.

    The fixture then waits for the burst to finish before handing the connection
    over, so the recording is exactly the burst whatever order the tests run in.
    It costs about 9 s once per run and buys more than it costs: `connect()`
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
        # The CACHE first, and the order is load-bearing. Listeners run in
        # registration order (``Transport._notify_listeners``), and the
        # recorder's stop condition is read one line below as "the cache has
        # these too". Registered the other way round the poll can see the
        # fourth tail message recorded and take the ``warmed`` snapshot before
        # ``DeviceState.apply_push`` has run for that same message - the stale
        # snapshot this file was just fixed for, one ``__call__`` wide instead
        # of six milliseconds. This way round the implication is real.
        cache.listen_on(transport)
        burst.attach(transport)

    # `--profile CLASSNAME` connects as that class instead of the one the unit's
    # identity resolves to, which is how a unit the registry would refuse - an
    # unmeasured firmware, or a Mini - gets measured by the suite that would
    # measure it. Without it, connect() refuses before a test can look.
    wanted = request.config.getoption("--profile")
    # EXPERIMENTAL always: on a new profile this suite IS the verification, and a
    # VERIFIED client would refuse everything before a test could look. On
    # QuadCortex it changes nothing.
    with protocol.connect(before_handshake=subscribe,
                          profile=_profile_named(wanted) if wanted else None,
                          support=protocol.Support.EXPERIMENTAL) as client:
        cache.bind(client)
        # Read by pytest_terminal_summary, which has a config and no fixtures.
        request.config._profile = type(client)
        # Read by pytest_terminal_summary: a cut-off burst is otherwise reported
        # only by the three tests that guard on it, and --verifies or a
        # single-file run deselects all three while the fixture still waits.
        request.config._burst = burst
        burst.record_until(HandshakeBurst.BURST_TAIL, patience=30.0)
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
def profile(qc):
    """The connected profile class (ADR-0020)."""
    return type(qc)


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


def _unrestored(failed):
    """The failure a restore that did not finish is reported as (ADR-0005).

    Written once, and called by both restore paths, because it is an
    instruction to the owner about their own unit: two spellings of it is two
    of them to keep right. ``tests/test_hardware_report.py`` holds the count.
    """
    return AssertionError(
        "COULD NOT RESTORE THE UNIT - fix these by hand:\n  "
        + "\n  ".join(failed))


def reload_loaded_preset(qc, before=None, away=6.0, settle=8.0,
                         scene_patience=5.0):
    """Reload the slot the unit is on, which is the only way to clear the flag.

    A write marks the preset edited, and writing the original value back is
    another write, so the per-test undo callables put the GRID right and leave
    the FLAG set. Only a recall clears it.

    Recalling the SAME slot does nothing: the unit sees no change. So this
    recalls a different slot first and comes back. ``position`` is a linear slot
    index, so the other slot is 0 or 1 rather than a neighbour - any slot the
    unit actually loads will do, and the checks below prove one did.

    ``before`` is the slot to come back to. A caller that knew it before the
    test ran passes it, so the reload returns to where the test STARTED rather
    than to wherever it ended, and the position check then fails if that slot
    will not load - emptied, or the setlist moved under it. The session teardown
    passes nothing and reloads whatever is loaded.

    A recall RESETS the active scene (``_A_RECALL_RESETS`` in
    ``device/entries.py``), so the scene is read first and put back after.

    A recall also DISCARDS unsaved edits, so every caller has to have
    established that the edits are this suite's own. ``preset_dirty_at_start``
    is how.

    ``away``, ``settle`` and ``scene_patience`` are the three waits, and only
    the offline tests in ``tests/test_hardware_report.py`` pass anything but the
    real ones.
    """
    before = qc.loaded_position() if before is None else before
    scene = qc.active_scene()
    other = 1 if before.position != 1 else 0
    qc.recall_preset(before.folder_key, other, is_factory=before.is_factory)
    time.sleep(away)
    qc.recall_preset(before.folder_key, before.position,
                     is_factory=before.is_factory)
    time.sleep(settle)
    # Confirmed, not sent and hoped for. A scene switch is not instantaneous -
    # `test_preset_surface.py` needs a watch with a 5 s timeout to see the unit
    # echo one - and a switch that never landed is the state-neutrality break
    # this line exists to prevent, silently.
    qc.switch_scene(scene)
    deadline = time.monotonic() + scene_patience
    back = qc.active_scene()
    while back != scene and time.monotonic() < deadline:
        time.sleep(0.2)
        back = qc.active_scene()
    assert back == scene, (
        f"the unit is on scene {back}, not {scene} where it started; the recall "
        f"reset it and the switch back did not land")
    now = qc.loaded_position()
    assert now.position == before.position, (
        f"the unit is on slot {now.position}, not {before.position} where the "
        f"reload was told to come back to")
    # Back on the right slot and still edited means the reload was a no-op -
    # the other slot was probably empty - and the writes are still on the grid.
    assert qc.preset_dirty(timeout=15.0) is False, (
        f"slot {before.position} is still showing unsaved edits, so the reload "
        f"did not take and the writes are still on the grid")


@pytest.fixture
def reload_the_loaded_preset(qc):
    """:func:`reload_loaded_preset` bound to the connection, as a callable.

    A fixture rather than an import: pytest keeps ONE module named ``conftest``
    and it is whichever DIRECTORY pytest walked into last, which is not the same
    as what it collected - a plain offline run collects no hardware test and
    imports this file anyway, for ``pytest_ignore_collect``. So the name is not
    a stable way to reach this module. Measured on pytest 9.1.1, four targets.

    The slot is read HERE, when the fixture is set up and before the test runs,
    so the reload comes back to where the test started rather than to wherever
    the test left the unit.
    """
    before = qc.loaded_position()
    return lambda: reload_loaded_preset(qc, before=before)


def _dirty_now(connection):
    """The edited flag, from the burst's own snapshot when it warmed one.

    ``PresetDirtyMessage`` is in :data:`HandshakeBurst.BURST_TAIL`, so the unit
    announces the flag during the connect burst and the cache has it before any
    test runs. Reading the snapshot costs no request, and ``preset_dirty``
    documents that the FIRST request after connecting is sometimes dropped -
    which, session-scoped, would fail every collected test rather than one.
    Falls back to the read when the burst warmed nothing.
    """
    held = connection[3].get("dirty")
    if held and "is_dirty" in held:
        return bool(held["is_dirty"])
    return connection[0].preset_dirty(timeout=15.0)


@pytest.fixture
def a_clean_preset(qc, preset_dirty_at_start, reload_the_loaded_preset):
    """A loaded preset with the edited flag clear, for a test that needs it to
    CHANGE.

    ``PresetDirty`` announces a change of the flag rather than an edit, so one
    transition is available per run and a test watching for it has to start
    from clear. It will not be: an undo is a write, so any earlier module that
    wrote a grid value leaves the flag set, and one sorts ahead of the module
    that wants this. Reloading is what clears it.

    Skips rather than reloads when the preset was already edited before the
    session began. Those edits are the owner's and a recall would discard them.
    """
    if preset_dirty_at_start:
        pytest.skip(
            "the loaded preset already had unsaved changes when this session "
            "started. Clearing them means a recall, which would throw the "
            "owner's work away. Save or reload the preset on the unit and run "
            "this again.")
    if qc.preset_dirty(timeout=15.0):
        reload_the_loaded_preset()


@pytest.fixture(scope="session")
def preset_dirty_at_start(_connection):
    """Whether the loaded preset had unsaved edits before any test ran.

    Whose edits they are decides what may be done with them. Edits already here
    are the OWNER's: nothing in this suite can put them back, so anything that
    restores by recalling refuses to run. Edits that appear later are the
    suite's own, and a recall is how they are cleared.

    This cannot see an edit the owner makes on the unit DURING a run; the suite
    already requires exclusive access, and the readme says not to touch it.
    """
    return _dirty_now(_connection)


@pytest.fixture(scope="session", autouse=True)
def _preset_left_as_found(_connection, preset_dirty_at_start):
    """Put the edited flag back, which a value restore cannot (ADR-0005).

    ADR-0005 promises a successful run leaves the unit exactly as it found it.
    The per-test ``restores`` callables cannot deliver that on their own: they
    write the original VALUE back, and a write is what marks the preset edited,
    so the grid ends right and the flag ends set. The unit was then handed back
    showing unsaved edits it did not start with.

    Runs once, after every test, and only when the preset was CLEAN at the
    start. Unsaved edits that were already there are the owner's and a recall
    would discard them.

    The decision is :func:`put_the_edited_flag_back`, which is a plain function
    so that ``tests/test_hardware_report.py`` can drive all four of its rows
    with no unit attached. It is the one call in this suite that can discard a
    person's unsaved work, so it is not taken on trust.
    """
    yield
    put_the_edited_flag_back(_connection[0], preset_dirty_at_start)


def put_the_edited_flag_back(qc, dirty_at_start, **reload):
    """Clear the edited flag when this suite is the one that set it.

    Four rows, and only the first one touches the unit:

    ===================  ==========  ===========================================
    dirty at start       dirty now   what happens
    ===================  ==========  ===========================================
    no                   yes         reload, which clears the flag
    yes                  either      nothing: the edits are the owner's
    no                   no          nothing: there is nothing to clear
    ===================  ==========  ===========================================

    The read is inside the try with the reload. A link that died during the run
    makes it raise, and that is exactly when the unit is left edited and the
    owner needs the agreed sentence rather than a traceback.
    """
    if dirty_at_start:
        return
    try:
        if not qc.preset_dirty(timeout=15.0):
            return
        reload_loaded_preset(qc, **reload)
    except Exception as exc:                         # noqa: BLE001 - reported, not swallowed
        raise _unrestored(
            [f"clear the edited flag this suite set on the loaded preset: {exc!r}"])


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
        raise _unrestored(failed)


#: The name every scratch copy is saved under. Fixed rather than unique per run
#: (spec section 4), so a leftover copy is recognisable on the unit's screen -
#: and so :func:`_scratch_slot` has one name to refuse.
SCRATCH_NAME = "pyquadcortex scratch"


def _scratch_slot(listing, name):
    """The free User slot to save the scratch copy into.

    Refuses a listing that already holds ``name``: ``delete_preset`` deletes BY
    NAME, so a leftover copy from an interrupted run plus a new one under the
    same name is ambiguous, and the fixture would be choosing which of the
    owner's presets to delete. The owner deletes the leftover instead - that
    they can see which is which is the point of the fixed name.
    """
    for entry in listing:
        if entry.name == name:
            pytest.fail(
                f"a preset called {name!r} is already in the User setlist - a "
                f"leak from an interrupted run. Delete it on the unit by hand "
                f"and re-run; this fixture deletes by name and will not guess "
                f"which copy is which.")
    for entry in listing:
        if not entry.name:
            return entry.index
    pytest.fail("the User setlist has no free slot for the scratch copy; "
                "free one on the unit and re-run")


def _release_scratch(qc, before, setlist, name, settle=3.0):
    """Put the unit back after :func:`scratch_preset`, whatever went wrong.

    The recall and the delete are INDEPENDENT: a recall that raises must not
    take the delete with it, or the copy stays behind under a name the next run
    refuses. Both failures are collected and reported in the same words
    ``restores`` uses, so a teardown that could not finish reads the same
    whichever fixture owned it.

    The copy may not be there at all: the fixture's ``try`` opens BEFORE the
    save, so a save that failed outright runs this teardown with nothing to
    delete. That is fine to call anyway - ``delete_preset`` goes through
    ``_file_operation``, which tolerates the device not replying and returns
    ``None`` rather than raising, so deleting a name the unit does not have is
    a no-op, not an error. A listing was tried here first as a guard, but
    ``list_presets`` is itself documented as unreliable on a single READ
    (:meth:`~pyquadcortex.protocol.client.QuadCortex.list_presets`), so a flaky
    listing could skip a delete that would have worked and leave the copy
    behind under the fixed scratch name. Call it unconditionally instead and
    let device state be the arbiter, same as everywhere else in this module.

    ``settle`` is the pause after the recall, and only the offline test in
    ``tests/test_hardware_report.py`` passes anything but the real 3 s.
    """
    failed = []
    try:
        qc.recall_preset(before.folder_key, before.position)
        time.sleep(settle)
    except Exception as exc:                         # noqa: BLE001 - reported, not swallowed
        failed.append(
            f"recall the preset that was loaded "
            f"({before.folder_key}, {before.position}): {exc!r}")
    try:
        qc.delete_preset(setlist, name)
    except Exception as exc:                         # noqa: BLE001 - reported, not swallowed
        failed.append(
            f"delete the scratch preset {name!r} from the User setlist: {exc!r}")
    if failed:
        raise _unrestored(failed)


@pytest.fixture
def scratch_preset(qc, preset_dirty_at_start):
    """A disposable copy of the loaded preset in a free User slot.

    Yields ``(folder_key, position, name)``. Teardown recalls the original slot
    and deletes the copy, so a test that must edit, save or undo never touches
    one of the owner's presets. Requires the loaded preset to be clean.
    """
    from pyquadcortex.protocol import Setlist
    before = qc.loaded_position()
    # The same question `restored` asks, for the same reason: this saves a copy
    # of the grid and recalls the original afterwards, so edits that were the
    # owner's would be captured into the copy and then discarded from the slot.
    # Edits a previous test made are the suite's own and go the same way.
    assert preset_dirty_at_start is False, (
        "the loaded preset already had unsaved edits when this session started; "
        "save or reload it on the unit and run again")
    free = _scratch_slot(qc.list_presets(Setlist.USER, include_empty=True),
                         SCRATCH_NAME)
    # The try opens BEFORE the save, because the save is itself a way to leave
    # a copy behind: it writes and then waits for the unit to confirm, so a
    # confirm that times out raises with the copy already in the owner's User
    # setlist under the fixed name - which makes the next run's delete-by-name
    # ambiguous, the leak _release_scratch exists to prevent. Everything that
    # can leave the copy there is therefore inside the block whose finally
    # deletes it, and _release_scratch tolerates there being nothing to delete.
    try:
        stored = qc.save_current_preset(Setlist.USER, free, SCRATCH_NAME,
                                        confirm=True, confirm_timeout=30.0)
        assert stored == SCRATCH_NAME
        qc.recall_preset(Setlist.USER, free)
        time.sleep(3.0)
        yield Setlist.USER, free, SCRATCH_NAME
    finally:
        _release_scratch(qc, before, Setlist.USER, SCRATCH_NAME)


def _decides(when, outcome):
    """Whether one phase report decides the operations its test names.

    A ``call`` report always counts - that is the test running. A ``setup`` or
    ``teardown`` counts only when it did NOT pass, because a passing one says
    nothing the ``call`` report has not already said.

    Teardown is the half that was missing and it is the important half: both
    ``restores`` and ``scratch_preset`` report a restore they could not finish
    there and nowhere else (see :func:`_unrestored`), so ignoring it printed the
    operation under ``passed:`` - and offered it as a ``VERIFIED`` candidate -
    on a run that left the owner's unit changed.
    """
    return when == "call" or outcome != "passed"


def pytest_runtest_logreport(report):
    """Record how each operation's tests came out, for the end-of-run report."""
    _RAN.add(report.nodeid)
    names = _VERIFIES.get(report.nodeid)
    if not names:
        return
    if _decides(report.when, report.outcome):
        for name in names:
            _OUTCOMES.setdefault(name, []).append(report.outcome)


def _report_lines(cls, outcomes, claimed):
    """The end-of-run report, as ``(label, names, note)`` rows.

    Pure, so ``tests/test_hardware_report.py`` holds the arithmetic with no unit
    attached. ``claimed`` is every operation some test that RAN says it
    verifies (:func:`_claimed`), and it is what keeps the last line readable: ``QuadCortex``
    verifies EVERYTHING, so the plain difference against ``VERIFIED`` names all
    ~89 operations no test has ever driven, each tagged as a regression. Only
    something a test claims can regress - nothing else was measured - and only
    a FAILED test regresses it; a skipped one measured nothing.
    """
    from pyquadcortex.protocol.support import EVERYTHING

    passed = {op for op, seen in outcomes.items()
              if seen and all(o == "passed" for o in seen)}
    not_passed = {op for op, seen in outcomes.items()
                  if any(o != "passed" for o in seen)}
    # A SKIP is a test that declined to measure - a precondition the loaded
    # preset did not meet, an operator-only capture - and says nothing about
    # the unit. Only a FAILURE is a regression. Measured on the first post-merge
    # run (2026-09-07): the bypass echo test skipped for want of a stored bypass
    # entry and the old line called set_bypass a regression.
    # pytest reports a setup or teardown ERROR with outcome "failed" too, so an
    # errored test lands here as well (checked against pytest 9.1.1).
    failed = {op for op, seen in outcomes.items() if "failed" in seen}
    verified = (set(cls.operations()) if cls.VERIFIED is EVERYTHING
                else set(cls.VERIFIED))
    return [
        ("passed", sorted(passed), ""),
        ("failed or skipped", sorted(not_passed), ""),
        ("passed, not VERIFIED", sorted(passed - verified),
         "<- candidates to add"),
        ("VERIFIED and claimed by a test, failed",
         sorted(verified & claimed & failed), "<- regressions by name"),
    ]


def _claimed(verifies, ran):
    """The operations claimed by tests that actually ran.

    ``verifies`` maps node id to the names its marker carries; ``ran`` is the
    node ids that produced a report. Built at report time rather than at
    collection because pytest's own ``-k`` deselection runs AFTER this
    conftest's hook, so a `-k` run that kept only unmarked tests still had
    every marked operation counted as claimed - and the report then said
    "NOTHING PASSED" about a run that measured nothing on purpose (seen on the
    unit, 2026-09-07). Pure, held offline.
    """
    return set().union(*(names for nodeid, names in verifies.items()
                         if nodeid in ran)) if verifies else set()


def _measured_nothing(outcomes, claimed):
    """True when tests claimed operations and not one of them passed.

    The regression line is empty on a healthy run AND on a run where every
    test skipped its precondition, so an empty line alone cannot be read as
    clean. Pure, held offline like :func:`_report_lines`.
    """
    passed = {op for op, seen in outcomes.items()
              if seen and all(o == "passed" for o in seen)}
    return bool(claimed) and not passed


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print which operations this run measured on the connected profile.

    The suite IS the instrument (ADR-0020): what passed here is what a profile
    may list in ``VERIFIED``, and the two differences are the two things a
    maintainer wants - what has newly earned a place, and what has lost one.
    """
    if not config.getoption("--hardware"):
        return
    tr = terminalreporter
    cls = getattr(config, "_profile", None)
    if cls is None:
        # The session never connected. pytest's own summary carries the setup
        # errors; this line exists so the ABSENCE of the operations report is
        # never mistaken for "nothing to report".
        tr.section("operations: no report")
        tr.line("the hardware session never connected, so no operation was measured; "
                "see the setup errors above")
        return
    burst = getattr(config, "_burst", None)
    unfinished = None if burst is None else burst.unfinished()
    if unfinished is not None:
        tr.section("connect burst: cut off")
        tr.line(unfinished)
    claimed = _claimed(_VERIFIES, _RAN)
    lines = _report_lines(cls, _OUTCOMES, claimed)
    width = max(len(label) for label, _names, _note in lines)
    # pytest files a setup or teardown failure under "error", not "failed"
    # (_pytest/runner.py pytest_report_teststatus), and a restore that could
    # not finish is exactly that shape - so the count names errors separately
    # rather than losing them. The operations lines are unaffected: they read
    # report.outcome, which is "failed" for an error too.
    counts = {k: len(tr.stats.get(k, ()))
              for k in ("passed", "failed", "error", "skipped")}
    tr.section(f"operations on {cls.__name__} "
               f"(CorOS {', '.join(cls.MEASURED_ON)}, {cls.EVIDENCE.name})")
    tr.line(f"tests: {counts['passed']} passed, {counts['failed']} failed, "
            f"{counts['error']} errored, {counts['skipped']} skipped; "
            f"operations measured: {len(_OUTCOMES)} of {len(claimed)} claimed")
    for label, names, note in lines:
        tr.line(f"{label + ':':<{width + 1}} {names} ({len(names)})   {note}".rstrip())
    if _measured_nothing(_OUTCOMES, claimed):
        tr.line("NOTHING PASSED: every claimed operation failed or skipped, so the "
                "empty regression line above says nothing about the unit")

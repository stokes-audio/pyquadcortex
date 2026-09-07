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

#: ``nodeid -> operation names`` for every collected hardware test, filled at
#: collection. ``pytest_runtest_logreport`` is handed a report and not an item,
#: so the markers have to be looked up by node id; building the map at
#: collection also means the report and the ``--verifies`` selection read the
#: same names.
_VERIFIES = {}

#: ``operation -> list of outcomes``, one entry per phase that decided a test.
_OUTCOMES = {}


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
    left. The order matters and is the whole point: ``claimed`` in the
    end-of-run report is the union of this, and a deselected test measured
    nothing - recording it printed every operation the run never touched under
    ``VERIFIED and claimed by a test, not passed``, which reads as a
    regression. Held offline by ``tests/test_hardware_report.py``.
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
        path.relative_to(ROOT).as_posix()
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
def scratch_preset(qc):
    """A disposable copy of the loaded preset in a free User slot.

    Yields ``(folder_key, position, name)``. Teardown recalls the original slot
    and deletes the copy, so a test that must edit, save or undo never touches
    one of the owner's presets. Requires the loaded preset to be clean.
    """
    from pyquadcortex.protocol import Setlist
    before = qc.loaded_position()
    assert qc.preset_dirty() is False, "the loaded preset has unsaved edits; save or reload it first"
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
    names = _VERIFIES.get(report.nodeid)
    if not names:
        return
    if _decides(report.when, report.outcome):
        for name in names:
            _OUTCOMES.setdefault(name, []).append(report.outcome)


def _report_lines(cls, outcomes, claimed):
    """The end-of-run report, as ``(label, names, note)`` rows.

    Pure, so ``tests/test_hardware_report.py`` holds the arithmetic with no unit
    attached. ``claimed`` is every operation some COLLECTED test says it
    verifies, and it is what keeps the last line readable: ``QuadCortex``
    verifies EVERYTHING, so the plain difference against ``VERIFIED`` names all
    ~89 operations no test has ever driven, each tagged as a regression. Only
    something a test claims can regress - nothing else was measured.
    """
    from pyquadcortex.protocol.support import EVERYTHING

    passed = {op for op, seen in outcomes.items()
              if seen and all(o == "passed" for o in seen)}
    failed = {op for op, seen in outcomes.items()
              if any(o != "passed" for o in seen)}
    verified = (set(cls.operations()) if cls.VERIFIED is EVERYTHING
                else set(cls.VERIFIED))
    return [
        ("passed", sorted(passed), ""),
        ("failed or skipped", sorted(failed), ""),
        ("passed, not VERIFIED", sorted(passed - verified),
         "<- candidates to add"),
        ("VERIFIED and claimed by a test, not passed",
         sorted((verified & claimed) - passed), "<- regressions by name"),
    ]


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    """Print which operations this run measured on the connected profile.

    The suite IS the instrument (ADR-0020): what passed here is what a profile
    may list in ``VERIFIED``, and the two differences are the two things a
    maintainer wants - what has newly earned a place, and what has lost one.
    """
    if not config.getoption("--hardware"):
        return
    cls = getattr(config, "_profile", None)
    if cls is None:          # the session never connected, so there is nothing to report
        return
    claimed = set().union(*_VERIFIES.values()) if _VERIFIES else set()
    lines = _report_lines(cls, _OUTCOMES, claimed)
    width = max(len(label) for label, _names, _note in lines)
    tr = terminalreporter
    tr.section(f"operations on {cls.__name__} "
               f"(CorOS {', '.join(cls.MEASURED_ON)}, {cls.EVIDENCE.name})")
    for label, names, note in lines:
        tr.line(f"{label + ':':<{width + 1}} {names}   {note}".rstrip())

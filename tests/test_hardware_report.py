"""The hardware suite's bookkeeping, checked offline.

``tests/hardware/conftest.py`` does three things no hardware test can see for
itself, because each one is about a run rather than about the unit:

* it decides which phase report DECIDES an operation. A restore that failed is
  reported in ``teardown`` and nowhere else (``restores`` and
  ``scratch_preset`` both raise there), so a rule that reads only ``call``
  prints an operation under ``passed:`` on a run that left the unit changed -
  which is the one outcome ADR-0005 exists to make loud;
* it works out the four lines of the end-of-run report. The regression line is
  set arithmetic, and on ``QuadCortex`` - whose ``VERIFIED`` is ``EVERYTHING`` -
  the naive difference names every operation nobody has ever tested;
* it puts the unit back after ``scratch_preset``. The recall and the delete are
  independent failures, and the delete is the one that must happen anyway: it
  removes the copy, and the copy's name is fixed, so a leak makes the NEXT run
  ambiguous.

None of that needs a unit, so it is held here rather than being taken on trust
until somebody plugs one in. The conftest is loaded by path under its own module
name, the way ``tests/test_handshake_burst_recorder.py`` reaches
``HandshakeBurst``: without ``--hardware`` there is no other way into that
directory from the offline suite, and pytest's own copy of the module is
untouched by this.
"""
import importlib.util
from pathlib import Path

import pytest

from pyquadcortex.protocol.support import EVERYTHING, Evidence

_HARDWARE_CONFTEST = (
    Path(__file__).resolve().parent / "hardware" / "conftest.py")


@pytest.fixture(scope="module")
def conftest():
    """The hardware suite's conftest, loaded by path."""
    spec = importlib.util.spec_from_file_location(
        "hardware_conftest_report", _HARDWARE_CONFTEST)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# --- which phase decides an operation ---------------------------------------

@pytest.mark.parametrize("when,outcome,decides", [
    ("setup", "passed", False),
    ("setup", "failed", True),
    ("setup", "skipped", True),
    ("call", "passed", True),
    ("call", "failed", True),
    ("call", "skipped", True),
    ("teardown", "passed", False),
    ("teardown", "failed", True),
    ("teardown", "skipped", True),
])
def test_a_phase_decides_an_operation_when_it_ran_it_or_broke(
        conftest, when, outcome, decides):
    """A passing setup or teardown says nothing; a failing one says everything.

    The teardown rows are the ones with a bug behind them: ``restores`` raises
    its ``COULD NOT RESTORE THE UNIT`` there, so a rule that ignored teardown
    reported the operation as passed on a run that left the unit changed.
    """
    assert conftest._decides(when, outcome) is decides


def test_a_failed_restore_is_not_a_pass(conftest):
    """End to end through the recording, in the order pytest reports the phases."""
    outcomes = {}
    for when, outcome in [("setup", "passed"), ("call", "passed"),
                          ("teardown", "failed")]:
        if conftest._decides(when, outcome):
            outcomes.setdefault("set_param", []).append(outcome)

    lines = dict(_named(conftest._report_lines(
        _Profile, outcomes, claimed={"set_param"})))
    assert "set_param" not in lines["passed"]
    assert "set_param" in lines["failed or skipped"]


# --- the end-of-run report ---------------------------------------------------

class _Profile:
    """A profile that has verified two operations and knows of four."""

    MEASURED_ON = ("4.0.1",)
    EVIDENCE = Evidence.MAINTAINER
    VERIFIED = frozenset({"set_param", "set_bypass"})

    @classmethod
    def operations(cls):
        return {"set_param", "set_bypass", "set_ir", "set_block"}


class _Everything(_Profile):
    """A profile like ``QuadCortex``: it claims every operation it has."""

    VERIFIED = EVERYTHING


def _named(lines):
    """``(label, names)`` pairs, with the label's trailing note dropped."""
    return [(label, names) for label, names, _note in lines]


def test_the_report_names_the_four_things_a_maintainer_asks_for(conftest):
    outcomes = {"set_param": ["passed"], "set_ir": ["passed"],
                "set_bypass": ["failed"]}
    lines = dict(_named(conftest._report_lines(
        _Profile, outcomes, claimed={"set_param", "set_ir", "set_bypass"})))

    assert lines["passed"] == ["set_ir", "set_param"]
    assert lines["failed or skipped"] == ["set_bypass"]
    # set_ir passed and the profile does not claim it: a candidate.
    assert lines["passed, not VERIFIED"] == ["set_ir"]
    # set_bypass is claimed by the profile, a test names it, and it FAILED.
    assert lines["VERIFIED and claimed by a test, failed"] == ["set_bypass"]


def test_one_failing_run_of_an_operation_sinks_all_of_them(conftest):
    """Two tests name an operation and one fails: it did not pass."""
    lines = dict(_named(conftest._report_lines(
        _Profile, {"set_param": ["passed", "failed"]}, claimed={"set_param"})))

    assert lines["passed"] == []
    assert lines["failed or skipped"] == ["set_param"]


def test_a_regression_is_only_an_operation_some_test_claims(conftest):
    """The line that was unreadable on the maintainer's own unit.

    ``QuadCortex.VERIFIED`` is ``EVERYTHING``, so the difference against
    ``operations()`` names all ~89 operations no test has ever driven, every
    one of them tagged as a regression. A regression is an operation a test
    SAYS it verifies and that did not pass; nothing else can regress, because
    nothing else was measured.
    """
    lines = dict(_named(conftest._report_lines(
        _Everything, {"set_param": ["failed"], "set_bypass": ["passed"]},
        claimed={"set_param", "set_bypass"})))

    # set_ir and set_block are VERIFIED here too - EVERYTHING says so - and no
    # test names either, so neither is a regression. Only set_param is.
    assert lines["VERIFIED and claimed by a test, failed"] == ["set_param"]
    assert lines["passed, not VERIFIED"] == [], (
        "a profile that verifies everything can have no candidates")


def test_a_skipped_test_is_not_a_regression(conftest):
    """Measured on the first post-merge run, 2026-09-06: the bypass echo test
    skipped ("no stored bypass entry") and the report called set_bypass a
    regression. A skip measured nothing; it belongs on the failed-or-skipped
    line and nowhere else."""
    lines = dict(_named(conftest._report_lines(
        _Everything, {"set_bypass": ["skipped"], "set_param": ["failed"]},
        claimed={"set_bypass", "set_param"})))

    assert lines["failed or skipped"] == ["set_bypass", "set_param"]
    assert lines["VERIFIED and claimed by a test, failed"] == ["set_param"]


def test_an_operation_no_test_claims_is_never_a_regression(conftest):
    """The 89. Nothing names them, so the run says nothing about them."""
    lines = dict(_named(conftest._report_lines(
        _Everything, {"set_param": ["passed"]}, claimed={"set_param"})))

    assert lines["VERIFIED and claimed by a test, failed"] == []


def test_a_profile_with_nothing_claimed_reports_nothing_as_a_regression(conftest):
    """``--verifies`` can deselect every marked test; that is not 105 regressions."""
    lines = dict(_named(conftest._report_lines(_Profile, {}, claimed=set())))

    assert all(names == [] for names in lines.values())


# --- what a run CLAIMS to verify ---------------------------------------------

class _Mark:
    def __init__(self, *args):
        self.args = args


class _Item:
    """A collected test, as far as the collection hook reads one."""

    def __init__(self, nodeid, *names):
        self.nodeid = nodeid
        self._marks = [_Mark(*names)] if names else []

    def iter_markers(self, name):
        return list(self._marks) if name == "verifies" else []


class _Config:
    """A `--hardware` config that records what the hook deselects."""

    def __init__(self, verifies=None, profile=None):
        self._options = {"--hardware": True, "--verifies": verifies, "--profile": profile}
        self.deselected = []
        self.hook = self

    def getoption(self, name):
        return self._options[name]

    def pytest_deselected(self, items):
        self.deselected.extend(items)


@pytest.fixture
def collection(conftest):
    """The hook's recording, emptied around each test that drives it."""
    conftest._VERIFIES.clear()
    yield conftest._VERIFIES
    conftest._VERIFIES.clear()


def test_every_collected_test_claims_its_operations(conftest, collection):
    items = [_Item("t.py::a", "set_param"), _Item("t.py::b", "set_bypass")]

    conftest.pytest_collection_modifyitems(None, _Config(), items)

    assert collection == {"t.py::a": {"set_param"}, "t.py::b": {"set_bypass"}}


def test_a_deselected_test_claims_nothing(conftest, collection):
    """`--verifies NAME` narrows the run, so it must narrow the report too.

    The recording used to happen before the deselection, so `claimed` was
    built from every COLLECTED test - and an operation whose test never ran
    was printed under `VERIFIED and claimed by a test, not passed`, which the
    report labels a regression. A run of one test reported ~104 of them.
    """
    items = [_Item("t.py::a", "set_param"), _Item("t.py::b", "set_bypass")]
    config = _Config(verifies="set_param")

    conftest.pytest_collection_modifyitems(None, config, items)

    assert [i.nodeid for i in items] == ["t.py::a"]
    assert [i.nodeid for i in config.deselected] == ["t.py::b"]
    assert collection == {"t.py::a": {"set_param"}}

    claimed = set().union(*collection.values())
    lines = dict(_named(conftest._report_lines(
        _Everything, {"set_param": ["passed"]}, claimed)))
    assert lines["VERIFIED and claimed by a test, failed"] == [], (
        "set_bypass was deselected, so this run measured nothing about it")


def test_a_verifies_naming_no_operation_stops_the_run(conftest, collection):
    """A typo deselected every test, and pytest reports that as a green run.

    Nothing ran, nothing was measured, and the summary line says so only in a
    deselected count nobody reads. The name is checked against the operations
    instead.
    """
    items = [_Item("t.py::a", "set_param")]

    with pytest.raises(pytest.UsageError) as caught:
        conftest.pytest_collection_modifyitems(
            None, _Config(verifies="set_paramm"), items)

    text = str(caught.value)
    assert "set_paramm" in text and "QuadCortex.operations()" in text


def test_a_verifies_no_test_names_says_where_that_is_recorded(conftest, collection):
    """A real operation with no test is not a typo, so it gets its own answer:
    the excuse list, which is where an operation no test drives has to appear."""
    items = [_Item("t.py::a", "set_param")]

    with pytest.raises(pytest.UsageError) as caught:
        conftest.pytest_collection_modifyitems(
            None, _Config(verifies="set_bypass"), items)

    text = str(caught.value)
    assert "set_bypass" in text
    assert "UNMARKED_OPERATIONS" in text
    assert "tests/test_hardware_markers.py" in text


def test_a_marker_naming_no_operation_stops_the_collection(conftest, collection):
    items = [_Item("t.py::a", "set_paramm")]

    with pytest.raises(pytest.UsageError, match="is not an operation"):
        conftest.pytest_collection_modifyitems(None, _Config(), items)


def test_an_unknown_profile_stops_collection_before_the_run_starts(conftest, collection):
    """`--profile` is validated here too, not only in the `_connection` fixture.

    The fixture's check would fire once per test, one UsageError per item in
    the run; checked at collection, a typo is one clean error before anything
    touches a unit - the same reasoning the `--verifies` checks above are
    built on.
    """
    items = [_Item("t.py::a", "set_param")]

    with pytest.raises(pytest.UsageError, match="is not a profile class"):
        conftest.pytest_collection_modifyitems(
            None, _Config(profile="QuadCortexMinni"), items)


# --- which profile the run connects as ---------------------------------------

def test_profile_names_resolve_to_their_class(conftest):
    """`--profile` is how the suite measures a unit the registry would refuse."""
    from pyquadcortex.protocol import client, profiles

    assert conftest._profile_named("QuadCortex") is client.QuadCortex
    assert conftest._profile_named("QuadCortexMini") is profiles.QuadCortexMini
    assert conftest._profile_named("QuadCortex41") is profiles.QuadCortex41


def test_an_unknown_profile_name_stops_the_run_naming_the_real_ones(conftest):
    """Naming a class that does not exist must not connect to somebody's unit."""
    with pytest.raises(pytest.UsageError) as caught:
        conftest._profile_named("QuadCortexMinni")
    text = str(caught.value)
    assert "QuadCortexMinni" in text
    assert "QuadCortex" in text and "QuadCortexMini" in text


# --- putting the unit back after scratch_preset ------------------------------

class _Position:
    """What ``loaded_position()`` hands back, as far as the teardown reads it."""

    folder_key = "user"
    position = 3


class _FakeQc:
    """The two methods the teardown calls, each able to fail on demand."""

    def __init__(self, recall_raises=None, delete_raises=None):
        self.calls = []
        self._recall_raises = recall_raises
        self._delete_raises = delete_raises

    def recall_preset(self, folder_key, position):
        self.calls.append(("recall", folder_key, position))
        if self._recall_raises is not None:
            raise self._recall_raises

    def delete_preset(self, setlist, name):
        self.calls.append(("delete", setlist, name))
        if self._delete_raises is not None:
            raise self._delete_raises


def test_the_scratch_copy_is_deleted_even_when_the_recall_fails(conftest):
    """The leak this fixture exists to prevent.

    A recall that raises used to skip the delete, so the copy stayed in the
    owner's User setlist under a FIXED name - and the next run's delete, which
    deletes by name, could no longer tell the two copies apart.
    """
    qc = _FakeQc(recall_raises=TimeoutError("no reply"))

    with pytest.raises(AssertionError) as caught:
        conftest._release_scratch(qc, _Position, "USER", "pyquadcortex scratch",
                                  settle=0.0)

    assert ("delete", "USER", "pyquadcortex scratch") in qc.calls
    assert "COULD NOT RESTORE THE UNIT" in str(caught.value)
    assert "TimeoutError" in str(caught.value)


def test_both_failures_are_named_at_once(conftest):
    qc = _FakeQc(recall_raises=TimeoutError("no reply"),
                 delete_raises=RuntimeError("refused"))

    with pytest.raises(AssertionError) as caught:
        conftest._release_scratch(qc, _Position, "USER", "pyquadcortex scratch",
                                  settle=0.0)

    message = str(caught.value)
    assert "TimeoutError" in message and "RuntimeError" in message
    assert "pyquadcortex scratch" in message


def test_a_clean_teardown_recalls_then_deletes_and_says_nothing(conftest):
    qc = _FakeQc()

    conftest._release_scratch(qc, _Position, "USER", "pyquadcortex scratch",
                              settle=0.0)

    assert qc.calls == [("recall", "user", 3),
                        ("delete", "USER", "pyquadcortex scratch")]


def test_a_copy_that_was_never_saved_is_not_reported_as_unrestored(conftest):
    """The fixture's `try` opens before the save, so the teardown runs on a
    failed save with nothing to delete. ``delete_preset`` goes through
    ``_file_operation``, which tolerates the device not replying and returns
    ``None`` instead of raising, so deleting a name the unit does not have is a
    no-op - there is no listing guard left to skip it, and none is needed."""
    qc = _FakeQc()

    conftest._release_scratch(qc, _Position, "USER", "pyquadcortex scratch",
                              settle=0.0)

    assert qc.calls == [("recall", "user", 3),
                        ("delete", "USER", "pyquadcortex scratch")]


def test_nothing_that_can_leave_the_copy_behind_sits_outside_the_try(conftest):
    """The `try:` opens BEFORE the save, not after it.

    The save is itself a way to leave a copy behind: it writes and then waits
    for the unit to confirm, so a confirm that times out raises with the copy
    already in the owner's User setlist under the fixed name. The save, the
    check on its name and the recall that follows must all be inside the block
    whose `finally` deletes it. Read from the source because the fixture needs
    a unit; the ORDER of the lines is the whole fix.
    """
    body = _HARDWARE_CONFTEST.read_text(encoding="utf-8").split(
        "def scratch_preset(")[1]
    lines = [line.strip() for line in body.splitlines()]
    opened = lines.index("try:")
    for guarded in ("stored = qc.save_current_preset(Setlist.USER, free, SCRATCH_NAME,",
                    "assert stored == SCRATCH_NAME",
                    "qc.recall_preset(Setlist.USER, free)"):
        assert lines.index(guarded) > opened, (
            f"{guarded!r} runs before the try: that deletes the copy")


def test_the_restore_wording_is_written_once(conftest):
    """``restores`` and ``scratch_preset`` report in the same words.

    Two spellings of the owner's instructions is two of them to keep right, so
    the source may carry the sentence exactly once - in the helper both call.
    """
    source = _HARDWARE_CONFTEST.read_text(encoding="utf-8")
    assert source.count("COULD NOT RESTORE THE UNIT") == 1
    assert "COULD NOT RESTORE THE UNIT" in str(conftest._unrestored(["x: y"]))


# --- choosing the scratch slot -----------------------------------------------

class _Entry:
    def __init__(self, index, name):
        self.index = index
        self.name = name


def test_the_scratch_slot_is_the_first_empty_one(conftest):
    listing = [_Entry(0, "Gig"), _Entry(1, ""), _Entry(2, "")]

    assert conftest._scratch_slot(listing, "pyquadcortex scratch") == 1


def test_a_leftover_scratch_preset_stops_the_run(conftest):
    """A copy from an interrupted run makes a delete-by-name ambiguous.

    The name is fixed by the spec, so the fixture cannot dodge the collision by
    picking another one; the owner is told to delete the leftover by hand.
    """
    listing = [_Entry(0, "pyquadcortex scratch"), _Entry(1, "")]

    with pytest.raises(pytest.fail.Exception, match="already"):
        conftest._scratch_slot(listing, "pyquadcortex scratch")


def test_a_full_user_setlist_stops_the_run(conftest):
    listing = [_Entry(0, "Gig"), _Entry(1, "Rehearsal")]

    with pytest.raises(pytest.fail.Exception, match="free slot"):
        conftest._scratch_slot(listing, "pyquadcortex scratch")

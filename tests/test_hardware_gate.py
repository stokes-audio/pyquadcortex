"""The ``--hardware`` gate holds for a path named on the command line.

ADR-0005's suite drives the only unit this project has, and ``--hardware`` is the
flag that means "yes, touch my unit". ``tests/hardware/conftest.py`` needs two
hooks to say that once, because pytest answers the question two different ways:

* a path pytest REACHES by walking the tree is offered to
  ``pytest_ignore_collect``, which vetoes it - so ``pytest`` and ``pytest tests/``
  collect nothing from that directory;
* a path NAMED on the command line is never offered to that hook at all. It was
  collected and then RAN: with a unit attached it drove the unit, and with none
  attached it failed rather than being absent. A developer narrowing a run to one
  file lost the gate without being told.

This file pins the second half, which is the half that rots silently - the first
half goes on working, so the suite stays green while the gate is gone. It checks
through a subprocess running the developer's own command rather than asserting
about the hook, because the hook is not what was wrong: pytest's choice of when
to call it was.

Nothing here can reach a unit even if the gate is broken. Every subprocess runs
with ``hid`` poisoned (see :func:`_poisoned_hid`), so a hardware test that
escapes the gate dies at ``connect()`` instead of driving somebody's amp. An
offline test may never touch the unit (ADR-0002), least of all this one.
"""
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
SUITE = ROOT / "tests" / "hardware"

#: Every module in the hardware suite, spelled the way a developer would type it.
#: Read from the directory rather than listed, so a module added tomorrow is gated
#: by this file the day it lands - #39's `test_settings_values.py` arrived that
#: way. Recursive on purpose: the conftest hook filters on
#: ``path.is_relative_to(SUITE)``, so it gates a module in a subdirectory too, and
#: a flat glob here would quietly stop covering what the hook covers.
MODULES = sorted(str(path.relative_to(ROOT))
                 for path in SUITE.rglob("test_*.py"))


def _pytest(poison, *args):
    """Run pytest from the repo root, exactly as a developer would.

    The environment is scrubbed rather than inherited, because every assertion in
    this file reads pytest's own words - ``no tests ran``, node-id lines, an
    ``ERROR`` prefix - and the environment can change all three. Two ways in:

    * ``PYTEST_ADDOPTS=--hardware`` is a plausible export for somebody in the
      middle of a hardware session, and it makes this file report the gate broken
      when it is not. Measured, at the 10 tests this file holds today: seven of
      them fail.
    * an output-rewriting plugin (pytest-sugar and friends) autoloads through
      entry points and would do the same. Not measured - nothing of the kind is
      installed here - so autoload is off on the reasoning that nothing in this
      file needs a plugin, rather than on a reading.
    """
    env = dict(os.environ)
    for leaks in ("PYTEST_ADDOPTS", "PYTEST_PLUGINS"):
        env.pop(leaks, None)
    env["PYTEST_DISABLE_PLUGIN_AUTOLOAD"] = "1"
    env["PYTHONPATH"] = os.pathsep.join(
        [str(poison), str(ROOT), env.get("PYTHONPATH", "")]).rstrip(os.pathsep)
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-p", "no:cacheprovider", *args],
        cwd=ROOT, capture_output=True, text=True, env=env)


@pytest.fixture(scope="module")
def _poisoned_hid(tmp_path_factory):
    """A directory holding a ``hid`` that refuses to import.

    ``session.open_device()`` imports ``hid`` lazily, so this is the last gate
    before the USB link. With it on the subprocess's path, a hardware test that
    got past the flag check errors at the connection instead of talking to the
    unit - the failure this file is looking for, with none of the side effects.
    """
    directory = tmp_path_factory.mktemp("no-hid")
    (directory / "hid.py").write_text(
        'raise ImportError("the offline suite may not open the unit")\n')
    return directory


@pytest.fixture(scope="module")
def collected_with_the_flag(_poisoned_hid):
    """What the suite offers WITH the flag - collection only, so no unit runs.

    This is the proof that the gate opens. Without it every other assertion here
    is satisfied by a gate that refuses the hardware suite unconditionally, which
    would be a different bug with the same green suite.
    """
    result = _pytest(_poisoned_hid, "--hardware", "--collect-only", "-q",
                     "tests/hardware")
    assert result.returncode == pytest.ExitCode.OK, (
        result.stdout + result.stderr)
    ids = [line.strip() for line in result.stdout.splitlines()
           if line.startswith("tests/hardware") and "::" in line]
    assert ids, result.stdout
    return ids


def test_there_are_hardware_modules_to_gate():
    """Guards the glob above: an empty list would pass everything vacuously."""
    assert MODULES, f"no test modules found under {SUITE}"
    assert (SUITE / "conftest.py").exists()


def test_naming_a_hardware_module_is_refused_without_the_flag(_poisoned_hid):
    """The developer's own command, run for real, with every module named.

    Not ``--collect-only``: the bug was that these tests RAN, so the check has to
    be a run. ``no tests ran`` is the assertion that they did not.
    """
    result = _pytest(_poisoned_hid, *MODULES)

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        "a hardware module named on the command line was not refused:\n"
        + result.stdout + result.stderr)
    assert "no tests ran" in result.stdout, result.stdout
    assert "--hardware" in result.stderr, result.stderr
    for module in MODULES:
        assert module in result.stderr, (
            f"the refusal does not name {module}:\n{result.stderr}")


def test_naming_a_single_hardware_test_is_refused_without_the_flag(
        _poisoned_hid, collected_with_the_flag):
    """The narrowest run there is - one node id - is refused too.

    This is the shape a developer reaches for when iterating on one failure, so
    it is the shape most likely to be typed without thinking about the flag.
    """
    result = _pytest(_poisoned_hid, collected_with_the_flag[0])

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        result.stdout + result.stderr)
    assert "no tests ran" in result.stdout, result.stdout
    assert "--hardware" in result.stderr, result.stderr


def test_collecting_a_hardware_module_is_refused_without_the_flag(
        _poisoned_hid):
    """``--collect-only`` is refused as well, and prints the list anyway.

    The exit code is the usage error and no test runs, but pytest emits the
    collect-only listing from a ``finally`` block, so the item names still reach
    stdout after the refusal. That is why `tests/hardware/readme.md` claims "does
    not run" for a named path rather than "is not collected": the stronger claim
    is only true of the paths reached by recursion, checked below.
    """
    result = _pytest(_poisoned_hid, "--collect-only", "-q", MODULES[0])

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        result.stdout + result.stderr)
    assert "--hardware" in result.stderr, result.stderr


def test_a_path_through_a_symlink_is_gated_too(_poisoned_hid, tmp_path):
    """The comparison is resolved on both sides, and that is load-bearing.

    pytest builds a node's path with ``absolutepath``, which does not follow
    symlinks, while the conftest knows itself through ``__file__``. Compare the
    two as they come and a path naming this directory through a link is not
    recognised as this directory, so the gate stops firing with nothing to show
    for it.

    An absolute argument is what reaches that state: a RELATIVE one is joined to
    the working directory, which the OS has already resolved, so both sides come
    out physical whatever the developer typed. Measured with the item side left
    unresolved, on ``tests/hardware/test_scales_on_unit.py`` rather than the one
    this test happens to name: its 28 tests collected, exit 0.
    """
    link = tmp_path / "linked-repo"
    link.symlink_to(ROOT)

    result = _pytest(_poisoned_hid, str(link / MODULES[0]))

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        "a hardware module named through a symlink was not refused:\n"
        + result.stdout + result.stderr)
    assert "no tests ran" in result.stdout, result.stdout
    assert "--hardware" in result.stderr, result.stderr


def test_recursion_still_collects_no_hardware_test(_poisoned_hid):
    """The half that already worked, kept working.

    ``pytest tests/`` walks into the directory and ``pytest_ignore_collect``
    vetoes every file, so here the stronger claim holds: not collected at all.
    """
    result = _pytest(_poisoned_hid, "tests/", "--collect-only", "-q")

    assert result.returncode == pytest.ExitCode.OK, (
        result.stdout + result.stderr)
    offered = [line for line in result.stdout.splitlines()
               if line.startswith("tests/hardware")]
    assert not offered, offered


def test_naming_the_directory_collects_nothing(_poisoned_hid):
    """``pytest tests/hardware`` - the readme's own example for the veto branch.

    A directory named on the command line is an initial path too, so the veto
    does not run for the DIRECTORY. Its files are reached by recursion from
    there, which is what saves this shape: the per-file veto still fires. Worth
    its own case because the two halves of the gate pass it back and forth.
    """
    result = _pytest(_poisoned_hid, "tests/hardware", "--collect-only", "-q")

    assert result.returncode == pytest.ExitCode.NO_TESTS_COLLECTED, (
        result.stdout + result.stderr)
    assert "no tests collected" in result.stdout, result.stdout


def test_one_hardware_path_refuses_the_whole_run(_poisoned_hid):
    """A mixed run is refused entirely, offline half included.

    The deliberate choice, pinned because it is the one a future reader is most
    likely to soften: it would be easy to drop the hardware items and run the
    rest, and that hands back a green run to somebody who asked to touch the unit
    and was not told they did not.

    Any offline/hardware pair does now. When this was written the two files had to
    be chosen with no shared basename, because a colliding pair died in collection
    before the gate was reached; the rename in this branch removed the last such
    pair, and ``test_the_whole_tree_collects_with_the_flag`` keeps it that way.
    """
    result = _pytest(_poisoned_hid, "tests/test_registry.py",
                     "tests/hardware/test_tempo_mode.py")

    assert result.returncode == pytest.ExitCode.USAGE_ERROR, (
        result.stdout + result.stderr)
    assert "no tests ran" in result.stdout, result.stdout
    assert "tests/hardware/test_tempo_mode.py" in result.stderr, result.stderr
    assert "tests/test_registry.py" not in result.stderr, (
        "the refusal names an offline path as if it were gated:\n"
        + result.stderr)


def test_the_whole_tree_collects_with_the_flag(_poisoned_hid):
    """`pytest --hardware` from the repo root can collect BOTH suites.

    The gate opening is worth nothing if what it opens cannot be collected, and
    that is not hypothetical: until 2026-08-28 this exited 2 with two collection
    errors, because `tests/hardware/test_scales.py` and `tests/test_scales.py`
    shared a basename with no ``__init__.py`` to tell them apart, and so did the
    two ``test_values.py``. pytest mapped each pair to one module name and refused
    the second. Only the documented `pytest tests/hardware --hardware` worked,
    which is why it went years unnoticed.

    The two hardware files were renamed rather than the tree being made a package:
    three offline modules do ``from waiting import ...``, which works only while
    pytest keeps putting ``tests/`` on ``sys.path`` - and it stops doing that the
    moment ``tests/`` becomes a package or a namespace package.

    So this is the test that makes the naming rule enforceable instead of
    remembered: a new hardware module whose basename an offline module already
    owns fails here, in the offline suite, on the run everybody does.

    Collection only, so no unit is involved.
    """
    result = _pytest(_poisoned_hid, "--hardware", "--collect-only", "-q")

    assert result.returncode == pytest.ExitCode.OK, (
        "`pytest --hardware` cannot collect the tree:\n"
        + result.stdout[-3000:] + result.stderr[-3000:])
    # Matched on pytest's own words for a collection failure, not on the
    # substring "error" - a test called `..._raises_keyerror` contains that.
    # Without the count: pytest writes "1 error during collection" in the
    # singular, so the plural spelling misses the single-collision case, which is
    # the one this test exists for.
    assert "during collection" not in result.stdout, result.stdout[-3000:]
    reported = [line for line in result.stdout.splitlines()
                if line.startswith("ERROR")]
    assert not reported, reported
    ids = [line.strip() for line in result.stdout.splitlines() if "::" in line]
    assert any(node.startswith("tests/hardware/") for node in ids), (
        "the hardware suite is not in a --hardware run of the whole tree")
    assert any(not node.startswith("tests/hardware/") for node in ids), (
        "the offline suite is not in a --hardware run of the whole tree")


def test_the_flag_opens_the_gate_for_every_module(collected_with_the_flag):
    """With ``--hardware``, every module in the directory is collected again."""
    for module in MODULES:
        assert any(node.startswith(module + "::")
                   for node in collected_with_the_flag), (
            f"{module} is not collected even with --hardware")

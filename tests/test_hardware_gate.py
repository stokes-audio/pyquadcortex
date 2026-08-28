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
#: Read from the directory rather than listed, so a module added tomorrow is
#: gated by this file the day it lands.
MODULES = sorted(str(path.relative_to(ROOT))
                 for path in SUITE.glob("test_*.py"))


def _pytest(poison, *args):
    """Run pytest from the repo root, exactly as a developer would."""
    env = dict(os.environ)
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
    out physical whatever the developer typed. Measured on this checkout with the
    item side left unresolved: 28 hardware tests collected, exit 0.
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


def test_the_flag_opens_the_gate_for_every_module(collected_with_the_flag):
    """With ``--hardware``, every module in the directory is collected again."""
    for module in MODULES:
        assert any(node.startswith(module + "::")
                   for node in collected_with_the_flag), (
            f"{module} is not collected even with --hardware")

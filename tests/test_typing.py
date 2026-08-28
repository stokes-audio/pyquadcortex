"""The STATIC half of the unit check (ADR-0018).

`tests/test_values.py` proves the runtime refuses a wrong unit. This proves a
type checker refuses one BEFORE the code runs, wherever the caller names the
parameter with a generated constant.

It works by running mypy over `tests/typing/wrong_units.py` and comparing the
lines it complains about against the `# want: error` markers in that file. Both
directions matter and both have failed in development: a check that stops
catching a mistake, and one that starts rejecting a correct call.
"""

import pathlib
import re
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
CASES = ROOT / "tests" / "typing" / "wrong_units.py"


def _mypy_available():
    try:
        import mypy  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _mypy_available(),
    reason="mypy is in the dev extra; CI installs it and runs this")


def _wanted():
    """Line numbers marked `# want: error`, and the total line count."""
    lines = CASES.read_text().splitlines()
    return ({i for i, line in enumerate(lines, 1) if "# want: error" in line},
            len(lines))


def _reported():
    """Line numbers mypy reports an error on, and its whole output.

    Matched on the FILE NAME rather than the path it was given: mypy prints
    paths relative to its working directory, so anchoring on the absolute path
    matched nothing and every check below passed while catching nothing. The
    `test_there_are_cases_to_check` guard above exists for the same reason.
    """
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--no-error-summary",
         "--no-incremental", str(CASES)],
        capture_output=True, text=True, cwd=ROOT)
    return {int(m.group(1)) for m in
            re.finditer(rf"^\S*{re.escape(CASES.name)}:(\d+): error:",
                        result.stdout, re.M)}, result.stdout


def test_there_are_cases_to_check():
    """A file that stopped being found would make every check below vacuous."""
    wanted, total = _wanted()
    assert CASES.exists() and total > 30
    assert len(wanted) >= 7


def test_every_wrong_unit_is_rejected_before_it_runs():
    wanted, _ = _wanted()
    reported, output = _reported()
    missed = sorted(wanted - reported)
    assert not missed, (
        f"the type checker accepted a call it should reject, at "
        f"{CASES.name} lines {missed}. Static unit checking is not doing its "
        f"job for those.\n{output}")


def test_no_correct_call_is_rejected():
    wanted, _ = _wanted()
    reported, output = _reported()
    wrong = sorted(reported - wanted)
    assert not wrong, (
        f"the type checker rejected a call that is correct, at "
        f"{CASES.name} lines {wrong}. A checker that cries wolf gets turned "
        f"off.\n{output}")

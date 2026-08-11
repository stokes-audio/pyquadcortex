"""ADR-0002: nothing in the package imports `hid` at module scope.

`hid` is a ctypes binding that needs the native hidapi library, so a module-scope
import of it would break `import pyquadcortex`, `qcctl --help`, CI, and this whole
suite on any machine without hidapi installed. The one legitimate `import hid`
lives inside `session.open_device()`.

The check runs in a subprocess so it cannot be fooled by a fake `hid` another
test left in `sys.modules`, and it walks every module in the package so a new
one - in the model or in the protocol layer - is covered the day it is added.
"""
import pathlib
import pkgutil
import subprocess
import sys

import pytest

import pyquadcortex

ROOT = pathlib.Path(__file__).resolve().parent.parent

def _reraise(name):
    """walk_packages swallows import errors by default.

    A subpackage whose ``__init__`` raises would then drop every module under it
    from the list below, and this file would report a clean sweep of a package it
    never opened.
    """
    raise


MODULES = sorted(
    info.name
    for info in pkgutil.walk_packages(pyquadcortex.__path__, "pyquadcortex.",
                                      onerror=_reraise)
)


def test_the_walk_found_both_namespaces():
    """Guards the parametrisation: an empty walk would pass vacuously."""
    assert any(m.startswith("pyquadcortex.protocol.") for m in MODULES)
    assert any(m.startswith("pyquadcortex.model") for m in MODULES)


@pytest.mark.parametrize("module", ["pyquadcortex"] + MODULES)
def test_importing_the_module_does_not_pull_in_hid(module):
    script = (
        "import sys, importlib\n"
        f"importlib.import_module({module!r})\n"
        "assert 'hid' not in sys.modules, "
        f"{module!r} + ' imports hid at module scope'\n"
    )
    result = subprocess.run([sys.executable, "-c", script], cwd=ROOT,
                            capture_output=True, text=True)
    assert result.returncode == 0, result.stderr

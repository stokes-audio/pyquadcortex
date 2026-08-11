"""The two namespaces: the model at the top, the protocol layer one deeper.

`pyquadcortex.connect()` returns the model's `Device`; `pyquadcortex.protocol`
holds the message-level API this library shipped through 0.40.0, unchanged
except for where it is imported from (ADR-0006).

The parity test below is the one that matters. It reads the *actual* pre-flip
`__init__.py`, taken verbatim from git at the last release before the flip
(94e5053, 0.40.0)::

    git show 94e5053:pyquadcortex/__init__.py \\
        > tests/fixtures/surface/pre_flip_init.py.txt

and asserts every name that file exported now resolves under
`pyquadcortex.protocol`. Nothing here is a hand-typed list, so a name dropped
during the move cannot be hidden by forgetting to add it to a checklist.
"""
import ast
import pathlib

import pytest

import pyquadcortex
from pyquadcortex import protocol

PRE_FLIP_INIT = (pathlib.Path(__file__).resolve().parent
                 / "fixtures" / "surface" / "pre_flip_init.py.txt")


def _pre_flip_exports() -> list[str]:
    """The `__all__` of the package as it was before the flip."""
    tree = ast.parse(PRE_FLIP_INIT.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "__all__" for t in node.targets)):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"no __all__ found in {PRE_FLIP_INIT}")


PRE_FLIP_EXPORTS = _pre_flip_exports()


def test_the_pre_flip_snapshot_is_the_whole_surface():
    """Guards the fixture itself: a truncated snapshot would pass vacuously."""
    assert len(PRE_FLIP_EXPORTS) > 50
    assert "QuadCortex" in PRE_FLIP_EXPORTS


@pytest.mark.parametrize("name", PRE_FLIP_EXPORTS)
def test_every_pre_flip_name_resolves_under_protocol(name):
    assert hasattr(protocol, name), (
        f"{name} was exported by pyquadcortex before the flip and must be "
        f"reachable as pyquadcortex.protocol.{name}"
    )


def test_protocol_still_declares_the_whole_pre_flip_surface():
    """Reachable is not enough - it has to stay the documented surface."""
    assert set(PRE_FLIP_EXPORTS) <= set(protocol.__all__)


def test_top_level_no_longer_re_exports_the_protocol_surface():
    """The flip is a break, not an alias. QuadCortex is one import deeper now."""
    assert not hasattr(pyquadcortex, "QuadCortex")


def test_the_model_is_what_the_top_level_offers():
    assert pyquadcortex.connect is not protocol.connect
    assert set(pyquadcortex.__all__) >= {"connect", "Device", "protocol"}


PROTOCOL_SOURCES = sorted(
    pathlib.Path(protocol.__file__).parent.rglob("*.py"))


def test_the_protocol_sources_were_actually_found():
    """Guards the parametrisation below: an empty list would pass vacuously."""
    assert len(PROTOCOL_SOURCES) > 5


@pytest.mark.parametrize("source", PROTOCOL_SOURCES, ids=lambda p: p.name)
def test_the_protocol_layer_never_imports_the_model(source):
    """The model calls the protocol layer, never the other way round.

    A back-import would make the layer map a lie, turn the protocol layer's
    offline tests into model tests, and create an import cycle the day the model
    grows past a skeleton. Checked on the source rather than at runtime, because
    a lazy import inside a function would not show up in `sys.modules`.
    """
    tree = ast.parse(source.read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            if node.module.startswith("pyquadcortex.model"):
                offenders.append(node.module)
        elif isinstance(node, ast.Import):
            offenders += [a.name for a in node.names
                          if a.name.startswith("pyquadcortex.model")]
    assert not offenders, (
        f"{source.name} imports {offenders} - the protocol layer must not "
        f"depend on the model")

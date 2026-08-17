"""The two namespaces: the model at the top, the protocol layer one deeper.

`pyquadcortex.connect()` returns the model's `Device`; `pyquadcortex.protocol`
holds the message-level API this library shipped through 0.40.0, unchanged
except for where it is imported from (ADR-0006).

The parity test below is the one that matters. It reads a COMMITTED COPY of the
pre-flip `__init__.py`, taken verbatim from git at the last release before the
flip (94e5053, 0.40.0)::

    git show 94e5053:pyquadcortex/__init__.py \\
        > tests/fixtures/surface/pre_flip_init.py.txt

and asserts every name that file exported now resolves under
`pyquadcortex.protocol`. Nothing here is a hand-typed list, so a name dropped
during the move cannot be hidden by forgetting to add it to a checklist.

The fixture is a copy rather than a live `git show`, because CI checks the repo
out one commit deep (`actions/checkout@v4` defaults to `fetch-depth: 1`) and
94e5053 is not in that clone. A copy is only as good as what pins it, so the
fixture's exact size and content hash are asserted below: editing the fixture is
allowed, editing it quietly is not.
"""
import ast
import hashlib
import inspect
import pathlib

import pytest

import pyquadcortex
from pyquadcortex import device, protocol

PRE_FLIP_INIT = (pathlib.Path(__file__).resolve().parent
                 / "fixtures" / "surface" / "pre_flip_init.py.txt")

#: `git show 94e5053:pyquadcortex/__init__.py | shasum -a 256`
PRE_FLIP_INIT_SHA256 = (
    "045110e79c22eecff23e15568950f18e81b38438785e66c20e369120fd591645")

#: The package exported exactly this many names at 0.40.0.
PRE_FLIP_EXPORT_COUNT = 70


def _pre_flip_exports() -> list[str]:
    """The `__all__` of the package as it was before the flip."""
    tree = ast.parse(PRE_FLIP_INIT.read_text())
    for node in tree.body:
        if (isinstance(node, ast.Assign)
                and any(getattr(t, "id", None) == "__all__" for t in node.targets)):
            return list(ast.literal_eval(node.value))
    raise AssertionError(f"no __all__ found in {PRE_FLIP_INIT}")


PRE_FLIP_EXPORTS = _pre_flip_exports()


def test_the_pre_flip_snapshot_is_byte_for_byte_what_0_40_0_shipped():
    """Guards the fixture itself, which is this file's own standard.

    The fixture IS the list of names checked below, so editing it edits the
    test's yardstick - and the quickest route to green after a later refactor
    drops a name is to delete that name from here. Pinning the hash means the
    fixture and the export list can only be changed together and on purpose.
    """
    digest = hashlib.sha256(PRE_FLIP_INIT.read_bytes()).hexdigest()
    assert digest == PRE_FLIP_INIT_SHA256, (
        f"{PRE_FLIP_INIT.name} no longer matches "
        f"`git show 94e5053:pyquadcortex/__init__.py`. If the snapshot really "
        f"needed to change, re-take it with that command and update "
        f"PRE_FLIP_INIT_SHA256 and PRE_FLIP_EXPORT_COUNT in the same commit."
    )


def test_the_pre_flip_snapshot_is_the_whole_surface():
    """A truncated snapshot would make every check below pass vacuously."""
    assert len(PRE_FLIP_EXPORTS) == PRE_FLIP_EXPORT_COUNT
    assert "QuadCortex" in PRE_FLIP_EXPORTS


@pytest.mark.parametrize("name", PRE_FLIP_EXPORTS)
def test_every_pre_flip_name_resolves_under_protocol(name):
    """The name has to still point at the thing it named, not just exist.

    `hasattr` alone is satisfied by `Model = None`, which keeps the name and
    loses the class. Eleven of these names appear nowhere else in the suite, so
    for those this is the whole of their coverage.
    """
    assert hasattr(protocol, name), (
        f"{name} was exported by pyquadcortex before the flip and must be "
        f"reachable as pyquadcortex.protocol.{name}"
    )
    value = getattr(protocol, name)
    assert value is not None, (
        f"pyquadcortex.protocol.{name} exists but is None - the name survived "
        f"the move and the thing it named did not"
    )
    if inspect.ismodule(value):
        assert value.__name__ == f"pyquadcortex.protocol.{name}", (
            f"pyquadcortex.protocol.{name} is the module {value.__name__}")
        return
    home = getattr(value, "__module__", None)
    if home is not None:            # plain constants carry no __module__
        assert home.startswith("pyquadcortex.protocol"), (
            f"pyquadcortex.protocol.{name} is defined in {home}, outside the "
            f"protocol layer")
        own_name = getattr(value, "__qualname__", None)
        assert own_name in (None, name), (
            f"pyquadcortex.protocol.{name} is bound to {own_name} - the export "
            f"survived the move as an alias for something else")


def test_protocol_still_declares_the_whole_pre_flip_surface():
    """Reachable is not enough - it has to stay the documented surface."""
    assert set(PRE_FLIP_EXPORTS) <= set(protocol.__all__)


def test_top_level_no_longer_re_exports_the_protocol_surface():
    """The flip is a break, not an alias. QuadCortex is one import deeper now."""
    assert not hasattr(pyquadcortex, "QuadCortex")


def test_the_model_is_what_the_top_level_offers():
    """Pinned as an equality, so every published name is a deliberate one.

    A superset check leaves anything past the three it names unguarded, and the
    two error re-exports the readme tells users about were in exactly that
    position: deleting them changed nothing anywhere.
    """
    assert pyquadcortex.connect is not protocol.connect
    assert set(pyquadcortex.__all__) == {
        "__version__", "connect", "Device", "protocol",
        "FootswitchLetter", "SceneLetter", "PresetAddress",
        "DeviceNotFoundError", "DeviceLostError",
        # the preset surface
        "Preset", "Scene", "Scenes",
        # the grid
        "Rows", "Row", "SplittableRow", "Slots", "BlockGrid",
        # what sits in a cell
        "Block", "DeviceBlock", "InputBlock", "OutputBlock",
        "SplitterBlock", "MixerBlock", "LaneOutput",
        "VirtualDevice", "InputSource", "OutputDestination",
        # what the model noticed
        "ModelEvent", "Changed", "Invalidated",
        "InactiveSceneError",
        # `Scene.activate()` hands one of these back, and reading its
        # outcome needs the enum - a return type a caller cannot name is
        # not a return type.
        "WriteWatch", "WatchOutcome",
    }
    for name in pyquadcortex.__all__:
        assert getattr(pyquadcortex, name, None) is not None, (
            f"pyquadcortex.__all__ lists {name}, which does not resolve")


PROTOCOL_SOURCES = sorted(
    pathlib.Path(protocol.__file__).parent.rglob("*.py"))


def test_the_protocol_sources_were_actually_found():
    """Guards the parametrisation below: an empty list would pass vacuously."""
    assert len(PROTOCOL_SOURCES) > 5


#: Read from the package rather than typed here. A hardcoded string survives the
#: package being renamed or moved, and the check below then looks for imports of
#: a package that no longer exists - passing for every source file, for ever,
#: with its own guard test still green.
MODEL_PACKAGE = device.__name__

#: The model's names as `pyquadcortex` re-exports them, taken from the model
#: package itself so the set follows the code.
#:
#: `from pyquadcortex import PresetAddress` reaches the model without naming the
#: model package at all - the AST sees `pyquadcortex.PresetAddress`, which is not
#: a module and does not start with `pyquadcortex.device`. That spelling became
#: reachable when the boundary re-exported its value types at top level, so the
#: check has to know which top-level names ARE the model.
RE_EXPORTED = {f"pyquadcortex.{name}" for name in device.__all__}


def _is_the_model(dotted: str) -> bool:
    """True for the model package, anything inside it, and its re-exported names.

    The dot boundary matters: a hypothetical top-level `pyquadcortex/devices.py`
    is a different module, and a prefix test with no boundary would report
    importing it as a layering violation.
    """
    return (dotted == MODEL_PACKAGE or dotted.startswith(MODEL_PACKAGE + ".")
            or dotted in RE_EXPORTED)


def _package_of(source: pathlib.Path) -> str:
    """The dotted package a source file lives in.

    That is what a relative import in it resolves against: the directory for a
    plain module, and the package itself for its own `__init__.py`.
    """
    root = pathlib.Path(pyquadcortex.__file__).parent
    parts = source.relative_to(root).parts
    if source.name == "__init__.py":
        parts = parts[:-1]
    else:
        parts = parts[:-1]
    return ".".join(("pyquadcortex",) + parts)


def _imported_modules(tree: ast.AST, package: str) -> list[str]:
    """Every module an import statement in `tree` names, as an absolute path.

    `package` is the dotted package the source file lives in, which is what
    relative imports are resolved against.

    Covering all the spellings matters because the house style here is the
    package-attribute form - `pyquadcortex/device/device.py` opens with
    `from pyquadcortex import protocol` - so `from pyquadcortex import device`
    is the spelling a future author is most likely to reach for, and it names no
    module at all in the AST. The relative forms need `node.level` resolved for
    the same reason.
    """
    found = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found += [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            if node.level:                       # from . / .. import ...
                base = package.split(".")
                base = base[:len(base) - node.level + 1]
                where = ".".join(base + ([node.module] if node.module else []))
            else:
                where = node.module or ""
            found.append(where)
            # `from X import a, b` also names X.a and X.b as modules.
            found += [f"{where}.{alias.name}" for alias in node.names]
    return found


@pytest.mark.parametrize("source", PROTOCOL_SOURCES, ids=lambda p: p.name)
def test_the_protocol_layer_never_imports_the_model(source):
    """The model calls the protocol layer, never the other way round.

    A back-import would make the layer map a lie, turn the protocol layer's
    offline tests into model tests, and create an import cycle the day the model
    grows past a skeleton. Checked on the source rather than at runtime, because
    a lazy import inside a function would not show up in `sys.modules`.
    """
    tree = ast.parse(source.read_text())
    offenders = sorted({m for m in _imported_modules(tree, _package_of(source))
                        if _is_the_model(m)})
    assert not offenders, (
        f"{source.name} imports {offenders} - the protocol layer must not "
        f"depend on the model")


IMPORT_SPELLINGS = [
    ("absolute from", "from pyquadcortex.device import Device", True),
    ("absolute plain", "import pyquadcortex.device", True),
    ("package attribute", "from pyquadcortex import device", True),
    ("relative from", "from ..device import Device", True),
    ("relative attribute", "from .. import device", True),
    ("the module inside it", "from pyquadcortex.device import device", True),
    ("a re-exported value type", "from pyquadcortex import PresetAddress", True),
    ("a re-exported type, renamed",
     "from pyquadcortex import FootswitchLetter as FS", True),
    ("two of them at once",
     "from pyquadcortex import SceneLetter, PresetAddress", True),
    ("a sibling module", "from pyquadcortex.protocol import client", False),
    ("a hypothetical devices.py", "from pyquadcortex import devices", False),
    ("a name, not a module", "from pyquadcortex.protocol import open_device",
     False),
    ("the package's own version", "from pyquadcortex import __version__", False),
]


@pytest.mark.parametrize("label,source,is_a_violation", IMPORT_SPELLINGS,
                         ids=[s[0] for s in IMPORT_SPELLINGS])
def test_the_layering_check_reads_every_import_spelling(label, source,
                                                        is_a_violation):
    """Guards the check above against the imports it cannot see.

    A layering rule enforced by a check with blind spots is enforced only for
    the spellings someone happened to think of. The re-export cases are the ones
    this story added: `pyquadcortex` now hands out three model types at top
    level, and `from pyquadcortex import PresetAddress` names no module.
    """
    found = _imported_modules(ast.parse(source), "pyquadcortex.protocol")
    assert any(_is_the_model(m) for m in found) is is_a_violation


IMPORTS_THE_CHECK_CANNOT_SEE = [
    ("a star import", "from pyquadcortex import *"),
    ("the package, then an attribute reach",
     "import pyquadcortex\nx = pyquadcortex.device.translate.row_to_wire(row)"),
]


@pytest.mark.parametrize("label,source", IMPORTS_THE_CHECK_CANNOT_SEE,
                         ids=[s[0] for s in IMPORTS_THE_CHECK_CANNOT_SEE])
def test_the_layering_check_says_where_it_stops(label, source):
    """Written down rather than left to be discovered.

    This reads only import statements, so a name that arrives through `*`, or a
    module reached by attribute after importing the package, is invisible to it.
    Neither is house style and neither appears in the tree, but a sample table
    where every case passes reads like a completeness proof. If one of these
    starts being caught, move it up into `IMPORT_SPELLINGS`.
    """
    found = _imported_modules(ast.parse(source), "pyquadcortex.protocol")
    assert not any(_is_the_model(m) for m in found), (
        f"the check now sees {label!r} - move it into IMPORT_SPELLINGS")

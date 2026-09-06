"""The snapshot package all three catalog generators write into (ADR-0020).

``--snapshot coros_x_y_z`` names a package under
``pyquadcortex/protocol/catalogs/``, and each generator writes one module of
it. Which means each of them has to answer the same two questions - is this a
name we may create a directory for, and what does the package's ``__init__``
import - and three copies of that answer is three of them to keep right. This
module is the one copy; the generators import it the way
``generate_params.py`` imports ``generate_models`` for its shared helpers.

The ``__init__`` is REWRITTEN on every run rather than created once, because a
snapshot is generated one module at a time: a run that was interrupted, or a
contributor who has generated the models and not yet the params, left an
``__init__`` importing three modules of which one existed - a package that
raises ``ImportError`` the moment anything touches it. It now imports what is
actually on disk, so a half-generated snapshot imports and says what it has.
"""
import pathlib

#: The modules a snapshot package can hold, in the order the ``__init__``
#: imports them - alphabetical, as an import line is written.
MODULES = ("models", "options", "params")

#: The same three in the order ``__all__`` lists them. It differs, and it is
#: kept because the committed ``coros_4_0_1/__init__.py`` is what this has to
#: reproduce byte for byte; ``tests/test_generators.py`` holds that.
EXPORTS = ("models", "params", "options")


def snapshot_version(snapshot: str) -> str:
    """'coros_4_0_1' -> '4.0.1', for the snapshot package's __init__ docstring."""
    return snapshot.removeprefix("coros_").replace("_", ".")


def _check_name(snapshot: str) -> None:
    """Refuse a ``--snapshot`` that is not a package name we may create.

    The value becomes a directory under the repo's own ``catalogs/`` package
    AND a dotted name inside a generated import statement, so it has to be an
    identifier before it is anything else. A separator or a ``..`` would write
    outside the tree; a leading underscore is private by convention and the
    package's ``__init__`` would not be the thing importing it.
    """
    trouble = None
    if any(bad in snapshot for bad in ("/", "\\", "..")):
        trouble = "it must not contain '/', '\\' or '..'"
    elif not snapshot.isidentifier():
        trouble = "it must be a Python identifier"
    elif snapshot.startswith("_"):
        trouble = "it must not start with '_'"
    if trouble is not None:
        raise SystemExit(
            f"--snapshot {snapshot!r} is not a snapshot package name: {trouble}. "
            f"Name it by CorOS version, e.g. coros_4_1_0.")


def ensure_snapshot_package(catalogs_root, snapshot: str) -> pathlib.Path:
    """Create ``<catalogs_root>/<snapshot>/`` and write its ``__init__.py``.

    Returns the directory. Call it BEFORE writing a module, which checks the
    name and makes the directory, and again AFTER, so the ``__init__`` names
    the module this run has just added; it is idempotent, and the second call
    is the one that makes the package import.
    """
    _check_name(snapshot)
    directory = pathlib.Path(catalogs_root) / snapshot
    directory.mkdir(parents=True, exist_ok=True)
    present = [name for name in MODULES if (directory / f"{name}.py").exists()]
    imports = (
        f"from pyquadcortex.protocol.catalogs.{snapshot} import "
        f"{', '.join(present)}  # noqa: F401\n" if present else "")
    exports = ", ".join(f'"{name}"' for name in EXPORTS if name in present)
    (directory / "__init__.py").write_text(
        f'"""The Quad Cortex catalog on CorOS {snapshot_version(snapshot)}, '
        'read from the maintainer\'s unit."""\n'
        f"{imports}"
        "\n"
        f"__all__ = [{exports}]\n",
        encoding="utf-8",
    )
    return directory

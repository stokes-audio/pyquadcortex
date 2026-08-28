#!/usr/bin/env python3
"""Check what a built wheel and sdist actually contain.

`twine check` validates metadata and never opens the archives, so nothing was
watching the file list. The generated protobuf bindings are the reason
`pip install pyquadcortex` needs no protoc toolchain (ADR-0001): a build rule
that dropped them would produce a wheel that installs cleanly and then fails on
the first import, which is the kind of break that reaches users rather than CI.

Run against a `dist/` directory that already holds one wheel and one sdist::

    python -m build
    python scripts/check_artifacts.py dist
"""
import pathlib
import sys
import tarfile
import zipfile

#: Paths that must be inside BOTH artifacts. The bindings are the point; the
#: rest catches a packaging rule that took only one namespace with it.
REQUIRED = (
    "pyquadcortex/protocol/proto/__init__.py",
    "pyquadcortex/protocol/proto/Preset_pb2.py",
    "pyquadcortex/protocol/proto/ProductionAutomation_pb2.py",
    # The stubs and the PEP 561 marker travel with the bindings or the typing
    # stops at our own CI: without `py.typed` a checker ignores an installed
    # package's annotations entirely, and without the *_pb2.pyi it cannot see
    # inside a generated message. Both are easy for a packaging rule to drop,
    # because neither is a .py file and nothing imports them.
    "pyquadcortex/py.typed",
    "pyquadcortex/protocol/proto/Preset_pb2.pyi",
    "pyquadcortex/protocol/proto/ProductionAutomation_pb2.pyi",
    "pyquadcortex/protocol/cli.py",
    "pyquadcortex/protocol/client.py",
    "pyquadcortex/device/device.py",
    "pyquadcortex/device/preset.py",
    "pyquadcortex/device/grid.py",
    "pyquadcortex/device/blocks.py",
    # The translation boundary is a PACKAGE, so its `__init__` alone proves
    # nothing: a packaging rule that took the directory but dropped its modules
    # would ship a boundary that re-exports names it no longer has. So a real
    # converter module is named beside it.
    "pyquadcortex/device/translate/__init__.py",
    "pyquadcortex/device/translate/coordinates.py",
    "pyquadcortex/device/translate/grid.py",
    # The two files that DECIDE what `import pyquadcortex` hands back. Ship a
    # wheel without either and every module above is still present and correct,
    # while the package exports nothing.
    "pyquadcortex/device/__init__.py",
    "pyquadcortex/__init__.py",
    "pyquadcortex/_version.py",
)

#: The console script a user types. It is a string in pyproject that nothing
#: imports, so the built metadata is the only place it can be confirmed.
CONSOLE_SCRIPT = "qcctl = pyquadcortex.protocol.cli:main"


def _one(directory: pathlib.Path, pattern: str) -> pathlib.Path:
    found = sorted(directory.glob(pattern))
    if len(found) != 1:
        raise SystemExit(
            f"expected exactly one {pattern} in {directory}, found {len(found)}")
    return found[0]


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    dist = pathlib.Path(argv[0] if argv else "dist")
    wheel_path = _one(dist, "*.whl")
    sdist_path = _one(dist, "*.tar.gz")

    wheel = zipfile.ZipFile(wheel_path)
    wheel_names = wheel.namelist()
    with tarfile.open(sdist_path) as archive:
        sdist_names = archive.getnames()

    problems = []
    for path in REQUIRED:
        for label, names in (("wheel", wheel_names), ("sdist", sdist_names)):
            if not any(name.endswith(path) for name in names):
                problems.append(f"{path} is missing from the {label}")

    entry_points = [n for n in wheel_names if n.endswith("entry_points.txt")]
    if not entry_points:
        problems.append("the wheel declares no console script at all")
    else:
        declared = wheel.read(entry_points[0]).decode()
        if CONSOLE_SCRIPT not in declared:
            problems.append(
                f"the wheel declares {declared.strip()!r}, not {CONSOLE_SCRIPT!r}")

    if problems:
        print(f"{wheel_path.name} / {sdist_path.name}:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    print(f"{wheel_path.name} and {sdist_path.name} carry the generated "
          f"bindings, both namespaces, and the qcctl entry point")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""What `pyproject.toml` promises about the installed package.

The console-script target is a STRING in a file nothing imports, so a typo or a
module rename ships a `qcctl` that dies on first use and is never noticed until
someone installs the wheel. This resolves it the way the installed script does.
"""
import importlib
import pathlib
import re
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())
BINDINGS = ROOT / "pyquadcortex" / "protocol" / "proto"

#: Every generated file names the generator that wrote it, on a line reading
#: `# Protobuf Python Version: 7.35.1`.
GENCODE_STAMP = re.compile(r"^# Protobuf Python Version: *(\S+)", re.MULTILINE)


def test_the_console_script_is_still_qcctl():
    assert set(PYPROJECT["project"]["scripts"]) == {"qcctl"}


def test_the_console_script_target_resolves_to_a_callable():
    target = PYPROJECT["project"]["scripts"]["qcctl"]
    module_name, _, attribute = target.partition(":")
    module = importlib.import_module(module_name)
    assert callable(getattr(module, attribute, None)), (
        f"pyproject declares qcctl = {target!r}, but {attribute} is not a "
        f"callable in {module_name}"
    )


def test_the_console_script_target_actually_runs():
    """Resolving the name is not the same as the command working.

    Everything else `qcctl` needs happens on the way into `main` - the parser is
    built, the module-level imports run - and none of it is exercised by looking
    the attribute up. This runs it the way the installed script does, in a fresh
    process so nothing another test imported can carry it.
    """
    target = PYPROJECT["project"]["scripts"]["qcctl"]
    module_name, _, attribute = target.partition(":")
    result = subprocess.run(
        [sys.executable, "-c",
         f"import importlib, sys\n"
         f"sys.argv = ['qcctl', '--help']\n"
         f"m = importlib.import_module({module_name!r})\n"
         f"raise SystemExit(getattr(m, {attribute!r})())\n"],
        cwd=ROOT, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert "qcctl" in result.stdout
    for command in ("recall", "scene", "version", "dump-preset"):
        assert command in result.stdout, (
            f"`qcctl --help` no longer lists {command}")


def test_the_version_file_pyproject_reads_is_the_one_the_package_publishes():
    import pyquadcortex
    from pyquadcortex import protocol

    path = ROOT / PYPROJECT["tool"]["hatch"]["version"]["path"]
    assert path.exists(), f"hatch reads the version from {path}, which is missing"
    assert f'__version__ = "{pyquadcortex.__version__}"' in path.read_text()
    assert protocol.__version__ == pyquadcortex.__version__


def test_the_wheel_takes_the_whole_package_and_narrows_nothing():
    """Both namespaces are subpackages, so the wheel must take the tree.

    This reads a declaration, not an artifact. What ships is checked against the
    built wheel and sdist in the `build` job of `.github/workflows/ci.yml`,
    which is where a wheel already exists; the generated protobuf bindings are
    what that job is watching, since ADR-0001 makes shipping them the whole
    reason `pip install` needs no protoc.
    """
    wheel = PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]
    assert wheel["packages"] == ["pyquadcortex"]
    narrowing = {"exclude", "only-include"} & set(wheel)
    assert not narrowing, (
        f"the wheel target grew {sorted(narrowing)}; anything it drops leaves "
        f"the installed package short of what the tests import")


def test_the_generated_bindings_are_where_the_package_imports_them_from():
    """ADR-0001: these are committed on purpose and must never be gitignored."""
    proto = ROOT / "pyquadcortex" / "protocol" / "proto"
    for name in ("__init__.py", "Preset_pb2.py", "ProductionAutomation_pb2.py"):
        assert (proto / name).is_file(), f"{name} is missing from {proto}"


def _committed_gencode() -> dict[str, str]:
    """The generator version stamped into each committed binding."""
    stamps = {}
    for path in sorted(BINDINGS.glob("*_pb2.py")):
        found = GENCODE_STAMP.search(path.read_text())
        assert found, (
            f"{path.name} carries no `# Protobuf Python Version:` line, so "
            f"nothing here can tell which generator wrote it")
        stamps[path.name] = found.group(1)
    assert stamps, f"no generated bindings found in {BINDINGS}"
    return stamps


def _protobuf_pin() -> str:
    """The `protobuf` requirement string from pyproject's runtime deps."""
    for requirement in PYPROJECT["project"]["dependencies"]:
        # Anchored on a version operator, not a word boundary: `protobuf-stubs`
        # is a real package name and `protobuf\b` would happily match it.
        if re.match(r"protobuf\s*[<>=!~]", requirement):
            return requirement
    raise AssertionError("pyproject no longer depends on protobuf at all")


def _bound(pin: str, operator: str) -> str | None:
    """The version in `pin`'s `operator` clause, e.g. `>=` -> "7.35.1"."""
    for clause in pin.split(","):
        clause = clause.strip().removeprefix("protobuf").strip()
        if clause.startswith(operator):
            return clause[len(operator):].strip()
    return None


def _the_gencode() -> str:
    """The one gencode version the committed bindings agree on.

    Both files come out of the same protoc run, so two different stamps mean one
    was regenerated on its own - and since the sibling import ties them
    together, the descriptors they build are no longer known to agree.
    """
    stamps = _committed_gencode()
    assert len(set(stamps.values())) == 1, (
        f"the committed bindings carry different gencode versions: {stamps}. "
        f"Regenerate them together with scripts/compile_protos.sh")
    return next(iter(stamps.values()))


def test_all_the_committed_bindings_came_from_one_generator():
    assert _the_gencode()


def test_the_protobuf_pin_floor_is_exactly_the_committed_gencode():
    """ADR-0001's whole claim: the bindings and the pin are one unit.

    Nothing at runtime enforces this. protobuf validates `runtime >= gencode`
    and nothing else, so both ways of drifting stay quiet until they reach a
    user, and regenerating with an older generator is the easy accident:
    `scripts/compile_protos.sh` refuses that one, and this catches gencode that
    arrived by any other route, on every PR, with no protoc installed.
    """
    gencode = _the_gencode()
    pin = _protobuf_pin()
    floor = _bound(pin, ">=")
    assert floor is not None, f"the protobuf pin {pin!r} has no `>=` lower bound"
    assert floor == gencode, (
        f"pyproject pins protobuf>={floor} but the committed bindings are "
        f"gencode {gencode}.\n"
        f"  floor below gencode: every user who installs protobuf=={floor} "
        f"gets a hard ImportError.\n"
        f"  floor above gencode: the bindings were regenerated by an older "
        f"generator. That still imports, which is exactly why it needs "
        f"catching here.\n"
        f"Move whichever one is wrong, in this commit (ADR-0001).")


def test_the_protobuf_pin_stops_below_the_next_gencode_major():
    """A major bump is the case where an unchanged pin does reach users.

    protobuf gencode is only guaranteed against a runtime of the same major, so
    an upper bound left behind a major-crossing regeneration lets pip resolve a
    runtime that cannot load the bindings at all.
    """
    gencode = _the_gencode()
    pin = _protobuf_pin()
    ceiling = _bound(pin, "<")
    assert ceiling is not None, f"the protobuf pin {pin!r} has no `<` upper bound"
    expected = str(int(gencode.split(".")[0]) + 1)
    assert ceiling.split(".")[0] == expected, (
        f"the committed bindings are gencode {gencode}, so the pin should stop "
        f"below protobuf {expected}, not {ceiling}")

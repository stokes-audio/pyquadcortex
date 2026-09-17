"""What `pyproject.toml` promises about the installed package.

The console-script target is a STRING in a file nothing imports, so a typo or a
module rename ships a `qcctl` that dies on first use and is never noticed until
someone installs the wheel. This resolves it the way the installed script does.
"""
import importlib
import importlib.metadata
import pathlib
import re
import subprocess
import sys
import tomllib

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

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


PROTO = ROOT / "pyquadcortex" / "protocol" / "proto"


def test_the_generated_bindings_are_where_the_package_imports_them_from():
    """ADR-0001: these are committed on purpose and must never be gitignored."""
    for name in ("__init__.py", "Preset_pb2.py", "ProductionAutomation_pb2.py"):
        assert (PROTO / name).is_file(), f"{name} is missing from {PROTO}"


def test_every_binding_has_its_type_stub_beside_it():
    """ADR-0018: committed for the same reason the bindings are.

    Without a stub a checker cannot see inside a generated message at all, and
    CI runs one. `scripts/check_artifacts.py` proves they reach the wheel; this
    proves they are in the tree, which is the half a `.gitignore` edit or a
    hand-run protoc could quietly undo.
    """
    for name in ("Preset_pb2", "ProductionAutomation_pb2"):
        stub = PROTO / f"{name}.pyi"
        assert stub.is_file(), f"{name}.pyi is missing from {PROTO}"


def test_no_stub_imports_a_sibling_flat():
    """The failure that reads exactly like success.

    protoc writes `import Preset_pb2` - flat, like the bindings' own imports.
    The BINDINGS survive that through the sys.path shim in `proto/__init__.py`;
    a type checker cannot use a runtime shim, so the import resolves to nothing
    and EVERY field carrying that type silently becomes `Any` while mypy still
    reports success. `msg.preset` is the path every keyed grid write takes.

    `scripts/compile_protos.sh` rewrites these package-relative. This is what
    notices if a stub is ever regenerated without it.
    """
    for stub in PROTO.glob("*_pb2.pyi"):
        flat = [line for line in stub.read_text().splitlines()
                if re.match(r"^import \w+_pb2\b", line)]
        assert not flat, (
            f"{stub.name} imports a sibling flat ({flat[0]!r}), which a type "
            f"checker cannot resolve - every field of that type becomes Any")


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


# -- the pins and the environment that claims to satisfy them -----------------

#: The declared pins that can refuse a NEWER release. This is not the whole
#: reach of the comparison below - every pin refuses an install beneath its
#: floor, so `pytest>=8` really does bite on pytest 7. It is the direction a
#: working copy drifts in, where an ordinary upgrade would otherwise walk past a
#: bound nobody re-reads. What naming it buys is the claim that an upgrade stays
#: quiet unless it crosses one of these two, which `docs/STEERING.md` section 6
#: makes in prose and the two tests below hold from both ends: the set has to
#: match what pyproject declares, and section 6 has to name the set.
PINS_WITH_A_CEILING = {"protobuf", "mypy"}

#: A version no release will reach, for asking a specifier whether it has an
#: effective ceiling at all.
_ABOVE_EVERYTHING = "9999.0.0"


def _declared_requirements():
    """Every requirement `pyproject.toml` declares, runtime and dev extra."""
    project = PYPROJECT["project"]
    return [Requirement(r) for r in project["dependencies"]] + [
        Requirement(r) for extra in project["optional-dependencies"].values()
        for r in extra]


def _has_a_ceiling(req):
    """Whether this requirement can refuse a newer release.

    Asked by offering a version nothing will ever reach, rather than by reading
    operators. `~=1.83` and `==1.2.*` both refuse 2.0 while neither spells a
    `<`, so an operator scan calls them open and lets a minor-version ceiling
    in behind the sentence that says there is none. `!=1.2.3` really is open,
    and this says so.
    """
    return not req.specifier.contains(_ABOVE_EVERYTHING, prereleases=True)


def test_the_pins_that_cap_an_upgrade_are_the_ones_named_here():
    """Which pins can refuse a NEWER release, held so the prose cannot drift.

    `pytest>=8` refuses pytest 7 like any other pin, but nothing above 8, so an
    upgrade never trips it. That asymmetry is the whole argument for the
    comparison below staying quiet in ordinary use, and an argument nobody holds
    is an argument that rots - so the set is named, and a new ceiling has to
    come through here.
    """
    declared = _declared_requirements()
    ceilinged = {canonicalize_name(req.name)
                 for req in declared if _has_a_ceiling(req)}
    assert ceilinged == {canonicalize_name(n) for n in PINS_WITH_A_CEILING}, (
        f"the pins that can refuse a newer release are now {sorted(ceilinged)}, "
        f"not {sorted(PINS_WITH_A_CEILING)}. A new ceiling widens what the "
        f"comparison below can refuse, so move this set and the sentence in "
        f"docs/STEERING.md section 6 together, in this commit.")


def _steering_bullet(lead):
    """The whole bullet in `docs/STEERING.md` that starts with `lead`.

    The documents wrap at about 80 columns, so a bullet is several lines. Reading
    only the first one would pass a bullet that names nothing after the wrap.
    """
    lines = (ROOT / "docs" / "STEERING.md").read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line.startswith(lead)), None)
    if start is None:
        return None
    indent = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for line in lines[start + 1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            break
        # Only a bullet at the lead's own indent ends it. A continuation line
        # may itself begin with `- `, which this repository uses as a separator.
        here = len(line) - len(line.lstrip())
        if here <= indent and re.match(r"[-*]\s", line.lstrip()):
            break
        out.append(line.strip())
    return " ".join(out)


def test_the_steering_document_names_the_pins_that_cap_an_upgrade():
    """Section 6 tells a reader why this file's check is quiet. Hold it to that.

    `tests/test_option_audit.py` already holds `docs/domain-model.md` to the
    counts it quotes, for the same reason: a document that can drift from the
    thing it describes will.

    BOTH directions, because both have a trigger queued. A pin that gains a
    ceiling leaves the sentence naming too few. And section 8 asks whether to
    raise the mypy ceiling - doing so leaves it naming one too many, which a
    check that only looked for what was missing would have passed.
    """
    bullet = _steering_bullet("- **The environment is held to the pins")
    assert bullet, (
        "docs/STEERING.md section 6 no longer carries the bullet about holding "
        "the environment to the pins, which is where this check is explained")
    capping = {canonicalize_name(n) for n in PINS_WITH_A_CEILING}
    missing = sorted(n for n in capping if f"`{n}`" not in bullet)
    assert not missing, (
        f"docs/STEERING.md section 6 does not name {missing}, which can refuse "
        f"a newer release. The sentence claims which pins cap an upgrade; it "
        f"has to name all of them.")
    declared = _declared_requirements()
    stale = sorted({canonicalize_name(req.name) for req in declared
                    if canonicalize_name(req.name) not in capping
                    and f"`{canonicalize_name(req.name)}`" in bullet})
    assert not stale, (
        f"docs/STEERING.md section 6 still names {stale}, which no longer caps "
        f"an upgrade. Raising a ceiling has to move the sentence too, or it "
        f"goes on claiming a bound that is gone.")


def test_the_sdist_still_does_not_ship_the_documents_this_suite_reads():
    """Why the comparison below has no development-checkout gate.

    One was written and removed. The sdist ships `tests/` but not `docs/`, and
    this suite reads `docs/` - the STEERING check above, and
    `tests/test_option_audit.py` holding `docs/domain-model.md` to its counts -
    so it does not run from an unpacked sdist at all. A skip for a population
    that cannot reach the code only ever hides something. Shipping `docs/` would
    change that, and this is what trips when someone does, rather than a comment
    asking to be remembered.
    """
    sdist = PYPROJECT["tool"]["hatch"]["build"]["targets"]["sdist"]
    assert "include" in sdist, (
        "the sdist declares no include list, so hatchling ships the whole tree "
        "and `docs/` with it. Revisit the gate this replaced.")
    forced = sdist.get("force-include", {})
    # Both sides of `force-include`: the key is the source path and the value is
    # where it lands, so either one can be `docs/`.
    named = list(sdist["include"]) + list(forced) + list(forced.values())
    assert not [p for p in named if p.strip("/").split("/")[0] == "docs"], (
        f"the sdist now names {named}, so this suite can be run from an "
        f"unpacked sdist - where a repackager's own protobuf and mypy are "
        f"versions they chose and cannot swap, and not the drift the comparison "
        f"below is about. Revisit the gate this replaced.")
    # What it cannot see: a glob that happens to reach `docs/` (`/*`, `/doc*`),
    # a build hook that writes files in, or the directory renamed. It reads the
    # two lists hatchling takes paths in, not hatchling's own answer - which
    # would mean building an sdist in an offline test. A wider net here would
    # be guessing at spellings rather than reading a declaration.


def test_no_installed_dependency_sits_outside_the_pin_that_declares_it():
    """CI installs from these pins; a working copy is installed by hand.

    So the two drift, and nothing said so. This checkout ran mypy 2.3.1 against
    a `mypy>=1.15,<2` pin for an unknown number of pull requests, which made
    every local "mypy clean" a claim about a checker CI does not run. Nothing
    turned out to be broken. Nothing would have reported it if it had been.

    What is not installed is SKIPPED rather than failed, so this says nothing
    about an environment carrying only what it needs. `protobuf` is the one it
    anchors on: the package cannot be imported without it, so a lookup that
    cannot find `protobuf` has broken rather than answered - and without that
    anchor the skip would let this pass having compared nothing at all.
    """
    declared = _declared_requirements()
    compared, outside = [], []
    for req in declared:
        # A marker is a condition on the environment - `; python_version < "3.11"`
        # - and a pin that does not apply here cannot be disagreed with. Nothing
        # in pyproject.toml carries one today; enforcing one that does not apply
        # is the cheapest way to start crying wolf.
        if req.marker is not None and not req.marker.evaluate():
            continue
        name = canonicalize_name(req.name)
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            continue
        compared.append(name)
        # `prereleases=True` so an installed prerelease is COMPARED rather than
        # reported as outside every pin, which is the other obvious way to cry
        # wolf.
        if not req.specifier.contains(installed, prereleases=True):
            outside.append(f"{name} {installed} installed, pinned "
                           f"{req.name}{req.specifier}")
    assert "protobuf" in compared, (
        f"nothing answered for protobuf, which this package cannot import "
        f"without, so the version lookup has broken rather than the environment "
        f"being bare. Compared {sorted(compared)}.")
    assert not outside, (
        "the environment running pytest disagrees with pyproject.toml:\n  "
        + "\n  ".join(outside)
        + "\nCI installs from the pins, so whatever was checked against these "
          "versions was checked against something else. Reinstall with "
          "`uv pip install -e \".[dev]\"` - note that is the interpreter "
          "running this, which in a worktree is usually the main checkout's. "
          "If the newer version is the one this project now wants, move the "
          "pin in this commit.")

"""What `pyproject.toml` promises about the installed package.

The console-script target is a STRING in a file nothing imports, so a typo or a
module rename ships a `qcctl` that dies on first use and is never noticed until
someone installs the wheel. This resolves it the way the installed script does.
"""
import importlib
import pathlib
import subprocess
import sys
import tomllib

ROOT = pathlib.Path(__file__).resolve().parent.parent
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text())


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

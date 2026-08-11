"""What `pyproject.toml` promises about the installed package.

The console-script target is a STRING in a file nothing imports, so a typo or a
module rename ships a `qcctl` that dies on first use and is never noticed until
someone installs the wheel. This resolves it the way the installed script does.
"""
import importlib
import pathlib
import tomllib

import pytest

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


def test_the_version_file_pyproject_reads_is_the_one_the_package_publishes():
    import pyquadcortex
    from pyquadcortex import protocol

    path = ROOT / PYPROJECT["tool"]["hatch"]["version"]["path"]
    assert path.exists(), f"hatch reads the version from {path}, which is missing"
    assert f'__version__ = "{pyquadcortex.__version__}"' in path.read_text()
    assert protocol.__version__ == pyquadcortex.__version__


@pytest.mark.parametrize("package", ["pyquadcortex"])
def test_the_wheel_ships_the_whole_package(package):
    """Both namespaces are subpackages, so the wheel must take the tree."""
    assert PYPROJECT["tool"]["hatch"]["build"]["targets"]["wheel"]["packages"] == [package]

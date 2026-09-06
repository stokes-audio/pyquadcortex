"""The catalog generators write per-firmware snapshots (ADR-0020, spec section 2)."""
import importlib.util
import os
import pathlib
import subprocess
import sys

import pytest

from pyquadcortex.protocol import catalog

ROOT = pathlib.Path(__file__).parent.parent


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


# Two Cabsim-prefixed categories, not one: generate_params.example_cab must
# find the lowest id ACROSS categories whose name starts with "Cabsim", not
# just the lowest id within the first such category it meets. Cabsim Guitar
# (M) is placed AFTER Cabsim Bass (M) in this document and carries the lower
# id (12001 < 21001), so document order cannot be what the test measures -
# only a genuine cross-category minimum passes. The Delay model exercises an
# ordinary factory model. The Internal category supplies the ids
# generate_params.render() indexes directly - CABSIM_LAYOUT and the
# CONTAINERS table - which are not in cat.factory_models() (internal="true")
# but must still exist in the catalog for cat[id] to resolve.
XML = b"""<?xml version="1.0"?>
<ModelRepo>
  <Category id="21" name="Cabsim Bass (M)">
    <Model id="21005" name="N212 Darkglass Neo (M)"><Parameter name="MIC 1" type="comboBox"/></Model>
    <Model id="21001" name="N210C Darkglass (M)"><Parameter name="MIC 1" type="comboBox"/></Model>
  </Category>
  <Category id="12" name="Cabsim Guitar (M)">
    <Model id="12001" name="N412 Stand (M)"><Parameter name="MIC 1" type="comboBox"/></Model>
  </Category>
  <Category id="6" name="Delay">
    <Model id="6001" name="Analog Delay (M)"><Parameter name="MIX" type="float" units="%" min="0" max="100"/></Model>
  </Category>
  <Category id="0" name="Internal">
    <Model id="12000" name="Default Cabsim" internal="true"/>
    <Model id="23000" name="Lane Output Control" internal="true"/>
    <Model id="28000" name="Input Gate Control" internal="true"/>
    <Model id="11000" name="Mixer" internal="true"/>
    <Model id="10004" name="Splitter" internal="true"/>
    <Model id="10000" name="Splitter AB" internal="true"/>
    <Model id="25000" name="Tempo Control" internal="true"/>
  </Category>
</ModelRepo>"""


@pytest.fixture
def cat():
    return catalog.parse_model_repo(XML)


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_the_header_names_the_snapshot_it_was_written_for(name, cat):
    mod = _load(name)
    text = mod.render(cat, snapshot="coros_9_9_9")
    assert "--snapshot coros_9_9_9" in text.splitlines()[2], text.splitlines()[:4]


def test_the_params_docstring_example_uses_the_lowest_id_cab_in_the_catalog(cat):
    mod = _load("generate_params")
    assert mod.example_cab(cat) == "models.CabsimGuitarM.N412_STAND_M"
    assert "Block(0, 5, models.CabsimGuitarM.N412_STAND_M)" in mod.render(cat, snapshot="coros_9_9_9")


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_snapshot_is_required_and_decides_the_output_path(name, tmp_path, monkeypatch, cat):
    """`CATALOGS` decides the directory and `--snapshot` decides the leaf.

    The redirection is `CATALOGS` rather than the working directory, because
    the working directory is what the generators used to write relative to -
    a run from anywhere but the repo root built a whole new tree there.
    """
    mod = _load(name)
    monkeypatch.setattr(mod, "load_payload", lambda path: XML)
    monkeypatch.setattr(
        mod, "CATALOGS", tmp_path / "pyquadcortex" / "protocol" / "catalogs")
    monkeypatch.setattr(sys, "argv", [name, "--payload", "x"])
    with pytest.raises(SystemExit):        # argparse: --snapshot is required
        mod.main()
    monkeypatch.setattr(sys, "argv", [name, "--payload", "x", "--snapshot", "coros_9_9_9"])
    mod.main()
    written = tmp_path / "pyquadcortex" / "protocol" / "catalogs" / "coros_9_9_9"
    assert (written / f"{name.split('_')[1]}.py").exists()


# --- the snapshot package itself ---------------------------------------------

@pytest.fixture
def snapshots():
    """`scripts/_snapshots.py`, the one copy all three generators import."""
    return _load("_snapshots")


@pytest.mark.parametrize("bad", ["../../etc", "coros/4/1", "coros-4-1-0",
                                 "_private", "4.1.0", ""])
def test_a_snapshot_name_that_is_not_a_package_name_is_refused(snapshots, tmp_path, bad):
    """The value becomes a directory in the repo AND a dotted name in a
    generated import, so it is checked before either happens."""
    with pytest.raises(SystemExit) as caught:
        snapshots.ensure_snapshot_package(tmp_path, bad)
    assert "--snapshot" in str(caught.value)
    assert not list(tmp_path.iterdir()), "nothing was created"


def test_a_full_snapshots_init_is_the_committed_one_byte_for_byte(snapshots, tmp_path):
    """`coros_4_0_1/__init__.py` is what this generates, so it is the fixture.

    Regenerating the shipped snapshot must not rewrite the file that is in the
    repo, which is the only way to know the generated one is the real thing.
    """
    directory = tmp_path / "coros_4_0_1"
    directory.mkdir()
    for name in ("models", "params", "options"):
        (directory / f"{name}.py").write_text("# generated\n", encoding="utf-8")

    snapshots.ensure_snapshot_package(tmp_path, "coros_4_0_1")

    committed = (ROOT / "pyquadcortex" / "protocol" / "catalogs" /
                 "coros_4_0_1" / "__init__.py")
    assert (directory / "__init__.py").read_text(encoding="utf-8") == \
        committed.read_text(encoding="utf-8")


def test_a_half_generated_snapshot_imports_what_is_there(snapshots, tmp_path):
    """The `__init__` used to be written once, naming all three modules.

    A snapshot is generated one module at a time, so an interrupted run - or a
    contributor part-way through the three commands - left a package whose
    `__init__` imported a module that did not exist. Anything touching it
    raised ImportError, which reads as a broken library rather than as an
    unfinished snapshot.
    """
    catalogs = tmp_path / "pyquadcortex" / "protocol" / "catalogs"
    for package in (catalogs.parent.parent, catalogs.parent, catalogs):
        package.mkdir(parents=True, exist_ok=True)
        (package / "__init__.py").write_text("", encoding="utf-8")
    directory = snapshots.ensure_snapshot_package(catalogs, "coros_9_9_9")
    (directory / "models.py").write_text("ALL = ()\n", encoding="utf-8")
    snapshots.ensure_snapshot_package(catalogs, "coros_9_9_9")

    text = (directory / "__init__.py").read_text(encoding="utf-8")
    assert "import models  # noqa: F401" in text
    assert "params" not in text and "options" not in text
    assert '__all__ = ["models"]' in text

    # Imported for real, out of tmp_path and not out of the repo: the failure
    # this guards is an ImportError at import time, which only importing sees.
    proved = subprocess.run(
        [sys.executable, "-c",
         "import pyquadcortex.protocol.catalogs.coros_9_9_9 as s; print(s.__all__)"],
        cwd=tmp_path, env={"PATH": os.environ.get("PATH", ""), "PYTHONPATH": ""},
        capture_output=True, text=True)
    assert proved.returncode == 0, proved.stderr
    assert proved.stdout.strip() == "['models']"


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_the_generators_share_one_copy_of_the_snapshot_package(name, snapshots):
    """Three copies of the same helper is three of them to keep right."""
    mod = _load(name)
    assert mod._snapshots.ensure_snapshot_package is snapshots.ensure_snapshot_package \
        or mod._snapshots.__file__ == snapshots.__file__
    source = (ROOT / "scripts" / f"{name}.py").read_text(encoding="utf-8")
    assert "def ensure_snapshot_package" not in source
    assert "def _ensure_snapshot_package" not in source


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_a_generated_snapshot_names_the_module_this_run_wrote(
        name, tmp_path, monkeypatch, cat):
    """The `__init__` is rewritten after the module lands, not before it."""
    mod = _load(name)
    monkeypatch.setattr(mod, "load_payload", lambda path: XML)
    monkeypatch.setattr(
        mod, "CATALOGS", tmp_path / "pyquadcortex" / "protocol" / "catalogs")
    monkeypatch.setattr(sys, "argv", [name, "--payload", "x", "--snapshot", "coros_9_9_9"])
    mod.main()
    written = (tmp_path / "pyquadcortex" / "protocol" / "catalogs" / "coros_9_9_9"
               / "__init__.py").read_text(encoding="utf-8")
    module = name.split("_")[1]
    assert f"import {module}  # noqa: F401" in written
    assert f'__all__ = ["{module}"]' in written


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_the_docstring_shows_the_command_that_actually_works(name):
    """`--snapshot` is required, so an example without it fails at argparse."""
    mod = _load(name)
    for line in mod.__doc__.splitlines():
        stripped = line.strip()
        if stripped.startswith(f"python scripts/{name}.py"):
            assert "--snapshot" in stripped or stripped.endswith("\\"), stripped
    assert "pyquadcortex/protocol/catalogs/<snapshot>/" in mod.__doc__


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_the_output_tree_is_the_repos_catalogs_package_wherever_the_run_starts(name):
    """An absolute path anchored on the script's own location.

    `contributing.md` and `architecture.md` give the command with no working
    directory, so a cwd-relative `CATALOGS` wrote a `pyquadcortex/protocol/
    catalogs/` tree wherever the maintainer happened to be standing, and the
    real snapshot was left untouched with the run reporting success.
    """
    mod = _load(name)
    assert mod.CATALOGS.is_absolute()
    assert mod.CATALOGS == ROOT / "pyquadcortex" / "protocol" / "catalogs"

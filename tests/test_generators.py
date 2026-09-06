"""The catalog generators write per-firmware snapshots (ADR-0020, spec section 2)."""
import importlib.util
import pathlib
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

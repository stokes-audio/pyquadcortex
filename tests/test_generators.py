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


# ---------------------------------------------------------------------------
# generate_options' audit stamping
#
# All of this shipped untested in its first version, including two refusals
# whose whole value is that they fire. `screen_word`'s in particular is the
# mechanism for catching a device that draws one `stepNames` string two ways -
# a finding - and nothing had ever invoked it.
# ---------------------------------------------------------------------------

AUDIT_XML = b"""<?xml version="1.0"?>
<ModelRepo>
  <Category id="6" name="Delay">
    <Model id="6001" name="Test Delay">
      <Parameter name="SHAPE" type="comboBox" stepNames="Soft,Hard,Wild"
                 min="0" max="1" defaultValue="0" steps="3"/>
      <Parameter name="SECRET" type="comboBox" stepNames="alpha,beta" hidden="true"
                 min="0" max="1" defaultValue="0" steps="2"/>
      <Parameter name="CELL" type="comboBox" stepNames="LOW,HIGH"
                 min="0" max="1" defaultValue="0" steps="2"/>
    </Model>
  </Category>
</ModelRepo>"""


def _reading(labels, index, screen, **extra):
    row = {"snapshot": "s", "labels": list(labels), "index": index,
           "screen": screen, "read_on": "2026-09-14", "method": "driven",
           "model": "Test Delay", "model_id": 6001, "param": "X",
           "param_index": 0}
    row.update(extra)
    return row


def _with_readings(monkeypatch, tmp_path, rows):
    """Point the generator's READINGS at a fixture we control."""
    import json
    mod = _load("generate_options")
    path = tmp_path / "readings.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setattr(mod, "READINGS", path)
    return mod


SHAPE = ("Soft", "Hard", "Wild")


def test_a_list_nobody_read_says_so_in_its_own_docstring(monkeypatch, tmp_path):
    mod = _with_readings(monkeypatch, tmp_path, [])
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "NOT audited against the screen" in text
    assert "'audited'" not in text


def test_reading_every_position_stamps_the_enum_audited(monkeypatch, tmp_path):
    rows = [_reading(SHAPE, i, w) for i, w in enumerate(SHAPE)]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "Audited against the unit's screen 2026-09-14: all 3 positions read." in text


def test_reading_some_positions_is_partial_and_never_audited(monkeypatch, tmp_path):
    """The status most likely to be rounded up, so it gets its own test."""
    rows = [_reading(SHAPE, 0, "Soft"), _reading(SHAPE, 2, "Wild")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "PARTLY audited against the screen" in text
    assert "2 of 3 positions read" in text
    assert "'partial'" in text
    assert "'audited'" not in text


def test_a_screen_word_that_contradicts_the_catalog_reaches_the_enum(monkeypatch, tmp_path):
    """The case the whole mechanism exists for, and which has never happened.

    Written against a SYNTHETIC disagreement rather than a recorded one, so it
    is a real test today instead of an assertion that waits years to run.
    """
    rows = [_reading(SHAPE, 0, "Soft"), _reading(SHAPE, 1, "Firm"),
            _reading(SHAPE, 2, "Wild")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "screen: 'Firm'; catalog: 'Hard'" in text
    # A wording difference and a MEANING error are said differently, because
    # flattening them into one word buried the two that mattered among five
    # harmless abbreviations on the waveform list.
    assert "The screen SPELLS 1 differently" in text
    # A wording difference is NOT announced as the catalog being wrong. Scoped
    # to the phrase rather than the word, which also appears in the
    # OPTION_CONTESTED docstring further down the same file.
    assert "The catalog is WRONG at" not in text


def test_two_readings_of_one_position_that_disagree_stop_the_generator(monkeypatch, tmp_path):
    """Two parameters sharing a `stepNames` string, drawn differently.

    That is a finding about the device. Letting file order pick a winner would
    bury it, so the generator refuses rather than emitting either.
    """
    rows = [_reading(SHAPE, 0, "Soft"),
            _reading(SHAPE, 0, "Gentle", param="OTHER"),
            _reading(SHAPE, 1, "Hard"), _reading(SHAPE, 2, "Wild")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "readings disagree" in str(caught.value)
    assert "not one list" in str(caught.value)


def test_the_hidden_flag_alone_never_stamps_a_list_unreadable(monkeypatch, tmp_path):
    """The rule that was wrong, pinned so it cannot come back.

    `SECRET` here is marked `hidden="true"` exactly as a Mono Synth's
    `OSC1 WAVE` is - and that one is on the screen. So a flagged parameter with
    no observation behind it stays UNREAD, which is work somebody should do,
    rather than `absent`, which is work nobody can.
    """
    mod = _with_readings(monkeypatch, tmp_path, [])
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "'absent'" not in text
    assert "NOT audited against the screen" in text


def test_looking_for_a_control_and_not_finding_it_stamps_the_list_absent(monkeypatch, tmp_path):
    rows = [_reading(("alpha", "beta"), 0, None, kind="absent",
                     model="Test Delay", param="SECRET")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "'absent'" in text
    assert "NOT DRAWN by the unit" in text
    assert "Test Delay's SECRET" in text


def test_a_list_recorded_both_read_and_not_drawn_stops_the_generator(monkeypatch, tmp_path):
    """One of the two observations is wrong; neither wins by file order."""
    rows = [_reading(("alpha", "beta"), 0, None, kind="absent", param="SECRET"),
            _reading(("alpha", "beta"), 1, "beta", param="SECRET")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "recorded both as read and as not drawn" in str(caught.value)


def test_a_list_the_unit_draws_is_stamped_drawn_rather_than_audited(monkeypatch, tmp_path):
    """Every position read, and not one WORD checked.

    `OFF,MUTE,DOWN,ON` is the real case: the unit draws circles and dots and
    never writes `MUTE`. Calling that audited would be the overstatement the
    stamp exists to prevent.
    """
    rows = [_reading(("LOW", "HIGH"), 0, "empty circle", kind="symbol"),
            _reading(("LOW", "HIGH"), 1, "filled circle", kind="symbol")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "'drawn'" in text
    assert "DRAWS them rather than naming them" in text
    assert "drawn as 'empty circle'" in text
    # A drawing must never be reported as disagreeing with the word. Asserted
    # against the phrases the generator actually emits - the earlier version
    # looked for "DISAGREE", a token this generator writes for no input at all,
    # so it could not fail.
    assert "The screen SPELLS" not in text
    assert "The catalog is WRONG at" not in text
    assert "screen: " not in text


def test_a_missing_readings_file_stops_the_run_instead_of_erasing_the_audit(monkeypatch, tmp_path):
    """Returning {} here rewrites every list as unread and prints success."""
    mod = _load("generate_options")
    monkeypatch.setattr(mod, "READINGS", tmp_path / "does-not-exist.json")
    with pytest.raises(SystemExit) as caught:
        mod.load_readings("s")
    assert "erase every recorded reading" in str(caught.value)


def test_a_rename_with_no_reading_behind_it_stops_the_generator(monkeypatch, tmp_path):
    """`MEANING_DISAGREEMENTS` renames a PUBLIC member, so it needs evidence.

    Without a guard it is `SPELLING_FIXES` with a bigger blast radius.
    """
    mod = _with_readings(monkeypatch, tmp_path, [])
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {1: "FIRM"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "no DRIVEN reading records" in str(caught.value)


def test_a_rename_cannot_lean_on_a_row_saying_the_control_is_not_drawn(monkeypatch, tmp_path):
    """An `absent` row says nothing about what a POSITION means.

    It was accepted as evidence at first, because every absent row claimed
    `method: "driven"` - so a rename could ride on a row whose whole content is
    "this control is not on the screen".
    """
    rows = [_reading(SHAPE, 1, None, kind="absent", method="looked")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {1: "FIRM"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "not drawn does not count" in str(caught.value)


def test_a_rename_cannot_overrule_a_reading_that_agrees_with_the_catalog(monkeypatch, tmp_path):
    """Requiring a reading to EXIST is not the same as requiring it to support.

    At first the guard only checked existence, so renaming position 1 to
    anything at all passed while the recorded screen word there was `Hard` -
    the catalog's own label. There has to be something to correct.
    """
    rows = [_reading(SHAPE, 1, "Hard")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {1: "FIRM"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "nothing to correct" in str(caught.value)


def test_a_rename_for_a_list_this_catalog_lacks_is_not_demanded(monkeypatch, tmp_path):
    """A correction is per snapshot; another firmware need not carry the list.

    Demanding a reading for a list that is not in the catalog being rendered
    would make a 4.1.0 run fail over a 4.0.1 finding.
    """
    mod = _with_readings(monkeypatch, tmp_path, [])
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS",
                        {("nowhere", "at", "all"): {0: "NOWHERE"}})
    mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")


def test_a_rename_to_a_name_the_list_does_not_contain_is_refused(monkeypatch, tmp_path):
    """The check that actually gives the guard teeth.

    The contradiction test is an exact string compare, so it is trivially
    satisfied on any list whose screen text ABBREVIATES - and every position of
    the one list this table governs does ("SIN" vs "Sine"). With only that test,
    `{0: "HARD_SYNC"}` was accepted on a pure hunch and went on to stamp the
    enum's docstring "the catalog is WRONG at 0".

    What this table can express is "this position is the thing the catalog calls
    ANOTHER position of this list" - a swap. Anything else is a new claim about
    the device and belongs in a record.
    """
    rows = [_reading(SHAPE, 0, "Sft"), _reading(SHAPE, 1, "Hrd"),
            _reading(SHAPE, 2, "Wld")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {0: "INVENTED"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "not what this list calls any of its positions" in str(caught.value)


def test_a_rename_to_the_name_that_position_already_has_is_refused(monkeypatch, tmp_path):
    rows = [_reading(SHAPE, 0, "Sft")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {0: "SOFT"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "already that position's name" in str(caught.value)


def test_a_swap_between_two_positions_of_the_list_is_accepted(monkeypatch, tmp_path):
    """The shape the real finding has: two positions of one list, exchanged."""
    rows = [_reading(SHAPE, 1, "Wld"), _reading(SHAPE, 2, "Hrd")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS",
                        {SHAPE: {1: "WILD", 2: "HARD"}})
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "WILD = 1" in text
    assert "HARD = 2" in text
    assert "The catalog is WRONG at 1, 2" in text


def test_a_rename_using_another_positions_name_is_still_not_a_swap(monkeypatch, tmp_path):
    """`{0: "HARD"}` again, spelled with a name the list happens to contain.

    Requiring the new name to belong to SOME position of the list was not
    enough: renaming position 0 to position 2's name is an invention that
    happens to be spelled from the right vocabulary, and it shadows the real
    member to `HARD_2`.
    """
    rows = [_reading(SHAPE, 0, "Sft"), _reading(SHAPE, 2, "Wld")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {0: "HARD"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "have to be the same set" in str(caught.value)


def test_half_a_swap_is_refused_because_it_deletes_a_member(monkeypatch, tmp_path):
    """`{1: "WILD"}` alone emits WILD and WILD_2, and HARD simply vanishes.

    Silently, from a PUBLIC enum, with no error anywhere - which is why the
    entry is checked as a whole rather than one rename at a time.
    """
    rows = [_reading(SHAPE, 1, "Wld"), _reading(SHAPE, 2, "Hrd")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {SHAPE: {1: "WILD"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    assert "deletes a member" in str(caught.value)


def test_a_closed_swap_keeps_every_member_the_list_had(monkeypatch, tmp_path):
    """The property the permutation check is really protecting."""
    rows = [_reading(SHAPE, 1, "Wld"), _reading(SHAPE, 2, "Hrd")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS",
                        {SHAPE: {1: "WILD", 2: "HARD"}})
    text = mod.render(catalog.parse_model_repo(AUDIT_XML), snapshot="s")
    for member in ("SOFT = 0", "WILD = 1", "HARD = 2"):
        assert member in text
    # no collision suffix anywhere, which is what a deletion would have caused
    assert "_1 =" not in text and "_2 =" not in text


def test_a_rename_to_a_name_two_positions_produce_is_refused(monkeypatch, tmp_path):
    """Two positions of one list can mangle to the same member name.

    The note lists do it - `A` and `A#` both give `A`, which `render_enum`
    resolves with an `_<index>` suffix. A correction naming one of those does not
    say which position it means, so it is refused rather than resolved by
    whichever index a dict comprehension happened to keep last.
    """
    notes = ("OFF", "A", "A#", "B")
    xml = AUDIT_XML.replace(
        b'<Parameter name="CELL" type="comboBox" stepNames="LOW,HIGH"',
        b'<Parameter name="CELL" type="comboBox" stepNames="OFF,A,A#,B"')
    rows = [_reading(notes, 1, "Ay")]
    mod = _with_readings(monkeypatch, tmp_path, rows)
    monkeypatch.setattr(mod, "MEANING_DISAGREEMENTS", {notes: {1: "A"}})
    with pytest.raises(SystemExit) as caught:
        mod.render(catalog.parse_model_repo(xml), snapshot="s")
    assert "does not say which" in str(caught.value)

"""The generated constants still match the unit's own catalog.

`models.py` and `params.py` are generated from a device's ModelRepo and then
COMMITTED, so they are a snapshot. A firmware or content update can renumber a
parameter or add a model, and nothing offline can notice - the generated file is
its own yardstick. This regenerates from the connected unit and compares.

Which snapshot it compares against comes from the CONNECTED PROFILE (ADR-0020)
rather than from a fixed path, so a unit on some other firmware is held against
its own snapshot, or told it has none yet.

A failure here is not necessarily a bug. It means the snapshot is stale, and the
fix is to regenerate that profile's snapshot and read the diff before committing
it:

    python scripts/generate_models.py --snapshot coros_4_0_1
    python scripts/generate_params.py --snapshot coros_4_0_1
    python scripts/generate_options.py --snapshot coros_4_0_1

Read the diff. A renumbered parameter is a real protocol change and belongs in
`docs/protocol.md`; a new model is routine.
"""
import importlib.util
import pathlib

import pytest

from pyquadcortex.protocol import catalog
from pyquadcortex.protocol.support import NoSnapshot

REPO = pathlib.Path(__file__).resolve().parents[2]


def _generator(name):
    spec = importlib.util.spec_from_file_location(
        f"qc_{name}", REPO / "scripts" / f"generate_{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def live_catalog(qc):
    return catalog.parse_model_repo(qc._fetch_model_repo())


@pytest.mark.parametrize("name", ["models", "params", "options"])
def test_the_committed_snapshot_matches_this_unit(live_catalog, profile, name):
    """Read-only. Compares against the CONNECTED profile's snapshot (ADR-0020),
    so a snapshot for another firmware never turns this unit's run red."""
    snapshot = getattr(profile, name)
    if isinstance(snapshot, NoSnapshot):
        pytest.fail(f"{profile.__name__} has no {name} snapshot yet; run "
                    f"scripts/generate_{name}.py --snapshot <coros_x_y_z> against this unit")
    generated = _generator(name).render(live_catalog,
                                        snapshot=snapshot.__name__.rsplit(".", 2)[-2])
    committed = pathlib.Path(snapshot.__file__).read_text(encoding="utf-8")
    if generated == committed:
        return

    gen_lines = generated.splitlines()
    com_lines = committed.splitlines()
    first = next((i for i, (a, b) in enumerate(zip(gen_lines, com_lines)) if a != b),
                 min(len(gen_lines), len(com_lines)))
    pytest.fail(
        f"{snapshot.__name__} no longer matches this unit's catalog. "
        f"First difference at line {first + 1}:\n"
        f"  committed: {com_lines[first] if first < len(com_lines) else '<end of file>'}\n"
        f"  this unit: {gen_lines[first] if first < len(gen_lines) else '<end of file>'}\n"
        f"Regenerate with `python scripts/generate_{name}.py --snapshot "
        f"{snapshot.__name__.rsplit('.', 2)[-2]}` and READ the diff - "
        f"a renumbered parameter is a protocol change, not a routine update."
    )


def test_the_cab_layout_claim_still_holds(qc, live_catalog):
    """`params.Cabsim` asserts every cab shares the Default Cabsim layout.

    That is the one claim in the generated file the catalog cannot support -
    the catalog lists two parameters per cab and the wire carries 22 - so it is
    held against the catalog entry the layout is taken FROM, and against the
    cab models still being under-described in the way that made this necessary.
    """
    from pyquadcortex.protocol import params

    reference = live_catalog[12000]
    assert len(reference.parameters) == len(params.Cabsim), (
        "the Default Cabsim layout changed size; params.Cabsim is derived from it")

    cabs = [m for m in live_catalog
            if m.category in ("Cabsim Guitar (M)", "Cabsim Guitar (ST)",
                              "Cabsim Bass (M)", "Cabsim Bass (ST)")
            and m.is_factory]
    assert cabs, "no factory cabs on this unit"
    assert all(len(m.parameters) == 2 for m in cabs), (
        "a cab now publishes more than its two mic selectors - the catalog may "
        "have started describing cabs properly, which would make the shared "
        "params.Cabsim layout unnecessary")

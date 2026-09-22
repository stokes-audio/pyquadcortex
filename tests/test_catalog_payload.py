"""The committed `ModelRepo` payload still generates the committed snapshot.

`tests/fixtures/catalog/model_repo_coros_4_0_1.bin` is the reply the 4.0.1 unit
sent on 2026-09-22. It exists so a snapshot can be regenerated with no unit
attached (ADR-0022), which is the only way a contributor without that firmware
can touch the generated catalog at all.

A saved input nothing reads is a file that rots silently. The offline suite
would pass identically if this payload were truncated, from the wrong firmware,
or replaced with noise, and `tests/hardware/test_generated_constants.py` cannot
help: it reads the unit, not this file. So the check that matters is the one
below - regenerate from the payload and compare with what is committed.

This is NOT the hardware check. That one asks whether the snapshot still matches
the UNIT, and only a unit can answer it. This one asks whether the snapshot
still matches its own recorded input, which is what makes regeneration
reproducible for someone who has no unit.
"""
import importlib.util
import pathlib

import pytest

from pyquadcortex.protocol import catalog

REPO = pathlib.Path(__file__).resolve().parents[1]

#: Each committed payload and the snapshot package it generates. A new profile
#: that saves its payload adds a row here, and is then held the same way.
PAYLOADS = [
    ("coros_4_0_1", "tests/fixtures/catalog/model_repo_coros_4_0_1.bin"),
]

GENERATORS = ("models", "params", "options")


def _generator(name):
    spec = importlib.util.spec_from_file_location(
        f"qc_{name}", REPO / "scripts" / f"generate_{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module", params=PAYLOADS, ids=[s for s, _ in PAYLOADS])
def payload(request):
    snapshot, relative = request.param
    path = REPO / relative
    assert path.exists(), f"{relative} is missing; it is the snapshot's input"
    return snapshot, catalog.parse_model_repo(path.read_bytes())


@pytest.mark.parametrize("name", GENERATORS)
def test_the_payload_still_generates_the_committed_snapshot(payload, name):
    """Regenerate from the saved payload and compare, byte for byte.

    A failure means the payload and the snapshot have parted company. Either
    someone edited a generated file by hand, which nothing may do, or a
    generator changed its output and the snapshot was not regenerated with it.
    The fix is to run the generator, never to edit the file:

        python scripts/generate_<name>.py --snapshot <snapshot> \\
            --payload tests/fixtures/catalog/model_repo_<snapshot>.bin
    """
    snapshot, parsed = payload
    generated = _generator(name).render(parsed, snapshot=snapshot)
    committed_path = (REPO / "pyquadcortex" / "protocol" / "catalogs"
                      / snapshot / f"{name}.py")
    committed = committed_path.read_text(encoding="utf-8")
    if generated == committed:
        return

    gen, com = generated.splitlines(), committed.splitlines()
    first = next((i for i, (a, b) in enumerate(zip(gen, com)) if a != b),
                 min(len(gen), len(com)))
    pytest.fail(
        f"{snapshot}/{name}.py no longer matches what the committed payload "
        f"generates. First difference at line {first + 1}:\n"
        f"  committed: {com[first] if first < len(com) else '<end of file>'}\n"
        f"  generated: {gen[first] if first < len(gen) else '<end of file>'}\n"
        f"Regenerate with `python scripts/generate_{name}.py --snapshot "
        f"{snapshot} --payload tests/fixtures/catalog/model_repo_{snapshot}.bin` "
        f"and read the diff. Never edit a generated file by hand.")


def test_the_payload_is_the_catalog_it_claims_to_be(payload):
    """A shape tripwire, so a truncated or wrong-firmware payload is caught here.

    The numbers are the 4.0.1 catalog's own, recorded when the generators last
    ran: 533 models, of which 414 are factory across 22 categories. They are
    asserted rather than derived so that replacing the file with a different
    unit's reply fails loudly instead of quietly regenerating a new snapshot.
    """
    snapshot, parsed = payload
    if snapshot != "coros_4_0_1":
        pytest.skip(f"no recorded shape for {snapshot}")
    factory = [m for m in parsed if m.is_factory]
    assert (len(parsed), len(factory)) == (533, 414)
    assert len({m.category for m in factory}) == 22

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
import json
import pathlib
import sys

import pytest

from pyquadcortex.protocol import catalog

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))

import _snapshots  # noqa: E402  (snapshot_version, so the version is derived not repeated)

#: Each committed payload, the snapshot package it generates, and that
#: catalog's recorded shape as `(models, factory models, factory categories)`.
#: A new profile that saves its payload adds a row here, and is then held the
#: same way. The shape lives in the row rather than in the test, so a payload
#: cannot be added and silently go unchecked.
PAYLOADS = [
    ("coros_4_0_1", "tests/fixtures/catalog/model_repo_coros_4_0_1.bin", (533, 414, 22)),
]

GENERATORS = ("models", "params", "options")


#: `tests/test_generators.py` already loads these, registers them in
#: `sys.modules` and caches them. Reuse it rather than keeping a second copy:
#: each bare exec of a generator inserts the repo root and `scripts/` at
#: `sys.path[0]` again, and those entries outlive the test that made them.
from test_generators import _load as _generator  # noqa: E402


@pytest.fixture(scope="module", params=PAYLOADS, ids=[row[0] for row in PAYLOADS])
def payload(request):
    snapshot, relative, shape = request.param
    path = REPO / relative
    assert path.exists(), f"{relative} is missing; it is the snapshot's input"
    return snapshot, catalog.parse_model_repo(path.read_bytes()), shape


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
    snapshot, parsed, _ = payload
    generated = _generator(f"generate_{name}").render(parsed, snapshot=snapshot)
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


def test_the_payload_still_has_the_shape_its_row_records(payload):
    """A named shape, so a swapped payload says what changed and not just where.

    This catches nothing the byte-for-byte test misses: a truncated payload
    raises while the fixture parses it, and a different unit's reply already
    fails the comparison above. What it adds is the failure message. "533 models
    became 471" names the problem; "first difference at line 812" does not.
    """
    _, parsed, (models, factory_models, factory_categories) = payload
    factory = [m for m in parsed if m.is_factory]
    assert (len(parsed), len(factory)) == (models, factory_models)
    assert len({m.category for m in factory}) == factory_categories


def test_every_payload_records_which_unit_produced_it(payload):
    """A payload cannot say what firmware it came from, so a sibling file does.

    The XML root carries no attributes and the tar member no metadata, so
    nothing in the payload states its own origin. Without the record beside it,
    "this is the 4.0.1 catalog" rests on the committer's word and no later
    reader can check which unit generated the snapshot.

    The record holds the firmware fields of the `Version` reply and leaves out
    the serial number, MAC address and custom name. Those identify an owner's
    unit and say nothing about the firmware.
    """
    snapshot, _, _ = payload
    relative = next(r for s, r, _ in PAYLOADS if s == snapshot)
    record = (REPO / relative).with_suffix(".provenance.json")
    assert record.exists(), (
        f"{record.relative_to(REPO)} is missing. A payload with no provenance "
        f"record cannot be checked against the firmware it claims to be from.")
    data = json.loads(record.read_text(encoding="utf-8"))
    reply = data["version_reply"]
    assert reply["zenos_git_hash"] == _snapshots.snapshot_version(snapshot)
    assert reply["device_type"] and reply["app_fw_version"]
    for owners in ("device_serial_number", "mac_address", "custom_name"):
        assert owners not in reply, (
            f"{owners} identifies a unit's owner, not its firmware; "
            f"it does not belong in a committed record")

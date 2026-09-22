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
import functools
import hashlib
import json
import pathlib

import pytest

from pyquadcortex.protocol import catalog
from test_generators import _load

REPO = pathlib.Path(__file__).resolve().parents[1]

#: `test_generators._load` is the one loader for these scripts, so this file
#: borrows it rather than keeping a second copy. It does NOT cache: it re-execs
#: on every call, and every generator inserts the repo root and `scripts/` at
#: `sys.path[0]` as it runs, two entries each, which nothing removes.
#:
#: The wrapper saves exactly one exec today. This module makes five calls over
#: four distinct names: the three generators once each, and `_snapshots` twice,
#: for `GENERATORS` below and again in the provenance test. It earns more as
#: `PAYLOADS` grows, because every row asks the same names again.
_script = functools.cache(_load)

#: Each committed payload, the snapshot package it generates, and that
#: catalog's recorded shape as `(models, factory models, factory categories)`.
#: A new profile that saves its payload adds a row here, and is then held the
#: same way. The shape lives in the row rather than in the test so that each
#: payload carries its own. It is filled in from the payload being added, so it
#: cannot fail then; it earns its place later, as the only thing that notices a
#: model the snapshot generates no constants for. See
#: `test_the_payload_still_has_the_shape_its_row_records`.
PAYLOADS = [
    ("coros_4_0_1", "tests/fixtures/catalog/model_repo_coros_4_0_1.bin", (533, 414, 22)),
]

#: Taken from `_snapshots.MODULES`, which with `EXPORTS` beside it is what the
#: snapshot package's own `__init__` is built from, and what
#: `tests/test_generators.py` pins. A fourth generator still has to be added
#: there, but only there, instead of here as well.
GENERATORS = _script("_snapshots").MODULES


@pytest.fixture(scope="module", params=PAYLOADS, ids=[row[0] for row in PAYLOADS])
def payload(request):
    snapshot, relative, shape = request.param
    path = REPO / relative
    assert path.exists(), f"{relative} is missing; it is the snapshot's input"
    return snapshot, relative, catalog.parse_model_repo(path.read_bytes()), shape


@pytest.mark.parametrize("name", GENERATORS)
def test_the_payload_still_generates_the_committed_snapshot(payload, name):
    """Regenerate from the saved payload and compare, byte for byte.

    A failure means the payload and the snapshot have parted company. Either
    someone edited a generated file by hand, which nothing may do, or a
    generator changed its output and the snapshot was not regenerated with it,
    or - for `options.py` alone - a row was added to
    `tests/fixtures/catalog/option_readings.json`, which `generate_options`
    reads besides the payload. The answer is the same in all three cases.
    The fix is to run the generator, never to edit the file:

        python scripts/generate_<name>.py --snapshot <snapshot> \\
            --payload tests/fixtures/catalog/model_repo_<snapshot>.bin
    """
    snapshot, relative, parsed, _ = payload
    generated = _script(f"generate_{name}").render(parsed, snapshot=snapshot)
    committed_path = (REPO / "pyquadcortex" / "protocol" / "catalogs"
                      / snapshot / f"{name}.py")
    committed = committed_path.read_text(encoding="utf-8")
    if generated == committed:
        return

    gen, com = generated.splitlines(), committed.splitlines()
    first = next((i for i, (a, b) in enumerate(zip(gen, com)) if a != b),
                 min(len(gen), len(com)))
    if gen == com:                          # identical lines, so it is the tail
        pytest.fail(
            f"{snapshot}/{name}.py has the same lines as the payload generates "
            f"but not the same bytes: {len(committed)} committed against "
            f"{len(generated)} generated. Suspect the trailing newline.")
    pytest.fail(
        f"{snapshot}/{name}.py no longer matches what the committed payload "
        f"generates. First difference at line {first + 1}:\n"
        f"  committed: {com[first] if first < len(com) else '<end of file>'}\n"
        f"  generated: {gen[first] if first < len(gen) else '<end of file>'}\n"
        f"Regenerate with `python scripts/generate_{name}.py --snapshot "
        f"{snapshot} --payload {relative}` and read the diff. "
        f"Never edit a generated file by hand.")


def test_every_committed_payload_has_a_row():
    """`PAYLOADS` covers every payload in the fixture directory.

    `CLAUDE.md` protects `tests/fixtures/catalog/*.bin` and says this file holds
    each one to the snapshot it generates. That is only true while the two
    agree: a payload committed with no row here is checked by nothing, while the
    rule says it is covered. This is the assertion that keeps the sentence true.
    """
    directory = REPO / "tests" / "fixtures" / "catalog"
    committed = {p.relative_to(REPO).as_posix() for p in directory.glob("*.bin")}
    listed = {relative for _, relative, _ in PAYLOADS}
    assert committed == listed, (
        f"payloads with no PAYLOADS row: {sorted(committed - listed)}; "
        f"rows with no payload: {sorted(listed - committed)}. Add the row, or "
        f"the file is protected by CLAUDE.md and checked by nothing.")


def test_the_payload_still_has_the_shape_its_row_records(payload):
    """The shape its row records, which is the one thing the comparison misses.

    Nearly everything is caught above: a truncated payload raises while the
    fixture parses it, and another unit's catalog fails the byte-for-byte
    comparison. One thing is not. The snapshot holds FACTORY content only, by
    design and by its own docstrings, so a model this repository generates no
    constants for can appear in the catalog without moving a single generated
    byte.

    Measured, not supposed: adding one model that carries a `sku` and a
    `plugin_id`, whose only parameter is a float and which therefore joins no
    option list, leaves `models.py`, `params.py` and `options.py` byte for byte
    identical. The model count is the only thing that moves.

    That case is ADR-0022's first open question - whether a unit that has
    bought an Archetype publishes anything the maintainer's unit does not - so
    this assertion is the guard for it rather than a nicer error message. It is
    also a nicer error message: "533 models became 534" names the problem where
    "first difference at line 812" would not.
    """
    _, _, parsed, (models, factory_models, factory_categories) = payload
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
    snapshot, relative, _, _ = payload
    payload_path = REPO / relative
    record = payload_path.with_name(payload_path.stem + ".provenance.json")
    assert record.exists(), (
        f"{record.relative_to(REPO)} is missing. A payload with no provenance "
        f"record cannot be checked against the firmware it claims to be from.")
    raw = record.read_text(encoding="utf-8")
    data = json.loads(raw)
    reply = data["version_reply"]
    assert reply["zenos_git_hash"] == _script("_snapshots").snapshot_version(snapshot)
    assert reply["device_type"] and reply["app_fw_version"]

    # The record also states the size and digest of the file beside it, and a
    # record describing some earlier capture is worse than none. Re-reading the
    # catalog from the same unit produces a payload that parses identically, so
    # every comparison above still passes while these two go stale in silence.
    # This is not comparing two payloads by hash, which ADR-0022 forbids; it is
    # checking that a record describes the file it sits next to.
    blob = payload_path.read_bytes()
    assert data["payload_bytes"] == len(blob), (
        f"{record.name} records {data['payload_bytes']} bytes and "
        f"{payload_path.name} is {len(blob)}. Update the record with the payload.")
    assert data["payload_sha256"] == hashlib.sha256(blob).hexdigest(), (
        f"{record.name}'s digest is not {payload_path.name}'s. The payload was "
        f"replaced without its record; regenerate both together.")

    # Scanned over the whole document, not just `version_reply`: a field moved
    # to the top level or pasted into a comment would pass a keys-only check.
    for owners in ("device_serial_number", "mac_address", "custom_name"):
        assert owners not in raw, (
            f"{owners} identifies a unit's owner, not its firmware; "
            f"it does not belong in a committed record")

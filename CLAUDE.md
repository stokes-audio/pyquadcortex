# pyquadcortex - Claude Guidance

Steering for this repo: `docs/STEERING.md`
Decisions for this repo: `docs/ADR.md`

Read `docs/STEERING.md` before non-trivial work (new operations, transport or framing changes, unfamiliar subsystems). Skip for trivial changes (typos, dep bumps, docs).

## Conventions

- Dev setup: `uv venv && uv pip install -e ".[dev]"` (or plain venv + pip, see contributing.md). Run tests with `.venv/bin/python -m pytest`. The suite passes offline - no hardware, no `hid` import, no `DYLD_LIBRARY_PATH`.
- Two namespaces, one package (ADR-0006): `pyquadcortex` is the model of the unit, `pyquadcortex.protocol` is the message-level API. The model's code lives in `pyquadcortex/device/` - not `model/`, because in this codebase the identifier `model` means an amp or pedal block (`protocol/models.py`, `catalog.Model`, `ModelCatalog`, `set_block(model=...)`). The model imports the protocol layer; nothing under `pyquadcortex/protocol/` may import from `pyquadcortex/device/`.
- Every conversion between a screen value and a wire value lives in `pyquadcortex/device/translate.py` and nowhere else in the model: rows 1-4, slots 1-8, scene and footswitch letters, preset addresses, display units. No `+1`/`-1` on a coordinate outside it, and no model module reaching past it for a protocol conversion helper - `tests/test_translation.py` reads the source and proves both. A model API takes `FootswitchLetter`, never a bare footswitch integer, because a footswitch index and a block's column are different numbers that usually agree. A new conversion goes in that module with its own test, however small it is.
- The model represents what the unit shows, in the unit's own words, and never guesses. A control we understand but cannot yet drive is modelled and REFUSES the operation (ADR-0007); a control we do not understand is omitted, with the reason recorded in `docs/domain-model.md`'s appendix. Nothing ships with a "this might be stale or wrong" caveat.
- A model property that reads a device field checks the field is PRESENT (`protocol.field_present`) before reporting it. Most of this schema sits in synthetic `oneof`s, so protobuf returns `""` or `0` for a field the unit never sent, and reporting that as the answer is the guess the rule above forbids. Never cache a reply that came back incomplete - a retry has to be able to recover.
- Anything the model caches is valid only while its connection is. A closed `Device` refuses reads rather than answering from cache, because a model that reports the unit's state through an object with no unit behind it is the failure the whole layer exists to avoid.
- `import hid` appears exactly once, lazily, inside `session.open_device()`. Never import `hid` at module scope. A new module that needs it imports it inside the function that opens the device; `tests/test_import_cleanliness.py` walks the whole package and proves it.
- Never gitignore or delete `pyquadcortex/protocol/proto/*_pb2.py` - the generated bindings are committed on purpose (ADR-0001, written before the proto directory was moved). Regenerate only via `scripts/compile_protos.sh`, and bump the `protobuf` pin in `pyproject.toml` in the same commit as regenerated bindings. The `grpcio-tools` floor in the dev extra is part of that same commit: `grpcio-tools` carries its own protoc, so the installed version decides the gencode, and an older one emits older gencode that still imports and quietly walks the pin backwards (ADR-0008). Both directions are now guarded - the script refuses to write a downgrade, and `tests/test_packaging.py` proves the committed gencode equals the pin floor - so trust the failure and fix the cause rather than working around either. Never read the floor off `grpcio-tools` metadata; 1.82.1 declares `protobuf>=7.35.1` and emits 7.35.0. Run the compiler and read the stamp. CI's `build` job runs `scripts/check_artifacts.py`, which proves the bindings are inside the wheel and the sdist.
- New operations follow `docs/architecture.md` "How to add a new operation": register the type, add a thin client method (no HID, no bytes, no sleeps in `protocol/client.py`), add an offline test asserting the exact wire shape, then verify on hardware and update the coverage table in `docs/protocol.md`.
- Grid mutations use the row/column-keyed pattern (`set_param` / `set_bypass`) - never extend the wholesale `write_preset` path.
- Docstrings state their evidence: confirmed on hardware vs inferred from the schema. When you verify something on hardware, record it (docstring + coverage table) in the same change.
- Code in the RX path preserves "the RX thread never dies": wrap every decode, skip unknown types at debug level, reset the reassembly buffer on anything malformed.
- Hardware sessions: quit Cortex Control first - it holds the HID interface exclusively.
- Describe the protocol work as documenting the device's protocol as-is (recovered schema, observed traffic). Do not call it "reverse engineering" in docs, comments, commit messages, or issues.
- Changed code under a path listed in `docs/STEERING.md` § Owned Paths? Diff and update STEERING/CLAUDE/ADR in the same PR.

## Do not

- Send anything to the firmware `Updater` surface - permanently out of scope; a botched firmware write is the one mistake a factory reset cannot fix.
- Drive cloud or account messages (`CloudLogin`, `CloudBackup`, capture sharing) without the owner's explicit go-ahead.
- Depend on the two unexplained trailer bytes or the raw-payload flag inference.
- Run the IR-import probing unattended - a past run killed the USB link and required a power cycle.
- Treat the schema as ground truth for unobserved message types - it is a starting hypothesis until verified on hardware.

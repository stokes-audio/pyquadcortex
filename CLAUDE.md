# pyquadcortex - Claude Guidance

Steering for this repo: `docs/STEERING.md`
Decisions for this repo: `docs/ADR.md`

Read `docs/STEERING.md` before non-trivial work (new operations, transport or framing changes, unfamiliar subsystems). Skip for trivial changes (typos, dep bumps, docs).

## Conventions

- Dev setup: `uv venv && uv pip install -e ".[dev]"` (or plain venv + pip, see contributing.md). Run tests with `.venv/bin/python -m pytest`. The suite passes offline - no hardware, no `hid` import, no `DYLD_LIBRARY_PATH`.
- Two namespaces, one package (ADR-0006): `pyquadcortex` is the model of the unit, `pyquadcortex.protocol` is the message-level API. The model imports the protocol layer; nothing under `pyquadcortex/protocol/` may import from `pyquadcortex/model/`.
- The model represents what the unit shows, in the unit's own words, and never guesses. A control we understand but cannot yet drive is modelled and REFUSES the operation (ADR-0007); a control we do not understand is omitted, with the reason recorded in `docs/domain-model.md`'s appendix. Nothing ships with a "this might be stale or wrong" caveat.
- `import hid` appears exactly once, lazily, inside `session.open_device()`. Never import `hid` at module scope. A new module that needs it imports it inside the function that opens the device; `tests/test_import_cleanliness.py` walks the whole package and proves it.
- Never gitignore or delete `pyquadcortex/protocol/proto/*_pb2.py` - the generated bindings are committed on purpose (ADR-0001, written before the proto directory was moved). Regenerate only via `scripts/compile_protos.sh`, and bump the `protobuf` pin in `pyproject.toml` in the same commit as regenerated bindings.
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

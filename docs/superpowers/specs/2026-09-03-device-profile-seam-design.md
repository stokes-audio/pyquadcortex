# Device profile seam - design

Date: 2026-09-03. Implements ADR-0020. Status: approved in discussion, awaiting
review of this document.

## What this builds

A connection resolves which device profile it is talking to before the handshake,
and the profile decides everything that differs by firmware or model. Today the
library has one profile, Quad Cortex on CorOS 4.0.1, and it is not written down
as one. After this work it has one real profile, two stubs that show how the
next ones arrive, and a mechanism that makes "does this operation work on my
unit" a fact the library states rather than a guess it makes.

What ships:

- `QuadCortex` is the 4.0.1 profile and the base class. Unchanged behaviour.
- `QuadCortex41` and `QuadCortexMini` are stubs that guide their own completion.
- `connect()` reads the unit's identity, resolves a class, and refuses the unknown.
- Generated constants live in per-firmware snapshot packages; the old import paths
  keep working as the baseline.
- Every operation a profile has not verified refuses by default and runs with a
  warning when the caller asks for `Support.EXPERIMENTAL`.
- The hardware suite reports, per profile, which operations passed.
- `set_block` checks the model id against the live catalog before sending.

What does not ship: `QuadCortex41`'s catalog snapshot and verified set. Those
need a 4.1 unit and belong to PR #44 once this lands.

## Decisions taken, and why

These were settled in discussion on 2026-09-03; the record is here so the
plan does not reopen them.

1. **The client class is the profile (option C).** `QuadCortex` stays the base
   and stays 4.0.1; there is no abstract layer between it and the first
   extension, because nothing older than 4.0.1 has ever been supported and none
   is expected. `QuadCortex41` is a subclass with overrides only. Firmware
   variants share one type, so callers annotate `QuadCortex` and need not know
   which they got. A Mini may need its own type because its hardware differs;
   that decision waits for a Mini. Rejected: a separate profile object the
   client delegates to (one more indirection whose hooks exist only to be
   called), and version checks in method bodies (the smell polymorphism removes).
2. **The class name tracks the firmware line, not the patch.** `QuadCortex41`,
   not `QuadCortex410`. If a 4.1.1 needs its own class, rename then. The class
   carries `MEASURED_ON`, the exact CorOS strings it has been run against, so an
   unlisted patch release still refuses until someone adds it after a suite run.
3. **Strict by default, lenient by explicit choice.** An inherited operation
   the profile has not verified refuses. The unit accepts a wrong write
   silently and offers no capability negotiation, so the safety net lenient
   libraries rely on does not exist here; the verified set is its substitute.
   The lenient mode exists because the hardware suite needs it to measure a
   new profile.
4. **The user-facing name is `Support`.** `connect(support=Support.VERIFIED)`
   is the default; `Support.EXPERIMENTAL` opts in. "Unmeasured" is our internal
   rule and stays internal (`VERIFIED` set, `MEASURED_ON` tuple on the class).
   Rejected names: `Compatibility.BEST_EFFORT`, `Verified.ASSUMED`,
   `Operations.ALL`.
5. **Snapshot packages per firmware (option A).** One package per generated
   catalog, named by CorOS version. Rejected: one module with version-tagged
   names, because a caller could import a constant their unit lacks and two
   names would point at one id.
6. **Both known future profiles are stubbed.** They mean different things: the
   Mini cannot connect (nothing measured); 4.1 connects and verifies nothing
   (the connection is known to work from a contributor's full suite run, the
   operations are not known per name).

## Section 1: the registry and connect

### Modules

Two new modules, so the import graph stays one-way. `client.py` needs the
vocabulary (to guard methods and store `support`); the stubs need `client.py`
(they subclass it); `session.py` needs both.

`pyquadcortex/protocol/support.py` - vocabulary only, imports nothing from
the package:

- `class Support(Enum)`: `VERIFIED`, `EXPERIMENTAL`.
- `class Evidence(Enum)`: `MAINTAINER` (measured on the maintainer's unit),
  `CONTRIBUTED` (measured by a contributor, not reproduced by the maintainer),
  `STUB` (nothing measured).
- `EVERYTHING`: the sentinel `VERIFIED` value meaning "all operations".
- `class Hardware` (frozen dataclass): `footswitches`, `expression_ports`.
- `class NoSnapshot`: the placeholder described in section 2.
- `unverified_text(cls, name)`: the one function that words a refusal, a
  warning, and the `UnsupportedDevice` hint.

`pyquadcortex/protocol/profiles.py` - imports `client.py`:

- `class QuadCortex41(QuadCortex)` and `class QuadCortexMini(QuadCortex)`, the
  stubs (section 5).
- `class UnsupportedDevice(Exception)`: raised when resolution fails. Carries
  `device_type`, `coros_version`, and the registered profiles, and its message
  names what the unit said and what is registered, without offering a fallback.
- `registry() -> dict[(DeviceType, str), type[QuadCortex]]`: built from
  `QuadCortex` plus `QuadCortex._PROFILES`, the list `QuadCortex.__init_subclass__`
  appends every subclass to. One entry per `MEASURED_ON` string; a class with
  an empty `MEASURED_ON` (the Mini) has no entry but is kept in a separate
  `stubs()` list so the refusal can name it.
- `resolve(version_message) -> type[QuadCortex]`: reads `device_type` and
  `zenos_git_hash` from the reply, looks the pair up, raises `UnsupportedDevice`
  otherwise, naming a matching stub when the `device_type` has one.

`session.py` imports `profiles.py`. `pyquadcortex.protocol` re-exports
`Support`, `Evidence`, `UnsupportedDevice`, `QuadCortex41`, `QuadCortexMini`.

### Class attributes every profile declares

On `QuadCortex` (values for 4.0.1):

| attribute | value on `QuadCortex` | meaning |
|---|---|---|
| `DEVICE_TYPE` | `DeviceType.QC` | the `Version.device_type` this class serves |
| `MEASURED_ON` | `("4.0.1",)` | exact `zenos_git_hash` strings a suite run has been done against |
| `CC_VERSION` | `"4.0.1"` (exists today) | the announce string the handshake sends |
| `EVIDENCE` | `Evidence.MAINTAINER` | how well the profile is known |
| `models`, `params`, `options` | the `coros_4_0_1` modules | the constants snapshot (section 2) |
| `HARDWARE` | `Hardware(footswitches=8, expression_ports=2)` | facts the model layer needs; a frozen dataclass, extended only when a measurement needs a new field |
| `VERIFIED` | `EVERYTHING` (a sentinel) | operation names verified on this profile (section 3) |

`QuadCortex41` declares: `MEASURED_ON = ("4.1.0",)`, `EVIDENCE =
Evidence.CONTRIBUTED`, `VERIFIED = frozenset()`, and the three snapshot
attributes bound to `NoSnapshot("coros_4_1_0")`. Everything else inherits.

`QuadCortexMini` declares: `DEVICE_TYPE = DeviceType.ATMA`, `MEASURED_ON = ()`,
`EVIDENCE = Evidence.STUB`, `VERIFIED = frozenset()`, `HARDWARE =
Hardware(footswitches=4, expression_ports=2)` with a comment naming the product
page as the source, snapshots `NoSnapshot("coros_mini")`. Its docstring is the
outline for a contributor: pass `profile=QuadCortexMini` and
`support=Support.EXPERIMENTAL`, run the hardware suite, send the report.

### connect

`protocol.connect(*, timeout, settle, handshake_patience, before_handshake,
profile=None, support=Support.VERIFIED)`:

1. Open the device and start the transport, as today.
2. Call `before_handshake(transport)` if given, as today.
3. Read `Version` through a bare `QuadCortex(transport)` using the identity
   predicate from PR #51. This is the same pre-handshake read the CLI's
   `version` subcommand has always used.
4. Resolve: `cls = profile or resolve(reply)`. When `profile` is given, check
   `reply.device_type == cls.DEVICE_TYPE`; a mismatch raises `UnsupportedDevice`
   ("you asked for QuadCortexMini and the unit says QC"). `profile=` does not
   check `MEASURED_ON`; that is its purpose.
5. Instantiate `cls(transport, _owned_resources=owned, support=support)` and
   run its `_hello()`, which announces `cls.CC_VERSION`. Retry logic unchanged.
6. Any failure closes what was opened, as today.

`pyquadcortex.connect()` (the model layer) gains the same two keywords and
passes them through; it returns a `Device` around whichever client came back.
`Device.from_client` is unchanged.

### Errors

- Unknown pair, no `profile=`: `UnsupportedDevice`, message: "the unit reports
  device_type QC, CorOS 4.2.0; this library has profiles for QC 4.0.1
  (verified on the maintainer's unit) and QC 4.1.0 (contributed). Pass
  `profile=QuadCortex41` to treat it as 4.1 while you measure it, with
  `support=Support.EXPERIMENTAL`." The nearest profile is named as a choice for
  the caller, never taken for them.
- Mini: `UnsupportedDevice` naming the stub and the how-to-start.
- `profile=` with the wrong `device_type`: `UnsupportedDevice`.

## Section 2: constants snapshots

### Layout

```
pyquadcortex/protocol/catalogs/__init__.py
pyquadcortex/protocol/catalogs/coros_4_0_1/__init__.py
pyquadcortex/protocol/catalogs/coros_4_0_1/models.py    (moved, unchanged content)
pyquadcortex/protocol/catalogs/coros_4_0_1/params.py
pyquadcortex/protocol/catalogs/coros_4_0_1/options.py
pyquadcortex/protocol/models.py     (shim: re-exports coros_4_0_1.models)
pyquadcortex/protocol/params.py     (shim)
pyquadcortex/protocol/options.py    (shim)
```

- The three generators take `--snapshot NAME` and write into
  `catalogs/NAME/`. The generated header records the snapshot name and the
  `zenos_git_hash` of the unit it was read from. `--out` is removed; one way to
  place a file.
- Each shim is hand-written, three lines: a docstring saying it is the
  maintainer's baseline per ADR-0020, `from .catalogs.coros_4_0_1.models import
  *`, and `from .catalogs.coros_4_0_1.models import ALL, __all__` (the names
  `import *` does not carry). `tests/test_translation.py`'s `BOUNDARY_MODULES`
  and any test that reads these files by path are updated to the new paths, and
  the packaging check proves the snapshot packages reach the wheel.
- `NoSnapshot(name)` is a small object whose `__getattr__` raises
  `AttributeError` with "no catalog snapshot for <profile> yet; run
  `scripts/generate_*.py --snapshot <name>` against that unit". It is what the
  two stubs bind.
- `QuadCortex.models`, `.params`, `.options` are class attributes bound to the
  `coros_4_0_1` modules. `qc.models` therefore follows the connection.
- The `params.py` module docstring example is rendered from the catalog being
  generated: the generator picks the first cab model in that catalog and writes
  its constant name into the example, so the name exists in that snapshot by
  construction. The template no longer hard-codes a bass cab that was renamed.

### Hardware comparison

`tests/hardware/test_generated_constants.py` renders from the connected unit
and compares against `profile.models`, `.params`, `.options`. On a profile bound
to `NoSnapshot`, the test fails with the generator command to run, which is the
correct outcome: it is the instruction for the person holding that unit.

## Section 3: enforcing "verified" without scattered checks

### Vocabulary

An **operation** is a public method of `QuadCortex` that sends to or waits on
the unit. `ALWAYS` is the explicit set of public methods that must work on any
profile because connecting and cleaning up depend on them: `version`,
`catalog`, `close`, `disconnect`, `add_listener`, `remove_listener`, and the
handshake (`_hello` is private and not an operation). Each entry carries a
one-line reason beside it. Everything public and not in `ALWAYS` is an
operation. `OPERATIONS` is computed from the class, not maintained by hand.

`EVERYTHING` is a sentinel value for `VERIFIED` meaning "all operations";
`QuadCortex` uses it.

### Mechanism

`QuadCortex.__init_subclass__(cls, **kw)`:

1. Appends `cls` to `QuadCortex._PROFILES`, which `profiles.registry()` reads.
2. If `cls.VERIFIED is EVERYTHING`, stops.
3. For every operation name defined on `QuadCortex` that `cls` inherits without
   overriding and that is not in `cls.VERIFIED`, replaces the attribute on `cls`
   with a guard wrapping the inherited function.

The guard, `_guarded(name, inherited)`, at call time reads `self._support`:

- `Support.VERIFIED`: raises `ControlNotDrivable(control=name,
  evidence=f"not yet verified on {cls.__name__} (CorOS {', '.join(MEASURED_ON)})",
  workaround=f"connect(support=Support.EXPERIMENTAL) to try it, or run
  `pytest tests/hardware --hardware --verifies {name}` on your unit and add
  the result to {cls.__name__}.VERIFIED")`. `--verifies NAME` is a conftest
  option that selects the tests marked with that operation (pytest's `-m`
  cannot match a marker's arguments). `ControlNotDrivable` is the refusal
  type CLAUDE.md requires; all three fields are set.
- `Support.EXPERIMENTAL`: logs one `WARNING` per operation name per instance
  ("{name} is not yet verified on {cls}; running it anyway") and calls the
  inherited function.

The message text comes from `support.unverified_text(cls, name)`, used by both
branches and by `UnsupportedDevice`, so wording cannot drift.

`QuadCortex.__init__` gains `support: Support = Support.VERIFIED` and stores it.
`qc.support` reads it back. `qc.unverified_operations` returns the set of
guarded names on `type(self)`: empty on `QuadCortex`.

A method the subclass overrides is left alone: an override is the subclass's
own measured behaviour. If a subclass overrides an operation, that name is
implicitly verified; the offline test below asserts it is also listed in
`VERIFIED`, so the set stays the single statement of what the class knows.

### Tests (offline)

- `tests/test_profiles.py`:
  - Every public method of `QuadCortex` is either in `ALWAYS` or is guarded on a
    throwaway subclass with `VERIFIED = frozenset()`. Catches a new method that
    slips past the mechanism.
  - `ALWAYS` holds only the lifecycle names, each with a reason (source-reading,
    like `BOUNDARY_MODULES`).
  - A guarded call under `VERIFIED` raises `ControlNotDrivable` with all three
    fields; under `EXPERIMENTAL` it runs the inherited function and logs once
    per name.
  - An override on a subclass is not guarded and must appear in `VERIFIED`.
  - `resolve()` maps `(QC, "4.0.1")` to `QuadCortex`, `(QC, "4.1.0")` to
    `QuadCortex41`, refuses `(QC, "4.2.0")` naming both, refuses `(ATMA, *)`
    naming the Mini stub and how to start.
  - `connect()` with a fake transport whose `Version` reply says 4.1.0 returns a
    `QuadCortex41` whose `set_scene_label` refuses and whose `version()` works;
    with `profile=QuadCortexMini` against a QC reply, raises.
  - `NoSnapshot` raises the pointed message on attribute access.
- `tests/test_typing.py` gains a case that `connect()` is typed as returning
  `QuadCortex` and that `Support.EXPERIMENTAL` is accepted where a `Support` is
  expected and a bare string is not.
- Existing suites pass unchanged except for import-path updates in the tests
  that read the generated files by path.

## Section 4: the hardware suite measures

- The session fixture connects with `support=Support.EXPERIMENTAL` and exposes
  `profile` (the connected class) beside `qc`. On `QuadCortex` this changes
  nothing.
- A marker `@pytest.mark.verifies("op", ...)` names the operations a hardware
  test exercises. Names are checked against `OPERATIONS` at collection; an
  unknown name is a collection error. `--verifies NAME` selects the tests that
  carry that name.
- At session end the conftest prints a report: profile, `MEASURED_ON`,
  operations whose marked tests all passed, operations whose marked tests
  failed or skipped, `passed - VERIFIED` (candidates to add) and
  `VERIFIED - passed` (verified operations that did not pass this run, named).
  On a stub or contributed profile the first difference is the content of that
  profile's PR.
- An offline test in `tests/test_hardware_gate.py`'s style asserts every
  operation is named by at least one hardware test's marker, with an explicit
  list of exceptions and a reason for each (operations that cannot be run
  state-neutrally, or that no test covers yet).
- Every existing hardware test is marked in this work. It is mechanical: the
  test already names the method it drives.
- A `scratch_preset` fixture copies the loaded preset into a free User slot
  under a fixed name, recalls it, yields its address, and on teardown recalls
  the original and deletes the copy. Tests that need a disposable preset use
  it instead of an environment variable naming one of the owner's slots. The
  PR #42 hardware test would use it once merged.

## Section 5: the stubs, the catalog check, the docs

- `QuadCortex41` and `QuadCortexMini` live in `profiles.py` beside the
  registry, each about eight lines plus a docstring that is the outline for
  finishing it. What a contributor adds for 4.1: the `coros_4_1_0` package,
  three attribute bindings, and `VERIFIED` filled from the report.
- `set_block` checks `cell.model_id in self.catalog` before sending and raises
  `ControlNotDrivable(control=model name or id, evidence="not in this unit's
  catalog (CorOS X)", workaround=names the profile whose snapshot has it, if
  any)`. The existing `UNPLACEABLE_MODELS` check stays first.
- Docs in the same PR: STEERING §5 gains a "Profile is the class" row and the
  change log an entry; CLAUDE.md's ADR-0020 bullet drops "the seam is not built
  yet" and states the mechanism; protocol.md's header names the profile
  classes; architecture.md's checklist becomes "adding a device profile";
  contributing.md gains that section; changelog names `Support`, `profile=`,
  `UnsupportedDevice`, `catalogs`, `qc.models` as new public surface, and the
  `--out` to `--snapshot` generator change as BREAKING for anyone scripting the
  generators.

## Error handling summary

- Unknown unit: `UnsupportedDevice` at connect, before any handshake.
- Known unit, unverified operation, default support: `ControlNotDrivable` at
  the call, before any bytes are sent.
- Known unit, unverified operation, experimental: one warning, then the
  inherited behaviour.
- Missing snapshot: `AttributeError` with the generator command, at attribute
  access.
- Model id the unit lacks: `ControlNotDrivable` before sending.
- Nothing here swallows an error or falls back to another profile.

## Out of scope

- `QuadCortex41`'s snapshot and verified set (PR #44, after this).
- Any Mini behaviour beyond the stub.
- A `MiniDevice` model type.
- Moving `protocol.models` off the 4.0.1 baseline.
- The `RecallPreset` retry and the other PR #44 observations.

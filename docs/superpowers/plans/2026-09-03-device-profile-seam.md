# Device Profile Seam Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `connect()` resolve a device profile from the unit's own `Version` reply, refuse unknown profiles, and make every operation a profile has not verified refuse by default - with one real profile (Quad Cortex 4.0.1), two stubs (4.1, Mini), per-firmware constants snapshots, and a hardware suite that reports which operations passed per profile.

**Architecture:** The client class IS the profile (ADR-0020, spec decision 1). `QuadCortex` stays the 4.0.1 base; subclasses declare class attributes and override only what a measurement showed differs. `QuadCortex.__init_subclass__` guards every inherited operation a subclass has not listed in `VERIFIED`. A new `support.py` holds the vocabulary (imports nothing from the package); a new `profiles.py` holds the stubs and the registry (imports the client); `session.py` resolves through it. Generated constants move to `protocol/catalogs/coros_4_0_1/`, with three-line shims at the old import paths.

**Tech Stack:** Python >= 3.11, protobuf bindings already committed, pytest, mypy (blocking CI job). Run tests with `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q` from the worktree (the worktree has no venv of its own; `PYTHONPATH` is what makes the run test THIS checkout). Never pass `--hardware`; nothing in this plan needs the unit.

**Spec:** `docs/superpowers/specs/2026-09-03-device-profile-seam-design.md`

## Global Constraints

- CLAUDE.md governs. Read it before Task 1. In particular: a refusal is `ControlNotDrivable(control, evidence, workaround)` with all three fields; `import hid` appears exactly once, in `session.open_device()`; nothing under `pyquadcortex/protocol/` imports from `pyquadcortex/device/`; docstrings state their evidence.
- The default test suite runs fully offline. Every test in this plan runs with no unit attached.
- mypy over `pyquadcortex/` must stay clean: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`.
- `protocol.models`, `protocol.params`, `protocol.options` keep importing and keep meaning the CorOS 4.0.1 snapshot (spec section 2).
- Names fixed by the spec: `Support.VERIFIED` / `Support.EXPERIMENTAL`; `Evidence.MAINTAINER` / `CONTRIBUTED` / `STUB`; `EVERYTHING`; `Hardware`; `NoSnapshot`; `unverified_text`; `UnsupportedDevice`; `QuadCortex41`; `QuadCortexMini`; class attributes `DEVICE_TYPE`, `MEASURED_ON`, `CC_VERSION`, `EVIDENCE`, `HARDWARE`, `VERIFIED`, `models`, `params`, `options`; `qc.support`, `qc.unverified_operations`; `connect(profile=, support=)`; generator flag `--snapshot`; marker `verifies`; option `--verifies`.
- Commit after every task with a message in the repo's style (`feat:`, `fix:`, `docs:`, `test:`). Never add a co-author line.
- Never call the work "reverse engineering" anywhere.

## File structure

Created:
- `pyquadcortex/protocol/support.py` - `Support`, `Evidence`, `EVERYTHING`, `Hardware`, `NoSnapshot`, `unverified_text`. Imports only the standard library.
- `pyquadcortex/protocol/profiles.py` - `QuadCortex41`, `QuadCortexMini`, `UnsupportedDevice`, `registry()`, `stubs()`, `resolve()`. Imports `client.py`.
- `pyquadcortex/protocol/catalogs/__init__.py`, `pyquadcortex/protocol/catalogs/coros_4_0_1/__init__.py` - the snapshot package.
- `pyquadcortex/protocol/catalogs/coros_4_0_1/{models,params,options}.py` - moved from `pyquadcortex/protocol/`.
- `tests/test_support.py`, `tests/test_profiles.py`, `tests/test_generators.py`, `tests/test_hardware_markers.py`.

Modified:
- `pyquadcortex/protocol/{models,params,options}.py` - become three-line shims.
- `pyquadcortex/protocol/client.py` - profile class attributes, `support` in `__init__`, `__init_subclass__`, `ALWAYS`, `operations()`, `unverified_operations`, `set_block` catalog check.
- `pyquadcortex/protocol/session.py` - `connect(profile=, support=)` resolves before the handshake.
- `pyquadcortex/protocol/__init__.py` - re-exports.
- `pyquadcortex/device/device.py` - `connect(profile=, support=)` pass-through.
- `scripts/generate_models.py`, `scripts/generate_options.py`, `scripts/generate_params.py` - `--snapshot`.
- `scripts/check_artifacts.py` - snapshot package in `REQUIRED`.
- `tests/conftest.py` - `verifies` marker and `--verifies` option.
- `tests/hardware/conftest.py` - `support=Support.EXPERIMENTAL`, `profile` fixture, `scratch_preset` fixture, `--verifies` selection, session report.
- `tests/hardware/test_generated_constants.py` - compares against the profile's snapshot.
- Every `tests/hardware/test_*.py` - `verifies` markers.
- `tests/test_values.py:197`, `tests/test_client.py` (connect tests), `tests/test_session.py`, `tests/typing/wrong_units.py`, `tests/test_typing.py`.
- Docs: `docs/STEERING.md`, `CLAUDE.md`, `docs/protocol.md`, `docs/architecture.md`, `contributing.md`, `changelog.md`, `docs/api.md`.

---

### Task 1: The vocabulary module `support.py`

**Files:**
- Create: `pyquadcortex/protocol/support.py`
- Test: `tests/test_support.py`

**Interfaces:**
- Produces: `Support` (Enum: `VERIFIED`, `EXPERIMENTAL`), `Evidence` (Enum: `MAINTAINER`, `CONTRIBUTED`, `STUB`), `EVERYTHING` (sentinel; `name in EVERYTHING` is always True), `Hardware(footswitches: int, expression_ports: int)` frozen dataclass, `NoSnapshot(name: str)` whose attribute access raises `AttributeError`, `unverified_text(cls, name) -> tuple[str, str]` returning `(evidence, workaround)`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_support.py
"""The vocabulary every profile speaks (spec section 1, "Modules")."""
import dataclasses

import pytest

from pyquadcortex.protocol import support


def test_support_has_exactly_the_two_modes_the_spec_names():
    assert {m.name for m in support.Support} == {"VERIFIED", "EXPERIMENTAL"}


def test_evidence_has_exactly_the_three_levels_the_spec_names():
    assert {e.name for e in support.Evidence} == {"MAINTAINER", "CONTRIBUTED", "STUB"}


def test_everything_contains_any_name_and_says_what_it_is():
    assert "set_scene_label" in support.EVERYTHING
    assert "anything_at_all" in support.EVERYTHING
    assert repr(support.EVERYTHING) == "EVERYTHING"


def test_hardware_is_frozen_and_takes_the_two_facts():
    hw = support.Hardware(footswitches=8, expression_ports=2)
    assert (hw.footswitches, hw.expression_ports) == (8, 2)
    with pytest.raises(dataclasses.FrozenInstanceError):
        hw.footswitches = 4  # type: ignore[misc]


def test_no_snapshot_refuses_every_attribute_and_names_the_generator():
    missing = support.NoSnapshot("coros_4_1_0")
    with pytest.raises(AttributeError) as caught:
        missing.Delay
    text = str(caught.value)
    assert "coros_4_1_0" in text
    assert "--snapshot coros_4_1_0" in text
    assert repr(missing) == "NoSnapshot('coros_4_1_0')"


class _Fake:
    __name__ = "QuadCortex41"
    MEASURED_ON = ("4.1.0",)


def test_unverified_text_names_the_profile_the_firmware_and_both_ways_out():
    evidence, workaround = support.unverified_text(_Fake, "set_scene_label")
    assert evidence == "not yet verified on QuadCortex41 (CorOS 4.1.0)"
    assert "connect(support=Support.EXPERIMENTAL)" in workaround
    assert "pytest tests/hardware --hardware --verifies set_scene_label" in workaround
    assert "QuadCortex41.VERIFIED" in workaround
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_support.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyquadcortex.protocol.support'`

- [ ] **Step 3: Write the module**

```python
# pyquadcortex/protocol/support.py
"""The words a device profile is described in (ADR-0020).

A PROFILE is a client class: ``QuadCortex`` for a Quad Cortex on CorOS 4.0.1,
a subclass for each other unit somebody has measured. This module holds the
vocabulary those classes declare themselves with and the one function that
words a refusal, so the text cannot drift between the places that use it.

It imports nothing from the package on purpose: ``client.py`` needs these
names to guard its methods, and the profile classes need ``client.py``, so
this is the module both can import without a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class Support(Enum):
    """How a connection treats an operation its profile has not verified.

    ``VERIFIED`` (the default) refuses it with ``ControlNotDrivable``.
    ``EXPERIMENTAL`` runs the inherited behaviour and logs one warning per
    operation. The hardware suite always connects ``EXPERIMENTAL``: on a new
    profile it is the thing doing the verifying.
    """

    VERIFIED = "verified"
    EXPERIMENTAL = "experimental"


class Evidence(Enum):
    """How well a profile is known.

    ``MAINTAINER``: measured on the maintainer's own unit. ``CONTRIBUTED``:
    measured by a contributor and not reproduced by the maintainer. ``STUB``:
    nothing measured; the class exists to show where the measurements go.
    """

    MAINTAINER = "maintainer"
    CONTRIBUTED = "contributed"
    STUB = "stub"


class _Everything:
    """The ``VERIFIED`` value meaning "every operation" - ``QuadCortex`` uses it."""

    def __contains__(self, name: object) -> bool:
        return True

    def __repr__(self) -> str:
        return "EVERYTHING"


EVERYTHING = _Everything()


@dataclass(frozen=True)
class Hardware:
    """Facts about the unit's hardware that the model layer needs.

    Extended only when a measurement needs a new field; a fact nobody reads
    is a guess with a name.
    """

    footswitches: int
    expression_ports: int


class NoSnapshot:
    """What a profile binds instead of a constants snapshot it does not have yet.

    Binding a stub to the 4.0.1 snapshot would hand a 4.1 user names their
    unit does not use, so the stub binds this and the first attribute access
    says how to generate the real thing.
    """

    def __init__(self, name: str):
        self._name = name

    def __getattr__(self, attr: str) -> object:
        raise AttributeError(
            f"no catalog snapshot {self._name!r} yet; run "
            f"`scripts/generate_models.py --snapshot {self._name}` (and the "
            f"params and options generators) against a unit on that firmware "
            f"and bind the result on the profile class")

    def __repr__(self) -> str:
        return f"NoSnapshot({self._name!r})"


def unverified_text(cls: type, name: str) -> tuple[str, str]:
    """The evidence and the workaround for an operation ``cls`` has not verified.

    One function, used by the refusal, the experimental-mode warning and the
    connect-time hint, so they cannot say three different things.
    """
    firmware = ", ".join(cls.MEASURED_ON) or "no firmware measured"
    evidence = f"not yet verified on {cls.__name__} (CorOS {firmware})"
    workaround = (
        f"connect(support=Support.EXPERIMENTAL) to try it, or run "
        f"`pytest tests/hardware --hardware --verifies {name}` on your unit "
        f"and add the result to {cls.__name__}.VERIFIED")
    return evidence, workaround
```

- [ ] **Step 4: Run the tests to verify they pass, and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_support.py -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: `6 passed`; `Success: no issues found`

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/support.py tests/test_support.py
git commit -m "feat: the vocabulary a device profile is described in (ADR-0020)"
```

---

### Task 2: Move the generated constants into a snapshot package, with shims

**Files:**
- Create: `pyquadcortex/protocol/catalogs/__init__.py`, `pyquadcortex/protocol/catalogs/coros_4_0_1/__init__.py`
- Move: `pyquadcortex/protocol/models.py` -> `pyquadcortex/protocol/catalogs/coros_4_0_1/models.py` (same for `params.py`, `options.py`)
- Modify: `pyquadcortex/protocol/models.py`, `params.py`, `options.py` (new shim content); `tests/test_values.py:197`; `scripts/check_artifacts.py:22-33`
- Test: `tests/test_profiles.py` (first tests; the file grows in Tasks 3-5)

**Interfaces:**
- Produces: importable `pyquadcortex.protocol.catalogs.coros_4_0_1.models` / `.params` / `.options` with the same content as before; `pyquadcortex.protocol.models` etc. re-export them.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_profiles.py
"""The device profile seam (ADR-0020, spec 2026-09-03)."""
import importlib

from pyquadcortex.protocol import models, options, params
from pyquadcortex.protocol.catalogs import coros_4_0_1


def test_the_baseline_shims_re_export_the_4_0_1_snapshot():
    """`protocol.models` keeps meaning CorOS 4.0.1 until an ADR moves it."""
    assert models.ALL is coros_4_0_1.models.ALL
    assert params.BY_MODEL is coros_4_0_1.params.BY_MODEL
    assert options.OPTION_LABELS is coros_4_0_1.options.OPTION_LABELS
    assert set(models.__all__) == set(coros_4_0_1.models.__all__)


def test_the_old_import_paths_still_resolve_as_modules():
    for name in ("models", "params", "options"):
        importlib.import_module(f"pyquadcortex.protocol.{name}")
        importlib.import_module(f"pyquadcortex.protocol.catalogs.coros_4_0_1.{name}")
```

Check before writing the shims which public names each generated module defines beyond `import *`: run `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -c "import pyquadcortex.protocol.models as m; print(m.__all__[:3], hasattr(m,'ALL'))"` and the same for `params` (`BY_MODEL`) and `options` (`OPTION_LABELS`). If a module has no `__all__`, the shim's second import line names the module-level containers explicitly instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_profiles.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyquadcortex.protocol.catalogs'`

- [ ] **Step 3: Move the files and write the package and shims**

```bash
mkdir -p pyquadcortex/protocol/catalogs/coros_4_0_1
git mv pyquadcortex/protocol/models.py pyquadcortex/protocol/catalogs/coros_4_0_1/models.py
git mv pyquadcortex/protocol/params.py pyquadcortex/protocol/catalogs/coros_4_0_1/params.py
git mv pyquadcortex/protocol/options.py pyquadcortex/protocol/catalogs/coros_4_0_1/options.py
```

```python
# pyquadcortex/protocol/catalogs/__init__.py
"""Generated constants, one package per firmware the catalog was read from.

A snapshot is named by the CorOS version it came from (``coros_4_0_1``) and
belongs to the profile class that binds it (ADR-0020). The unversioned
``pyquadcortex.protocol.models`` / ``params`` / ``options`` are shims over the
maintainer's baseline, ``coros_4_0_1``, and stay that way until a decision
record moves them.
"""
```

```python
# pyquadcortex/protocol/catalogs/coros_4_0_1/__init__.py
"""The Quad Cortex catalog on CorOS 4.0.1, read from the maintainer's unit."""
from pyquadcortex.protocol.catalogs.coros_4_0_1 import models, options, params  # noqa: F401

__all__ = ["models", "params", "options"]
```

```python
# pyquadcortex/protocol/models.py  (the shim; params.py and options.py are the same three lines with their names)
"""The maintainer's baseline: the CorOS 4.0.1 snapshot (ADR-0020). Import a
profile's own snapshot from ``pyquadcortex.protocol.catalogs`` when the
connection decides."""
from pyquadcortex.protocol.catalogs.coros_4_0_1.models import *  # noqa: F401,F403
from pyquadcortex.protocol.catalogs.coros_4_0_1.models import ALL, __all__  # noqa: F401
```

For `params.py` the second line imports `BY_MODEL, __all__`; for `options.py` it imports `OPTION_LABELS, __all__`. If Step 1's check showed any other module-level container a test or caller uses (grep `tests/` for `params.` and `options.` attribute access to be sure), import it there too.

The moved files' import lines: each generated module imports from `pyquadcortex.protocol.values` (params) or nothing package-relative; grep each moved file for `from pyquadcortex.protocol import` and confirm the absolute imports still resolve from the new location. They are absolute, so they do.

- [ ] **Step 4: Update the two places that read the generated files by path**

`tests/test_values.py:197`:
```python
PARAMS_PY = (pathlib.Path(__file__).parent.parent / "pyquadcortex" / "protocol"
             / "catalogs" / "coros_4_0_1" / "params.py")
```

`scripts/check_artifacts.py`, add to `REQUIRED` after the `.pyi` entries, with a comment:
```python
    # The constants snapshots are packages the profile classes bind (ADR-0020).
    # A wheel that dropped one would import and then fail on first attribute.
    "pyquadcortex/protocol/catalogs/__init__.py",
    "pyquadcortex/protocol/catalogs/coros_4_0_1/__init__.py",
    "pyquadcortex/protocol/catalogs/coros_4_0_1/models.py",
    "pyquadcortex/protocol/catalogs/coros_4_0_1/params.py",
    "pyquadcortex/protocol/catalogs/coros_4_0_1/options.py",
```

`tests/hardware/test_generated_constants.py:45` changes in Task 7; leave it now.

- [ ] **Step 5: Run the whole offline suite and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: everything passes (2540 before this plan plus the 2 new); mypy clean. If `tests/test_translation.py` or `tests/test_import_cleanliness.py` fails, read its message: both walk the package and name the file they object to; the fix is the path they print, never a widening of their allowlists.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: generated constants live in a per-firmware snapshot package; the old paths are shims"
```

---

### Task 3: `QuadCortex` declares itself as the 4.0.1 profile and guards subclasses

**Files:**
- Modify: `pyquadcortex/protocol/client.py` (imports near line 37; class body at line 257; `__init__` at 260; `CC_VERSION` at 390)
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: Task 1's `Support`, `Evidence`, `EVERYTHING`, `Hardware`, `unverified_text`; Task 2's `coros_4_0_1` package.
- Produces: class attributes `DEVICE_TYPE`, `MEASURED_ON`, `EVIDENCE`, `HARDWARE`, `VERIFIED`, `models`, `params`, `options`; `QuadCortex.ALWAYS` (dict name -> reason); `QuadCortex.operations() -> frozenset[str]`; `QuadCortex._PROFILES: list[type]`; `QuadCortex.__init__(transport, _owned_resources=None, support=Support.VERIFIED)`; `qc.support`; `qc.unverified_operations -> frozenset[str]`; the guard.

- [ ] **Step 1: Write the failing tests (append to `tests/test_profiles.py`)**

```python
import logging

import pytest

from pyquadcortex.protocol import client, errors, support
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from tests.test_client import FakeTransport  # the offline transport double


def test_quadcortex_declares_the_4_0_1_profile():
    qc = client.QuadCortex
    assert qc.DEVICE_TYPE == pa.VersionMessage.QC
    assert qc.MEASURED_ON == ("4.0.1",)
    assert qc.CC_VERSION == "4.0.1"
    assert qc.EVIDENCE is support.Evidence.MAINTAINER
    assert qc.HARDWARE == support.Hardware(footswitches=8, expression_ports=2)
    assert qc.VERIFIED is support.EVERYTHING
    assert qc.models is coros_4_0_1.models
    assert qc.params is coros_4_0_1.params
    assert qc.options is coros_4_0_1.options


def test_always_holds_only_the_lifecycle_and_each_entry_has_a_reason():
    assert set(client.QuadCortex.ALWAYS) == {
        "version", "catalog", "close", "disconnect", "add_listener", "remove_listener"}
    for name, reason in client.QuadCortex.ALWAYS.items():
        assert isinstance(reason, str) and len(reason) > 20, name


def test_operations_are_every_public_method_not_in_always():
    ops = client.QuadCortex.operations()
    public = {n for n, v in vars(client.QuadCortex).items()
              if not n.startswith("_") and (callable(v) or isinstance(v, property))}
    assert ops == frozenset(public - set(client.QuadCortex.ALWAYS))
    assert "set_scene_label" in ops and "version" not in ops
    assert len(ops) > 100


def _profile(verified=frozenset(), name="Probe"):
    """A throwaway subclass; deleted from _PROFILES afterwards by the fixture."""
    return type(name, (client.QuadCortex,), {
        "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
        "VERIFIED": verified})


@pytest.fixture
def forget_probes():
    yield
    client.QuadCortex._PROFILES[:] = [
        c for c in client.QuadCortex._PROFILES if c.__name__ != "Probe"]


def test_a_subclass_registers_itself_and_inherits_what_it_does_not_declare(forget_probes):
    Probe = _profile()
    assert Probe in client.QuadCortex._PROFILES
    assert Probe.CC_VERSION == "4.0.1" and Probe.DEVICE_TYPE == pa.VersionMessage.QC


def test_every_operation_is_guarded_on_a_subclass_that_verifies_nothing(forget_probes):
    Probe = _profile()
    for name in client.QuadCortex.operations():
        assert getattr(getattr(Probe, name), "_unverified", False), name
    for name in client.QuadCortex.ALWAYS:
        assert not getattr(getattr(Probe, name), "_unverified", False), name


def test_a_guarded_operation_refuses_under_verified_with_all_three_fields(forget_probes):
    Probe = _profile()
    fake = FakeTransport()
    qc = Probe(fake)
    with pytest.raises(errors.ControlNotDrivable) as caught:
        qc.set_scene_label(0, "x")
    err = caught.value
    assert err.control == "set_scene_label"
    assert err.evidence == "not yet verified on Probe (CorOS 9.9.9)"
    assert "--verifies set_scene_label" in err.workaround
    assert fake.sent == []  # nothing reached the wire


def test_a_guarded_operation_runs_and_warns_once_under_experimental(forget_probes, caplog):
    Probe = _profile()
    fake = FakeTransport()
    qc = Probe(fake, support=support.Support.EXPERIMENTAL)
    with caplog.at_level(logging.WARNING, logger="pyquadcortex.protocol.client"):
        qc.switch_scene(1)
        qc.switch_scene(2)
    assert len(fake.sent) == 2, "the inherited method ran both times"
    warnings = [r for r in caplog.records if "switch_scene" in r.getMessage()]
    assert len(warnings) == 1, "one warning per operation name per instance"
    assert "not yet verified on Probe" in warnings[0].getMessage()


def test_a_verified_operation_is_not_guarded_and_an_override_is_left_alone(forget_probes):
    class Probe(client.QuadCortex):
        MEASURED_ON = ("9.9.9",)
        EVIDENCE = support.Evidence.STUB
        VERIFIED = frozenset({"switch_scene", "set_scene_label"})

        def set_scene_label(self, scene, label):
            return "mine"

    assert not getattr(Probe.switch_scene, "_unverified", False)
    assert Probe(FakeTransport()).set_scene_label(0, "x") == "mine"


def test_an_override_must_also_be_listed_in_verified(forget_probes):
    """An override IS the subclass's measured behaviour, so the set must say so:
    VERIFIED stays the single statement of what the class knows."""
    with pytest.raises(TypeError, match="overrides set_scene_label but does not list it"):
        type("Probe", (client.QuadCortex,), {
            "MEASURED_ON": ("9.9.9",), "EVIDENCE": support.Evidence.STUB,
            "VERIFIED": frozenset(),
            "set_scene_label": lambda self, scene, label: None})


def test_unverified_operations_is_empty_on_the_base_and_full_on_a_stub(forget_probes):
    assert client.QuadCortex(FakeTransport()).unverified_operations == frozenset()
    Probe = _profile(verified=frozenset({"switch_scene"}))
    got = Probe(FakeTransport()).unverified_operations
    assert got == client.QuadCortex.operations() - {"switch_scene"}


def test_support_defaults_to_verified_and_is_readable():
    assert client.QuadCortex(FakeTransport()).support is support.Support.VERIFIED
    qc = client.QuadCortex(FakeTransport(), support=support.Support.EXPERIMENTAL)
    assert qc.support is support.Support.EXPERIMENTAL
```

`from tests.test_client import FakeTransport` works because pytest puts `tests/` on `sys.path` (CLAUDE.md notes three modules already rely on that). If the import fails, copy the seven-method `FakeTransport` class from `tests/test_client.py` into `tests/test_profiles.py` instead.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_profiles.py -q`
Expected: the two Task 2 tests pass; the rest FAIL with `AttributeError: type object 'QuadCortex' has no attribute 'DEVICE_TYPE'` and similar.

- [ ] **Step 3: Add the declarations, the guard, and the constructor change to `client.py`**

Imports (add near line 37-40; `logging` and `functools` may already be imported - check the top of the file and add only what is missing):

```python
import functools
import logging

from pyquadcortex.protocol.catalogs import coros_4_0_1
from pyquadcortex.protocol.support import (EVERYTHING, Evidence, Hardware,
                                           Support, unverified_text)

log = logging.getLogger(__name__)
```

Module-level guard factory (place just above `class QuadCortex:`):

```python
def _guarded(name, inherited):
    """Wrap an inherited operation a profile has not verified (ADR-0020).

    Under ``Support.VERIFIED`` it refuses before any bytes are sent; under
    ``Support.EXPERIMENTAL`` it warns once per operation per connection and
    runs the inherited behaviour. The decision is the caller's, made at
    connect, and the wording comes from one place.
    """
    @functools.wraps(inherited)
    def guard(self, *args, **kwargs):
        evidence, workaround = unverified_text(type(self), name)
        if self._support is Support.VERIFIED:
            raise errors.ControlNotDrivable(name, evidence, workaround)
        if name not in self._warned:
            self._warned.add(name)
            log.warning("%s is %s; running it anyway", name, evidence)
        return inherited(self, *args, **kwargs)
    guard._unverified = True  # type: ignore[attr-defined]
    return guard
```

(`errors` is already imported in `client.py` as the home of `ControlNotDrivable`; confirm with `grep -n "^from pyquadcortex.protocol import\|errors" pyquadcortex/protocol/client.py | head`.)

Class body, directly under the class docstring at line 258:

```python
    # -- the profile this class IS (ADR-0020) -----------------------------------
    #: The `Version.device_type` this class serves.
    DEVICE_TYPE = pa.VersionMessage.QC
    #: Exact `zenos_git_hash` strings a hardware-suite run has been done against.
    #: A patch release not listed here refuses to connect until someone adds it
    #: after a run - "probably only bug fixes" is the guess the rule stops.
    MEASURED_ON = ("4.0.1",)
    #: How well this profile is known. This one is the maintainer's own unit.
    EVIDENCE = Evidence.MAINTAINER
    #: Facts the model layer needs. Eight footswitches, two expression ports.
    HARDWARE = Hardware(footswitches=8, expression_ports=2)
    #: Operation names verified on this profile. Every method this class has
    #: carries 4.0.1 evidence, so the base verifies everything; a subclass
    #: starts from an empty set and grows it from the hardware suite's report.
    VERIFIED = EVERYTHING
    #: The constants snapshot read from this firmware. A subclass rebinds these.
    #: Dynamic on purpose: `qc.models` follows the connection, at the price of
    #: mypy seeing `Any` through it - import a snapshot module directly for
    #: static unit checking (ADR-0018).
    models = coros_4_0_1.models
    params = coros_4_0_1.params
    options = coros_4_0_1.options
    #: Public methods that must work on ANY profile, because connecting and
    #: cleaning up depend on them. Everything public and not here is an
    #: OPERATION and is guarded on a subclass that has not verified it. Each
    #: entry says why, because this list is the one place a guess could hide.
    ALWAYS = {
        "version": "resolving the profile reads it before any class is chosen",
        "catalog": "the live catalog is how set_block checks an id on any firmware",
        "close": "releasing the device must never depend on what was measured",
        "disconnect": "saying goodbye must never depend on what was measured",
        "add_listener": "subscribing to pushes is transport plumbing, not a unit operation",
        "remove_listener": "unsubscribing is transport plumbing, not a unit operation",
    }
    #: Every subclass, in definition order; `profiles.registry()` reads it.
    _PROFILES: list = []

    @classmethod
    def operations(cls) -> frozenset:
        """Every public method of `QuadCortex` that is an operation on the unit."""
        return frozenset(
            name for name, value in vars(QuadCortex).items()
            if not name.startswith("_")
            and (callable(value) or isinstance(value, property))
            and name not in QuadCortex.ALWAYS)

    def __init_subclass__(cls, **kwargs):
        super().__init_subclass__(**kwargs)
        QuadCortex._PROFILES.append(cls)
        if cls.VERIFIED is EVERYTHING:
            return
        for name in QuadCortex.operations():
            if name in cls.__dict__:
                if name not in cls.VERIFIED:
                    raise TypeError(
                        f"{cls.__name__} overrides {name} but does not list it in "
                        f"VERIFIED; an override is the profile's own measured "
                        f"behaviour, so say so in the one place that lists them")
                continue
            if name in cls.VERIFIED:
                continue
            setattr(cls, name, _guarded(name, getattr(QuadCortex, name)))
```

Constructor (replace the signature at line 260 and add two lines to the body):

```python
    def __init__(self, transport, _owned_resources=None,
                 support: Support = Support.VERIFIED):
        self._t = transport
        # Set by pyquadcortex.protocol.connect() so close() can tear down the transport
        # and HID device it opened on the caller's behalf. When a caller wires
        # their own transport, they own its lifecycle and this stays empty.
        self._owned = _owned_resources or []
        # Populated on first use of .catalog (a ~47 KB fetch from the device).
        self._catalog = None
        # How this connection treats an operation its profile has not verified
        # (ADR-0020). Read by the guard `__init_subclass__` installs.
        self._support = support
        self._warned: set = set()

    @property
    def support(self) -> Support:
        """How this connection treats operations its profile has not verified."""
        return self._support

    @property
    def unverified_operations(self) -> frozenset:
        """Operation names this profile has not verified - empty on `QuadCortex`."""
        return frozenset(
            name for name in QuadCortex.operations()
            if getattr(getattr(type(self), name), "_unverified", False))
```

`catalog` is a property and `operations()` must treat it as public; the `isinstance(value, property)` clause does that, and `ALWAYS` excludes it. The two new properties `support` and `unverified_operations` are public and NOT in `ALWAYS`; they must not be guarded either, since they carry no bytes. Add them to `ALWAYS` with reasons ("reports the connection's own setting; no bytes", "reports the guard's own state; no bytes") and extend the `test_always_holds_only_the_lifecycle...` set accordingly. `set_param`'s `@overload` stubs are not in `vars()` (only the final `def` is), so they need no handling.

- [ ] **Step 4: Run the tests, the full suite, and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_profiles.py -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: all pass; mypy clean. If mypy objects to `guard._unverified`, the `# type: ignore[attr-defined]` shown above is the accepted form (it is the only such ignore this plan adds; say so in the commit message). If mypy objects to `_PROFILES: list = []`, annotate it `list[type]`.

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/client.py tests/test_profiles.py
git commit -m "feat: QuadCortex is the 4.0.1 profile, and a subclass refuses what it has not verified"
```

---

### Task 4: `profiles.py` - the two stubs, the registry, `resolve()`

**Files:**
- Create: `pyquadcortex/protocol/profiles.py`
- Test: `tests/test_profiles.py`

**Interfaces:**
- Consumes: Task 3's class attributes and `_PROFILES`; Task 1's `NoSnapshot`, `Evidence`, `Hardware`, `unverified_text`.
- Produces: `QuadCortex41`, `QuadCortexMini`, `UnsupportedDevice(device_type, coros_version, message)` with attributes `device_type`, `coros_version`; `registry() -> dict[tuple[int, str], type]`; `stubs() -> list[type]`; `resolve(version_message) -> type`.

- [ ] **Step 1: Write the failing tests (append to `tests/test_profiles.py`)**

```python
from pyquadcortex.protocol import profiles


def test_the_4_1_stub_connects_but_verifies_nothing_and_has_no_snapshot():
    cls = profiles.QuadCortex41
    assert issubclass(cls, client.QuadCortex)
    assert cls.MEASURED_ON == ("4.1.0",)
    assert cls.EVIDENCE is support.Evidence.CONTRIBUTED
    assert cls.VERIFIED == frozenset()
    assert cls.CC_VERSION == "4.0.1", "inherited: the contributor's runs announced 4.0.1"
    assert isinstance(cls.models, support.NoSnapshot)
    with pytest.raises(AttributeError, match="coros_4_1_0"):
        cls.models.Delay
    assert len(cls(FakeTransport()).unverified_operations) == len(client.QuadCortex.operations())


def test_the_mini_stub_is_recognised_but_cannot_connect():
    cls = profiles.QuadCortexMini
    assert cls.DEVICE_TYPE == pa.VersionMessage.ATMA
    assert cls.MEASURED_ON == ()
    assert cls.EVIDENCE is support.Evidence.STUB
    assert cls.HARDWARE == support.Hardware(footswitches=4, expression_ports=2)
    assert cls in profiles.stubs()
    assert cls not in profiles.registry().values()


def test_the_registry_has_one_entry_per_measured_version():
    reg = profiles.registry()
    assert reg[(pa.VersionMessage.QC, "4.0.1")] is client.QuadCortex
    assert reg[(pa.VersionMessage.QC, "4.1.0")] is profiles.QuadCortex41
    assert all(len(k) == 2 for k in reg)


def _reply(device_type, coros):
    return pa.VersionMessage(action=pa.MessageAction.UPDATE, device_type=device_type,
                             zenos_git_hash=coros, device_serial_number="QA00EE910")


def test_resolve_maps_known_pairs_to_their_class():
    assert profiles.resolve(_reply(pa.VersionMessage.QC, "4.0.1")) is client.QuadCortex
    assert profiles.resolve(_reply(pa.VersionMessage.QC, "4.1.0")) is profiles.QuadCortex41


def test_resolve_refuses_an_unknown_firmware_naming_what_exists_without_taking_it():
    with pytest.raises(profiles.UnsupportedDevice) as caught:
        profiles.resolve(_reply(pa.VersionMessage.QC, "4.2.0"))
    err = caught.value
    assert (err.device_type, err.coros_version) == (pa.VersionMessage.QC, "4.2.0")
    text = str(err)
    assert "QC, CorOS 4.2.0" in text
    assert "4.0.1" in text and "4.1.0" in text
    assert "profile=QuadCortex41" in text and "Support.EXPERIMENTAL" in text


def test_resolve_refuses_a_mini_naming_the_stub_and_how_to_start():
    with pytest.raises(profiles.UnsupportedDevice) as caught:
        profiles.resolve(_reply(pa.VersionMessage.ATMA, "1.0.0"))
    text = str(caught.value)
    assert "Quad Cortex Mini" in text and "QuadCortexMini" in text
    assert "not yet supported" in text
    assert "profile=QuadCortexMini" in text and "pytest tests/hardware --hardware" in text


def test_resolve_refuses_a_reply_with_no_identity():
    with pytest.raises(profiles.UnsupportedDevice, match="did not report"):
        profiles.resolve(pa.VersionMessage(action=pa.MessageAction.UPDATE))
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_profiles.py -q -k "stub or registry or resolve"`
Expected: FAIL with `ModuleNotFoundError: No module named 'pyquadcortex.protocol.profiles'`

- [ ] **Step 3: Write the module**

```python
# pyquadcortex/protocol/profiles.py
"""The device profiles this library knows, and how a connection picks one.

A profile is a client class (ADR-0020). ``QuadCortex`` in ``client.py`` is the
Quad Cortex on CorOS 4.0.1 and the base of every other profile. This module
holds the two profiles we know are coming and have not measured, and the
registry ``connect()`` resolves through. Adding a profile is: subclass
``QuadCortex``, declare the class attributes, and the registry sees it.
"""
from __future__ import annotations

from pyquadcortex.protocol.client import QuadCortex
from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.protocol.support import Evidence, Hardware, NoSnapshot

_DEVICE_NAMES = {pa.VersionMessage.QC: "Quad Cortex",
                 pa.VersionMessage.ATMA: "Quad Cortex Mini"}


class QuadCortex41(QuadCortex):
    """Quad Cortex on CorOS 4.1 - connects, and verifies nothing yet.

    A contributor ran the whole hardware suite against a 4.1.0 unit with this
    handshake and announce string, so the connection is known to work. Which
    operations behave as on 4.0.1 is not known per name, so every inherited
    operation refuses under ``Support.VERIFIED`` and runs with a warning under
    ``Support.EXPERIMENTAL``. The snapshot is deliberately absent: binding the
    4.0.1 constants would hand a 4.1 user names their unit does not use.

    To finish this profile, on a 4.1 unit:

    1. ``scripts/generate_models.py --snapshot coros_4_1_0`` and the params and
       options generators; bind the three modules below.
    2. ``pytest tests/hardware --hardware``; the report at the end lists the
       operations whose tests passed. Put those names in ``VERIFIED``.
    3. Record any operation that behaved differently in ``docs/protocol.md``
       beside the 4.0.1 record, dated and named, and override it here.
    """

    MEASURED_ON = ("4.1.0",)
    EVIDENCE = Evidence.CONTRIBUTED
    VERIFIED = frozenset()
    models = NoSnapshot("coros_4_1_0")
    params = NoSnapshot("coros_4_1_0")
    options = NoSnapshot("coros_4_1_0")


class QuadCortexMini(QuadCortex):
    """Quad Cortex Mini - recognised, not supported, and here to be finished.

    Nothing has been measured: no Mini has connected through this library. The
    schema names it (``DeviceType.ATMA``, ``Mode.atma_page``, ``AtmaPowerOnMode``),
    which is inference from field names, not a measurement. Four footswitches
    is from the product page. Because ``MEASURED_ON`` is empty this class is
    never resolved; a Mini refuses to connect with a message naming this class.

    To start, on a Mini: ``connect(profile=QuadCortexMini,
    support=Support.EXPERIMENTAL)``. If the handshake works, run the hardware
    suite and send the report. Expect the eight-footswitch assumptions in
    ``QuadCortex`` to need overrides here, and expect this class to want a
    different base than ``QuadCortex`` once its shape is known.
    """

    DEVICE_TYPE = pa.VersionMessage.ATMA
    MEASURED_ON = ()
    EVIDENCE = Evidence.STUB
    HARDWARE = Hardware(footswitches=4, expression_ports=2)
    VERIFIED = frozenset()
    models = NoSnapshot("coros_mini")
    params = NoSnapshot("coros_mini")
    options = NoSnapshot("coros_mini")


class UnsupportedDevice(Exception):
    """The unit is not one this library has a measured profile for.

    Raised at connect, before the handshake. The message names what the unit
    said and what is registered. It never picks a profile for the caller: the
    ``profile=`` argument is how a person chooses to measure an unknown unit.
    """

    def __init__(self, device_type, coros_version, message):
        super().__init__(message)
        self.device_type = device_type
        self.coros_version = coros_version


def _all_profiles() -> list[type[QuadCortex]]:
    return [QuadCortex, *QuadCortex._PROFILES]


def registry() -> dict[tuple[int, str], type[QuadCortex]]:
    """``(device_type, coros_version) -> class``, one entry per measured version."""
    reg: dict[tuple[int, str], type[QuadCortex]] = {}
    for cls in _all_profiles():
        for coros in cls.MEASURED_ON:
            reg[(cls.DEVICE_TYPE, coros)] = cls
    return reg


def stubs() -> list[type[QuadCortex]]:
    """Profiles that cannot be resolved because nothing was measured on them."""
    return [cls for cls in _all_profiles() if not cls.MEASURED_ON]


def _describe(cls: type[QuadCortex]) -> str:
    level = {Evidence.MAINTAINER: "verified on the maintainer's unit",
             Evidence.CONTRIBUTED: "contributed, not verified by the maintainer",
             Evidence.STUB: "stub"}[cls.EVIDENCE]
    return f"{_DEVICE_NAMES.get(cls.DEVICE_TYPE, cls.DEVICE_TYPE)} {', '.join(cls.MEASURED_ON)} ({level})"


def resolve(reply: pa.VersionMessage) -> type[QuadCortex]:
    """The class for the unit that sent ``reply``, or ``UnsupportedDevice``."""
    if not (reply.HasField("device_type") and reply.HasField("zenos_git_hash")):
        raise UnsupportedDevice(None, None,
                                "the unit did not report device_type and zenos_git_hash in "
                                "its Version reply, so no profile can be chosen")
    key = (reply.device_type, reply.zenos_git_hash)
    found = registry().get(key)
    if found is not None:
        return found
    device = _DEVICE_NAMES.get(reply.device_type, pa.VersionMessage.DeviceType.Name(reply.device_type))
    known = "; ".join(_describe(c) for c in _all_profiles() if c.MEASURED_ON)
    same_device = [c for c in _all_profiles() if c.DEVICE_TYPE == reply.device_type]
    if same_device and all(c.EVIDENCE is Evidence.STUB for c in same_device):
        stub = same_device[0]
        hint = (f"The {device} is recognised and not yet supported; {stub.__name__} is the "
                f"stub to finish. To start: connect(profile={stub.__name__}, "
                f"support=Support.EXPERIMENTAL), then `pytest tests/hardware --hardware` "
                f"and send the report. See its docstring.")
    elif same_device:
        nearest = max(same_device, key=lambda c: c.MEASURED_ON)
        hint = (f"Pass profile={nearest.__name__} to treat it as "
                f"{', '.join(nearest.MEASURED_ON)} while you measure it, with "
                f"support=Support.EXPERIMENTAL; then add {reply.zenos_git_hash!r} to "
                f"{nearest.__name__}.MEASURED_ON.")
    else:
        hint = "No profile exists for this device type."
    raise UnsupportedDevice(
        reply.device_type, reply.zenos_git_hash,
        f"the unit reports {pa.VersionMessage.DeviceType.Name(reply.device_type)}, "
        f"CorOS {reply.zenos_git_hash}; this library has profiles for: {known}. {hint}")
```

Note on the "nearest" wording: the message NAMES a class the caller may choose and never instantiates it. `max(..., key=MEASURED_ON)` is a string comparison of version tuples, good enough for a hint, and the test only asserts the name appears.

- [ ] **Step 4: Run the tests, the suite, and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_profiles.py -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: all pass, mypy clean. `tests/test_import_cleanliness.py` walks every module; `profiles.py` imports no `hid`, so it passes.

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/profiles.py tests/test_profiles.py
git commit -m "feat: the profile registry, resolve(), and the two stubs that show how the next profiles arrive"
```

---

### Task 5: `connect()` resolves the profile before the handshake

**Files:**
- Modify: `pyquadcortex/protocol/session.py:82-175`; `pyquadcortex/device/device.py:234-278`; `pyquadcortex/protocol/__init__.py:53-62` and its `__all__`
- Test: `tests/test_session.py`; `tests/test_device.py` (one test); `tests/typing/wrong_units.py` + `tests/test_typing.py`

**Interfaces:**
- Consumes: Task 4's `resolve`, `UnsupportedDevice`, the stubs; Task 3's `support=` constructor argument.
- Produces: `protocol.connect(*, timeout, settle, handshake_patience, before_handshake, profile=None, support=Support.VERIFIED) -> QuadCortex`; `pyquadcortex.connect(*, timeout, settle, handshake_patience, profile=None, support=Support.VERIFIED) -> Device`; `pyquadcortex.protocol` exports `Support`, `Evidence`, `UnsupportedDevice`, `QuadCortex41`, `QuadCortexMini`, `Hardware`.

- [ ] **Step 1: Read `tests/test_session.py:14-56`** to see the `FakeDevice` / `FakeTransport` / `fake_stack` doubles. `FakeTransport.request` returns the message it was sent; it has no `await_broadcast`. `version()` now uses `await_broadcast` (PR #51), so the double needs one that answers a `Version` READ with a canned reply. Add to `FakeTransport` in `tests/test_session.py`:

```python
    #: What the unit says it is. Tests set this before connect() runs.
    version_reply = None

    def await_broadcast(self, expected_class, trigger, timeout=40.0, match=None):
        self.happened.append(f"await {expected_class.__name__}")
        trigger()
        reply = type(self).version_reply
        if reply is None:
            reply = pa.VersionMessage(action=pa.MessageAction.UPDATE,
                                      device_type=pa.VersionMessage.QC,
                                      zenos_git_hash="4.0.1",
                                      device_serial_number="QCS0000001")
        assert match is None or match(reply), "the canned reply must satisfy version()'s predicate"
        return reply
```

and `from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa` at the top if absent. Reset `FakeTransport.version_reply = None` in the `fake_stack` fixture beside `FakeTransport.instances = []`.

- [ ] **Step 2: Write the failing tests (append to `tests/test_session.py`)**

```python
from pyquadcortex.protocol import profiles, support


def test_connect_resolves_the_profile_from_the_units_version_before_the_handshake(fake_stack):
    qc = session.connect()
    t = FakeTransport.instances[0]
    assert type(qc) is client.QuadCortex
    assert qc.support is support.Support.VERIFIED
    order = [h for h in t.happened if h.startswith(("await Version", "request ResetComms", "send Version"))]
    assert order[0] == "await VersionMessage", "identity is read before anything else is sent"
    assert order.index("await VersionMessage") < order.index("request ResetCommsBuffersMessage")


def test_connect_hands_back_the_4_1_class_for_a_4_1_unit(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.1.0", device_serial_number="QCS0000001")
    qc = session.connect()
    assert type(qc) is profiles.QuadCortex41
    assert qc.unverified_operations == client.QuadCortex.operations()
    with pytest.raises(errors.ControlNotDrivable):
        qc.switch_scene(1)


def test_connect_refuses_an_unknown_unit_and_releases_the_device(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.2.0", device_serial_number="QCS0000001")
    with pytest.raises(profiles.UnsupportedDevice, match="4.2.0"):
        session.connect()
    t = FakeTransport.instances[0]
    assert t.stopped and t.device.closed
    assert not any(h.startswith("request ResetComms") for h in t.happened), "no handshake ran"


def test_connect_with_profile_skips_the_registry_but_checks_the_device_type(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.2.0", device_serial_number="QCS0000001")
    qc = session.connect(profile=profiles.QuadCortex41, support=support.Support.EXPERIMENTAL)
    assert type(qc) is profiles.QuadCortex41
    assert qc.support is support.Support.EXPERIMENTAL
    with pytest.raises(profiles.UnsupportedDevice, match="asked for QuadCortexMini"):
        session.connect(profile=profiles.QuadCortexMini)


def test_the_announce_string_is_the_resolved_profiles(fake_stack):
    FakeTransport.version_reply = pa.VersionMessage(
        action=pa.MessageAction.UPDATE, device_type=pa.VersionMessage.QC,
        zenos_git_hash="4.1.0", device_serial_number="QCS0000001")
    session.connect()
    t = FakeTransport.instances[0]
    assert "send VersionMessage" in t.happened, "the announce went out on the resolved class"
```

The last test can only check that an announce was sent, because `FakeTransport.send` records the type name only; `QuadCortex41.CC_VERSION` is inherited and equals `"4.0.1"`, which `tests/test_profiles.py` already pins.

Also add, in `tests/test_device.py` beside `test_from_client_exposes_the_client_it_was_given`:

```python
def test_connect_passes_profile_and_support_through(monkeypatch):
    seen = {}

    def fake_connect(**kw):
        seen.update(kw)
        return FakeClient()

    monkeypatch.setattr(device_module.protocol, "connect", fake_connect)
    device_module.connect(profile=profiles.QuadCortex41, support=support.Support.EXPERIMENTAL)
    assert seen["profile"] is profiles.QuadCortex41
    assert seen["support"] is support.Support.EXPERIMENTAL
    assert "before_handshake" in seen
```

with `from pyquadcortex import device as device_module` and `from pyquadcortex.protocol import profiles, support` at the top (check how `tests/test_device.py` imports `device.py` already and follow it).

- [ ] **Step 3: Run the tests to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_session.py tests/test_device.py -q`
Expected: the new tests FAIL (`TypeError: connect() got an unexpected keyword argument 'profile'`, and the resolution assertions).

- [ ] **Step 4: Change `session.connect`**

Signature and docstring additions:

```python
def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0,
            before_handshake=None,
            profile=None,
            support: Support = Support.VERIFIED) -> QuadCortex:
```

Add to the docstring's `Args:`:

```
        profile: a profile class to use instead of the one the unit's identity
            resolves to (ADR-0020). For measuring a unit this library has no
            profile for: the class's ``DEVICE_TYPE`` must still match what the
            unit reports, and ``MEASURED_ON`` is not checked. Combine with
            ``support=Support.EXPERIMENTAL`` to run operations the profile has
            not verified.
        support: how the connection treats an operation its profile has not
            verified. ``Support.VERIFIED`` (default) refuses it;
            ``Support.EXPERIMENTAL`` runs it with a warning. On ``QuadCortex``
            every operation is verified, so this changes nothing there.
```

and to `Raises:`:

```
        UnsupportedDevice: if the unit reports a device type and CorOS version
            no profile has measured, or ``profile`` names a class for a
            different device type. Raised before the handshake; the device is
            released.
```

Body: replace the line `qc = QuadCortex(transport, _owned_resources=owned)` with

```python
        # Who are we talking to? Read before the handshake, through the base
        # class, whose version() is in ALWAYS and works on any unit (ADR-0020).
        identity = QuadCortex(transport).version(timeout=timeout)
        if profile is None:
            cls = profiles.resolve(identity)
        else:
            cls = profile
            if identity.HasField("device_type") and identity.device_type != cls.DEVICE_TYPE:
                raise profiles.UnsupportedDevice(
                    identity.device_type, identity.zenos_git_hash,
                    f"you asked for {cls.__name__}, which serves "
                    f"{pa.VersionMessage.DeviceType.Name(cls.DEVICE_TYPE)}, and the unit "
                    f"says {pa.VersionMessage.DeviceType.Name(identity.device_type)}")
        qc = cls(transport, _owned_resources=owned, support=support)
```

Imports in `session.py`: `from pyquadcortex.protocol import profiles`, `from pyquadcortex.protocol.support import Support`, and `pa` if not already imported. `profiles` imports `client`, and `session` already imports `client`, so no cycle. The `try/except BaseException` that follows already releases everything opened, so an `UnsupportedDevice` from inside it closes the device: the test asserts that.

- [ ] **Step 5: Change `device.connect` and the exports**

`pyquadcortex/device/device.py:234`:

```python
def connect(*, timeout: float = 5.0, settle: float = 2.0,
            handshake_patience: float = 30.0,
            profile=None, support=protocol.Support.VERIFIED) -> Device:
```

pass both through in the `protocol.connect(...)` call, and add the two `Args:` paragraphs (copy from `session.connect`, shortened to one sentence each pointing at `pyquadcortex.protocol.connect`).

`pyquadcortex/protocol/__init__.py`: after the `session` import line add

```python
from pyquadcortex.protocol.support import Support, Evidence, Hardware  # noqa: F401
from pyquadcortex.protocol.profiles import (UnsupportedDevice, QuadCortex41,  # noqa: F401
                                            QuadCortexMini)
```

and add `"Support", "Evidence", "Hardware", "UnsupportedDevice", "QuadCortex41", "QuadCortexMini"` to `__all__`. `tests/test_namespace.py` pins the export list against a fixture; read its failure message if it fails and add the six names where it says, with the reason "ADR-0020 public surface".

- [ ] **Step 6: Typing test**

`tests/typing/wrong_units.py` is one file of accepted and rejected calls; `tests/test_typing.py` runs mypy over it and holds the reported line numbers against every line carrying the marker `# want: error`. Accepted lines carry no marker and must NOT be reported. Append, under a new comment header in the file's style:

```python
# -- ADR-0020: the support mode is an enum, and connect() returns the base type
from pyquadcortex import protocol

verified: protocol.QuadCortex = protocol.connect(support=protocol.Support.EXPERIMENTAL)
protocol.connect(support="experimental")  # want: error
protocol.connect(profile=protocol.QuadCortex41)
```

The `from pyquadcortex import protocol` line goes with the file's other imports at the top rather than mid-file if the file's existing tests object to import placement (run `tests/test_typing.py` and read its message).

- [ ] **Step 7: Run the suite and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: all pass, mypy clean.

- [ ] **Step 8: Commit**

```bash
git add -A
git commit -m "feat: connect() resolves the device profile before the handshake and refuses the unknown"
```

---

### Task 6: `set_block` checks the live catalog before sending

**Files:**
- Modify: `pyquadcortex/protocol/client.py` (`set_block`, the block starting `if cell.model_id in units_module.UNPLACEABLE_MODELS:` around line 1144)
- Test: `tests/test_client.py`

**Interfaces:**
- Consumes: `self.catalog` (a `ModelCatalog` with `get(model_id)` and `__len__`).
- Produces: `set_block` raises `ControlNotDrivable` for a model id the connected unit's catalog lacks, before sending.

- [ ] **Step 1: How `tests/test_client.py` fakes the catalog.** Eight tests do `qc._catalog = catalog.parse_model_repo(_sample_repo_payload())` (for example at line 617); `_sample_repo_payload()` is a helper in that file and `catalog` is already imported there. Use exactly that. Note which model ids the sample payload contains: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -c "import sys; sys.path.insert(0,'tests'); import test_client as t; from pyquadcortex.protocol import catalog; c=catalog.parse_model_repo(t._sample_repo_payload()); print(sorted(m.id for m in c.factory_models())[:40], len(c))"`.

- [ ] **Step 2: Write the failing test (append to `tests/test_client.py`)**

```python
def test_set_block_refuses_a_model_the_unit_does_not_have_before_sending():
    """A 4.1 constant on a 4.0.1 unit (ADR-0020): the unit would accept the
    hash silently and the block would simply not be there, reported as a DSP
    or port problem. Checking the live catalog first names the real cause."""
    fake = FakeTransport()
    qc = client.QuadCortex(fake)
    qc._catalog = catalog.parse_model_repo(_sample_repo_payload())
    missing = 6026  # Crystal Delay, CorOS 4.1.0 only; not in the sample payload either
    assert qc.catalog.get(missing) is None
    with pytest.raises(errors.ControlNotDrivable) as caught:
        qc.set_block(Block(0, 3, missing), verify=False)
    assert caught.value.control == "model 6026"
    assert "not in this unit's catalog" in caught.value.evidence
    assert fake.sent == []
```

`Block`, `catalog`, `client` and `FakeTransport` are already imported in that file; add `from pyquadcortex.protocol import errors` if it is not.

- [ ] **Step 3: Run to verify it fails**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_client.py -q -k refuses_a_model_the_unit_does_not_have`
Expected: FAIL (no exception raised; `fake.sent` has one message).

- [ ] **Step 4: Add the check**, directly after the `UNPLACEABLE_MODELS` block and before `row, column, model = ...`:

```python
        model_id = int(getattr(cell.model_id, "id", cell.model_id))
        if self.catalog.get(model_id) is None:
            raise errors.ControlNotDrivable(
                f"model {model_id}",
                f"not in this unit's catalog (CorOS {', '.join(type(self).MEASURED_ON)}); "
                f"the catalog is read from the unit, so this id does not exist on it",
                "use a constant from this connection's own snapshot (qc.models), or "
                "look the model up in qc.catalog by name")
```

and remove the later duplicate `model_id = int(getattr(model, "id", model))` line, keeping one. Add to the docstring: "Refused, before sending, for a model id the unit's own catalog does not list - a constant from another firmware's snapshot, for instance (ADR-0020)."

- [ ] **Step 5: Run the suite and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: the existing `set_block` tests that build a `QuadCortex(FakeTransport())` with NO catalog now fail, because the check fetches one and `FakeTransport.await_broadcast` returns `None`. That is expected and the fix is per test: give each one `qc._catalog = catalog.parse_model_repo(_sample_repo_payload())` and, where the id it places (5005 in `test_set_block_sends_row_column_keyed_grid_update`) is not in the sample payload, place an id that is (from Step 1's list) and update the assertions on `hash` to match. Never weaken the check to make a test pass.

- [ ] **Step 6: Commit**

```bash
git add pyquadcortex/protocol/client.py tests/test_client.py
git commit -m "feat: set_block refuses a model id the unit's own catalog does not list"
```

---

### Task 7: Generators take `--snapshot` and render the docstring example from the catalog

**Files:**
- Modify: `scripts/generate_models.py:56-70,124-135`; `scripts/generate_options.py:210-220,276-284`; `scripts/generate_params.py:163-200,290-298`
- Modify: `pyquadcortex/protocol/catalogs/coros_4_0_1/{models,params,options}.py` (header line; params docstring example)
- Test: `tests/test_generators.py`

**Interfaces:**
- Produces: each generator's `main()` accepts `--snapshot NAME` (required) and writes `pyquadcortex/protocol/catalogs/NAME/<module>.py`; `render(cat, snapshot)`; `generate_params.example_cab(cat) -> str`.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_generators.py
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


XML = b"""<?xml version="1.0"?>
<ModelRepo>
  <Category id="21" name="Cabsim Bass (M)">
    <Model id="21005" name="N212 Darkglass Neo (M)"><Parameter name="MIC 1" type="comboBox"/></Model>
    <Model id="21001" name="N210C Darkglass (M)"><Parameter name="MIC 1" type="comboBox"/></Model>
  </Category>
  <Category id="6" name="Delay">
    <Model id="6001" name="Analog Delay (M)"><Parameter name="MIX" type="float" units="%" min="0" max="100"/></Model>
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
    assert mod.example_cab(cat) == "models.CabsimBassM.N210C_DARKGLASS_M"
    assert "Block(0, 5, models.CabsimBassM.N210C_DARKGLASS_M)" in mod.render(cat, snapshot="coros_9_9_9")


@pytest.mark.parametrize("name", ["generate_models", "generate_params", "generate_options"])
def test_snapshot_is_required_and_decides_the_output_path(name, tmp_path, monkeypatch, cat):
    mod = _load(name)
    monkeypatch.setattr(mod, "load_payload", lambda path: XML)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", [name, "--payload", "x"])
    with pytest.raises(SystemExit):        # argparse: --snapshot is required
        mod.main()
    monkeypatch.setattr(sys, "argv", [name, "--payload", "x", "--snapshot", "coros_9_9_9"])
    mod.main()
    written = tmp_path / "pyquadcortex" / "protocol" / "catalogs" / "coros_9_9_9"
    assert (written / f"{name.split('_')[1]}.py").exists()
```

Adjust the XML if `catalog.parse_model_repo` requires attributes these elements lack: read `pyquadcortex/protocol/catalog.py:637-670` (`_parameter`, `factory_models`, the `hidden`/`factory` attributes) and add the minimum. The models must count as FACTORY for `generate_models.render` to emit them; check what `factory_models()` filters on and set it in the XML.

- [ ] **Step 2: Run to verify they fail**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_generators.py -q`
Expected: FAIL (`render() got an unexpected keyword argument 'snapshot'`, `no attribute 'example_cab'`).

- [ ] **Step 3: Change the three generators**

In each `render`, take `snapshot: str` and make the header's second line `f"GENERATED by scripts/<name>.py --snapshot {snapshot} - do not edit by hand."` (keep each file's existing header wording otherwise; `generate_options.py` uses double backticks around the script name - keep them). In each `main`, replace the `--out` argument with:

```python
    parser.add_argument("--snapshot", required=True,
                        help="snapshot package to write, named by CorOS version, e.g. coros_4_1_0")
```

and write to `pathlib.Path("pyquadcortex/protocol/catalogs") / args.snapshot / "<module>.py"`, creating the directory and an `__init__.py` (the `coros_4_0_1/__init__.py` text from Task 2 with the version substituted) if absent. Keep `encoding="utf-8"` on the write if present; add it if not.

In `generate_params.py`, add:

```python
def example_cab(cat: catalog.ModelCatalog) -> str:
    """The cab the module docstring's example places: the lowest-id factory
    model whose category name starts with "Cabsim", so the example names a
    constant that exists in the snapshot being written, by construction."""
    cabs = [m for m in cat.factory_models() if m.category.startswith("Cabsim")]
    if not cabs:
        raise SystemExit("no Cabsim category in this catalog; the docstring example needs one")
    cab = min(cabs, key=lambda m: m.id)
    return f"models.{class_name(cab.category)}.{constant_name(cab)}"
```

using whatever `generate_models.py` calls its class-name and constant-name functions (`class_name` exists at line 37; find the constant-name one and import both, or duplicate the two small functions if the scripts do not import each other today). Replace the hard-coded `"    cab = Block(0, 5, models.CabsimBassM.N212_DARKGLASS_NEO_M)",` at line 187 with `f"    cab = Block(0, 5, {example_cab(cat)})",`.

- [ ] **Step 4: Bring the committed 4.0.1 snapshot in line by hand**

The hardware comparison test compares the committed file byte-for-byte with a fresh render, so the committed files must equal what the changed generators would write. Without a saved 4.0.1 payload the change is two edits: in each of the three moved files, change the header's `GENERATED by ...` line to include ` --snapshot coros_4_0_1`; in `coros_4_0_1/params.py` line 23 change the example to `models.CabsimBassM.N210C_DARKGLASS_M` (id 21001, the lowest-id cab in the 4.0.1 catalog: `CabsimBassM` lists it first). `tests/test_docs.py` scans docstrings; it does not resolve `models.*` names, but check the example still passes its keyword and bare-number checks (it does: no keywords, `Real(3.0)` is typed).

- [ ] **Step 5: Run the suite and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "feat: generators write a named snapshot and render the docstring example from the catalog"
```

---

### Task 8: The hardware suite measures per profile

**Files:**
- Modify: `tests/conftest.py`; `tests/hardware/conftest.py`; `tests/hardware/test_generated_constants.py:42-47`; every `tests/hardware/test_*.py`
- Test: `tests/test_hardware_markers.py`; `tests/test_hardware_gate.py` (one assertion)

**Interfaces:**
- Consumes: Task 3's `operations()`; Task 5's `connect(support=)`.
- Produces: marker `verifies(*names)`; option `--verifies NAME`; fixtures `profile`, `scratch_preset`; a session report; `UNMARKED_OPERATIONS` (dict name -> reason) in `tests/test_hardware_markers.py`.

- [ ] **Step 1: Write the failing offline test**

```python
# tests/test_hardware_markers.py
"""Every operation is named by a hardware test, or says why not (spec section 4).

Source-reading, like tests/test_translation.py: the hardware modules are never
imported here, so this runs with no unit attached.
"""
import ast
import pathlib

from pyquadcortex.protocol import client

HARDWARE = pathlib.Path(__file__).parent / "hardware"

#: Operations no hardware test names, each with the reason. A new operation
#: has to come through this dict or through a marker; there is no third way.
UNMARKED_OPERATIONS = {
    # filled in Step 4 from the first run's failure message, with a reason per name
}


def marked_operations():
    names = set()
    for path in sorted(HARDWARE.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "verifies"):
                    for arg in deco.args:
                        assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                            f"{path.name}:{node.lineno} verifies() takes string literals")
                        names.add(arg.value)
    return names


def test_every_marker_names_a_real_operation():
    unknown = marked_operations() - client.QuadCortex.operations()
    assert not unknown, f"markers name operations QuadCortex does not have: {sorted(unknown)}"


def test_every_operation_is_marked_or_excused_with_a_reason():
    missing = client.QuadCortex.operations() - marked_operations() - set(UNMARKED_OPERATIONS)
    assert not missing, (
        f"operations no hardware test verifies and no reason excuses: {sorted(missing)}. "
        f"Mark the test that drives each one with @pytest.mark.verifies(...), or add it "
        f"to UNMARKED_OPERATIONS with the reason.")
    stale = set(UNMARKED_OPERATIONS) & marked_operations()
    assert not stale, f"excused AND marked; drop the excuse: {sorted(stale)}"
    for name, reason in UNMARKED_OPERATIONS.items():
        assert len(reason) > 20, name
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_hardware_markers.py -q`
Expected: `test_every_operation_is_marked_or_excused_with_a_reason` FAILS listing every operation (none is marked yet).

- [ ] **Step 3: Register the marker and the option in `tests/conftest.py`**

```python
def pytest_addoption(parser):
    parser.addoption("--hardware", ...)   # existing
    parser.addoption(
        "--verifies", action="store", default=None, metavar="OPERATION",
        help="with --hardware: run only the tests marked verifies(OPERATION) "
             "(ADR-0020). pytest's -m cannot match a marker's arguments.")


def pytest_configure(config):
    config.addinivalue_line(
        "markers",
        "verifies(*operations): the QuadCortex operations a hardware test exercises "
        "(ADR-0020). Names are checked against QuadCortex.operations() at collection.")
```

- [ ] **Step 4: Change `tests/hardware/conftest.py`**

Session fixture: `protocol.connect(before_handshake=subscribe, support=protocol.Support.EXPERIMENTAL)`, with a comment: "EXPERIMENTAL always: on a new profile this suite IS the verification, and a VERIFIED client would refuse everything before a test could look. On QuadCortex it changes nothing."

Add fixtures:

```python
@pytest.fixture(scope="session")
def profile(qc):
    """The connected profile class (ADR-0020)."""
    return type(qc)


@pytest.fixture
def scratch_preset(qc):
    """A disposable copy of the loaded preset in a free User slot.

    Yields ``(folder_key, position, name)``. Teardown recalls the original slot
    and deletes the copy, so a test that must edit, save or undo never touches
    one of the owner's presets. Requires the loaded preset to be clean.
    """
    from pyquadcortex.protocol import Setlist
    before = qc.loaded_position()
    assert qc.preset_dirty() is False, "the loaded preset has unsaved edits; save or reload it first"
    listing = qc.list_presets(Setlist.USER, include_empty=True)
    free = next(e.index for e in listing if not e.name)
    name = "pyquadcortex scratch"
    stored = qc.save_current_preset(Setlist.USER, free, name, confirm=True, confirm_timeout=30.0)
    assert stored == name
    qc.recall_preset(Setlist.USER, free)
    time.sleep(3.0)
    try:
        yield Setlist.USER, free, name
    finally:
        qc.recall_preset(before.folder_key, before.position)
        time.sleep(3.0)
        qc.delete_preset(Setlist.USER, name)
```

`--verifies` selection and marker validation go at the TOP of `pytest_collection_modifyitems`, inside `if config.getoption("--hardware"):`, before the existing early `return` on that flag (the rest of the hook is the offline refusal and stays as it is):

```python
    wanted = config.getoption("--verifies")
    if wanted:
        keep, drop = [], []
        for item in items:
            names = {n for m in item.iter_markers("verifies") for n in m.args}
            (keep if wanted in names else drop).append(item)
        items[:] = keep
        config.hook.pytest_deselected(items=drop)
```

Marker validation at collection (same hook, always): for each item, `names = {n for m in item.iter_markers("verifies") for n in m.args}`; any name not in `QuadCortex.operations()` raises `pytest.UsageError(f"{item.nodeid}: verifies({name!r}) is not an operation")`. Import `QuadCortex` inside the hook (the module must stay import-safe offline; `client.py` imports no `hid`, so a top-level import is also fine).

Report: collect outcomes with `pytest_runtest_logreport` into a dict `operation -> list[outcome]` using each item's markers (store the item's marker names on the report via a `pytest_runtest_makereport` hookwrapper, or look them up from `item` in `pytest_runtest_logreport` through `session.items`), then in `pytest_terminal_summary(terminalreporter, exitstatus, config)` when `--hardware` is set:

```python
    cls = getattr(config, "_profile", None)
    if cls is None:          # the session never connected, so there is nothing to report
        return
    passed = {op for op, outcomes in seen.items() if outcomes and all(o == "passed" for o in outcomes)}
    failed = {op for op, outcomes in seen.items() if any(o != "passed" for o in outcomes)}
    verified = set(cls.VERIFIED) if cls.VERIFIED is not EVERYTHING else cls.operations()
    tr = terminalreporter
    tr.section(f"operations on {cls.__name__} (CorOS {', '.join(cls.MEASURED_ON)}, {cls.EVIDENCE.name})")
    tr.line(f"passed:               {sorted(passed)}")
    tr.line(f"failed or skipped:    {sorted(failed)}")
    tr.line(f"passed, not VERIFIED: {sorted(passed - verified)}   <- candidates to add")
    tr.line(f"VERIFIED, not passed: {sorted(verified - passed)}   <- regressions by name")
```

The session fixture sets `request.config._profile = type(client)` after connecting (add `request` to its parameters).

- [ ] **Step 5: Mark every hardware test**

For each `def test_...` in `tests/hardware/test_*.py`, list the `qc.` methods it calls (this includes calls inside helpers it uses in the same file):

```bash
for f in tests/hardware/test_*.py; do echo "== $f"; grep -oE 'qc\.[a-z_]+\(' "$f" | sort | uniq -c | sort -rn; done
```

Then decorate each test with `@pytest.mark.verifies("name", ...)` naming the operations it EXERCISES AND ASSERTS ON, not every method it touches for setup (a test that calls `qc.read_current_preset()` only to find a block is not verifying `read_current_preset`). Judgement call per test; when in doubt, mark fewer. Tests that only read state to assert on the cache (`test_model_state.py`) mark the reads they assert on. Run `tests/test_hardware_markers.py` after each file; its message lists what remains.

- [ ] **Step 6: Fill `UNMARKED_OPERATIONS`**

Run `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest tests/test_hardware_markers.py -q`. Its failure lists the operations no marker names. For each, add an entry with the reason: "no hardware test yet; <what it would need>", "cannot run state-neutrally: <why>", or "cloud/account surface, parked per STEERING §2". Do not invent a marker to make the list shorter.

- [ ] **Step 7: `test_generated_constants.py` compares against the profile's snapshot**

Replace lines 42-47:

```python
@pytest.mark.parametrize("name", ["models", "params", "options"])
def test_the_committed_snapshot_matches_this_unit(live_catalog, profile, name):
    """Read-only. Compares against the CONNECTED profile's snapshot (ADR-0020),
    so a snapshot for another firmware never turns this unit's run red."""
    snapshot = getattr(profile, name)
    if isinstance(snapshot, NoSnapshot):
        pytest.fail(f"{profile.__name__} has no {name} snapshot yet; run "
                    f"scripts/generate_{name}.py --snapshot <coros_x_y_z> against this unit")
    generated = _generator(name).render(live_catalog, snapshot=snapshot.__name__.rsplit(".", 2)[-2])
    committed = pathlib.Path(snapshot.__file__).read_text(encoding="utf-8")
    if generated == committed:
        return
    ...  # keep the existing diff-printing failure below
```

with `from pyquadcortex.protocol.support import NoSnapshot`. Keep whatever the existing test does after the equality check (it prints a diff and names the regenerate command) and update that command to `--snapshot`.

- [ ] **Step 8: `tests/test_hardware_gate.py`**: it collects the hardware tree under `--hardware` with `hid` poisoned; the new marker validation runs at collection, so a wrong marker name would surface there too. In the test whose subprocess result is asserted `== pytest.ExitCode.OK` (line 98), add beside that assertion:

```python
    assert "UsageError" not in result.stderr, result.stderr   # a verifies() name that is not an operation
```

- [ ] **Step 9: Run the offline suite and mypy**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m mypy pyquadcortex`
Expected: all pass, including `tests/test_hardware_gate.py` (which imports every hardware module offline) and `tests/test_hardware_markers.py`.

- [ ] **Step 10: Commit**

```bash
git add -A
git commit -m "test: the hardware suite names the operations it verifies and reports them per profile"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/STEERING.md` (§5 table, §7 change log), `CLAUDE.md` (the ADR-0020 bullet), `docs/protocol.md` (header lines 7-19), `docs/architecture.md` ("Adapting to a new CorOS version"), `contributing.md`, `changelog.md`, `docs/api.md`

- [ ] **Step 1: STEERING §5**, add a row:

```
| Profile is the class | A connection resolves `(device_type, zenos_git_hash)` to a client class before the handshake; `QuadCortex` is 4.0.1 and the base, a subclass declares what differs and refuses what it has not verified | One `if firmware ==` in a method body is the smell polymorphism removes; the decision is made once, by which class is instantiated (see ADR-0020) | `QuadCortex41` in `pyquadcortex/protocol/profiles.py` | `ALWAYS`: the lifecycle methods every profile needs to connect and clean up |
```

**STEERING §7 change log**, a new entry above the 2026-09-03 ADR-0020 entry:

```
### 2026-09-04 - The profile seam is built (ADR-0020)

**What changed:** `connect()` reads the unit's `Version` before the handshake,
resolves `(device_type, zenos_git_hash)` in a registry of profile classes, and
refuses an unknown pair with `UnsupportedDevice`. `QuadCortex` declares itself
as the 4.0.1 profile; `QuadCortex41` connects and verifies nothing until a 4.1
unit's suite run fills its `VERIFIED` set; `QuadCortexMini` is recognised and
refused. An operation a profile has not verified refuses under the default
`Support.VERIFIED` and runs with one warning under `Support.EXPERIMENTAL`.
Generated constants live in `pyquadcortex/protocol/catalogs/coros_4_0_1/`;
`protocol.models` and friends are shims over it. `set_block` checks the live
catalog before sending. The hardware suite marks the operations each test
verifies and prints, per profile, which passed.

**Why:** the ADR-0020 entry below records the decision; this is the code.

**What did NOT change, on purpose:** `protocol.models` still means 4.0.1;
`CC_VERSION` still announces 4.0.1 on every profile; no 4.1 snapshot ships.
```

- [ ] **Step 2: CLAUDE.md**: in the ADR-0020 bullet, replace "(ADR-0020; the rule is decided, the seam is not built yet)" with "(ADR-0020)", and replace "Until the seam is built, `protocol.models`/`params`/`options` mean the QC 4.0.1 snapshot" with "`protocol.models`/`params`/`options` are shims over the QC 4.0.1 snapshot in `protocol/catalogs/coros_4_0_1/`; a connection's own snapshot is `qc.models`". Append two sentences: "A subclass of `QuadCortex` lists what it has verified in `VERIFIED`; everything else it inherits is guarded and refuses under `Support.VERIFIED`. Never put a version check in a method body - override on the profile class, and list the override in `VERIFIED`."

- [ ] **Step 3: protocol.md header**: after "A profile is what the unit reports in its `Version` reply", add "and is a client class: `QuadCortex` (4.0.1), `QuadCortex41`, `QuadCortexMini` in `pyquadcortex/protocol/profiles.py`."

- [ ] **Step 4: architecture.md**: retitle the section "Adding a device profile (a new CorOS version or a new model)" and replace the preamble paragraph added in PR #51 with the four steps from `QuadCortex41`'s docstring (generate the snapshot, run the suite, fill `VERIFIED`, record differences beside the 4.0.1 record). Keep the numbered re-verification checklist below it.

- [ ] **Step 5: contributing.md**: add a section "Adding a device profile" with the same four steps, plus: profiles are named by CorOS version; `MEASURED_ON` lists exact strings; a patch release is added after a suite run, not assumed.

- [ ] **Step 6: changelog.md** under `## Unreleased`, above the ADR-0020 entry from PR #51:

```
### BREAKING: the generators take `--snapshot`, and constants moved

`scripts/generate_models.py`, `generate_params.py` and `generate_options.py`
require `--snapshot coros_x_y_z` and write `pyquadcortex/protocol/catalogs/<snapshot>/`;
`--out` is gone. The generated modules moved there; `pyquadcortex.protocol.models`,
`params` and `options` still import and still mean CorOS 4.0.1, as shims.

### The connection knows which unit it is talking to (ADR-0020)

`connect()` reads the unit's `Version` first and picks the profile class for
`(device_type, zenos_git_hash)`: `QuadCortex` for a Quad Cortex on 4.0.1,
`QuadCortex41` for 4.1.0, and `UnsupportedDevice` for anything else, before
the handshake. `connect(profile=...)` names a class deliberately for a unit
nobody has measured. `connect(support=Support.EXPERIMENTAL)` runs operations
a profile has not verified, with one warning each; the default
`Support.VERIFIED` refuses them. `qc.models`, `qc.params`, `qc.options` are the
connection's own constants; `qc.unverified_operations` says what its profile
has not verified. `set_block` refuses a model id the unit's catalog lacks.
The Quad Cortex Mini is recognised and refused with instructions.
```

- [ ] **Step 7: api.md**: add `connect(profile=, support=)`, `Support`, `UnsupportedDevice`, `qc.models` to the table; the `test_api_method_table_entries_exist_where_the_table_says` test in `tests/test_docs.py` checks the names resolve.

- [ ] **Step 8: Run the docs tests and the suite**

Run: `PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q tests/test_docs.py tests/test_coverage_table.py && PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q`
Expected: all pass. `git diff --check` clean.

- [ ] **Step 9: Commit**

```bash
git add -A
git commit -m "docs: the profile seam in STEERING, CLAUDE, protocol, architecture, contributing, changelog, api"
```

---

## After the last task

Run the whole offline suite and mypy one more time from a clean tree, then the pre-PR self-review per the maintainer's `open-pr` skill. The PR body's test plan must say the hardware suite was NOT run in this work (nothing here needed the unit) and that the first thing to do on the maintainer's 4.0.1 unit after merge is `pytest tests/hardware --hardware`, which must print a report whose "VERIFIED, not passed" list is empty.

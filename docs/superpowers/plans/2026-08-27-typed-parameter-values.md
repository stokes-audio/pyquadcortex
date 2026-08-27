# Typed parameter values - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `set_param` takes one positional value that carries its own units, replacing `value=`, `real=` and `text=`.

**Architecture:** A new `pyquadcortex/protocol/values.py` holds `Encoded` (the device's 0..1) and `Real` (the parameter's own scale), with `Db`, `Hertz`, `Milliseconds`, `Seconds`, `Percent`, `Semitones`, `Cents` and `Bpm` subclassing `Real` to add a checkable claim about the unit. `set_param` dispatches on the value's type; the unit claim is checked against the catalog's `units` string before converting.

**Spec:** `docs/superpowers/specs/2026-08-27-typed-parameter-values-design.md`. Read it first - it records why each name was chosen and what was rejected.

**Tech Stack:** Python 3.11, pytest. Run the suite with:
`PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest -q`

## Global Constraints

- **The unit is NOT connected.** Do all offline work first and batch every hardware check into ONE request at the end (Task 7). Do not ask twice.
- Hard breaks, no aliases. Every break goes in `docs/migration.md`.
- No name may survive with a changed meaning. Nothing here does; keep it that way.
- Evidence-bearing docstrings: confirmed on hardware vs inferred from the schema.
- `import hid` stays exactly once, lazily, in `session.open_device()`.
- Changed code under a path in `docs/STEERING.md` § Owned Paths updates STEERING/CLAUDE/ADR in the same PR.
- Never auto-add an agent name as commit co-author. No em-dashes, no characters off the keyboard.
- Triage the PR with a spawned agent before handing it over.

## Scale of the migration

43 `real=` call sites, 51 `value=`, across `pyquadcortex/`, `examples/` and `tests/`. Most are mechanical.

## File Structure

| File | Responsibility |
|---|---|
| `pyquadcortex/protocol/values.py` | NEW. The value types and their unit claims |
| `pyquadcortex/protocol/client.py` | `set_param` dispatches on the value's type |
| `pyquadcortex/protocol/__init__.py` | Export the types |
| `tests/test_values.py` | NEW. The types in isolation |
| `docs/STEERING.md` | The boundary clarification |
| `docs/ADR.md` | ADR-0016 |

---

### Task 1: The value types

**Files:** Create `pyquadcortex/protocol/values.py`, `tests/test_values.py`

**Interfaces produced:** `Value`, `Encoded`, `Real`, `Db`, `Hertz`, `Milliseconds`, `Seconds`, `Percent`, `Semitones`, `Cents`, `Bpm`; `Real.check_unit(spec)`.

- [ ] **Step 1: Write the failing test**

```python
def test_a_unit_type_accepts_the_units_the_catalog_spells_two_ways():
    """`Cents`/`cents` and `Semitones`/`st` are the same unit twice."""
    assert values.Cents.CATALOG_UNITS == frozenset({"Cents", "cents"})
    assert values.Semitones.CATALOG_UNITS == frozenset({"Semitones", "st"})


def test_the_wrong_unit_names_both_sides():
    hz = catalog.Parameter(index=0, name="FREQ", minimum=20.0, maximum=20000.0,
                           default=0.0, units="Hz", type="float")
    with pytest.raises(TypeError, match="Hz.*Db|Db.*Hz"):
        values.Db(-3.1).check_unit(hz)
    values.Hertz(217).check_unit(hz)        # must not raise
    values.Real(217).check_unit(hz)         # Real claims nothing


def test_a_value_is_a_float_and_says_what_it_is():
    assert float(values.Db(-3.1)) == pytest.approx(-3.1)
    assert repr(values.Db(-3.1)) == "Db(-3.1)"
```

- [ ] **Step 2: Run it and watch it fail**

`pytest tests/test_values.py -v` - no module `values`.

- [ ] **Step 3: Implement**

```python
class Value(float):
    """A parameter value that knows which SCALE it is on."""
    __slots__ = ()
    def __repr__(self):
        return f"{type(self).__name__}({float(self)!r})"


class Encoded(Value):
    """The device's own 0..1, identical for every parameter."""
    __slots__ = ()


class Real(Value):
    """The value on the PARAMETER's scale, whatever that scale is."""
    __slots__ = ()
    #: Catalog `units` strings this type claims. Empty means "claims nothing".
    CATALOG_UNITS: frozenset[str] = frozenset()

    def check_unit(self, spec) -> None:
        if not self.CATALOG_UNITS or spec.units in self.CATALOG_UNITS:
            return
        raise TypeError(
            f"{type(self).__name__}({float(self)!r}) says this value is in "
            f"{sorted(self.CATALOG_UNITS)[0]}, and the catalog says "
            f"{spec.name!r} is in {spec.units!r}. Use the matching type, or "
            f"Real(...) to make no claim about the unit."
        )


class Db(Real):
    __slots__ = ()
    CATALOG_UNITS = frozenset({"dB"})
# ... Percent "%", Hertz "Hz", Milliseconds "ms", Seconds "s",
#     Semitones {"Semitones", "st"}, Cents {"Cents", "cents"}, Bpm "BPM"
```

Subclassing `float` is deliberate: `float(Db(-3.1))` works, arithmetic works, and `set_param` distinguishes a typed value from a bare number with `isinstance(v, Value)` rather than by type of number.

- [ ] **Step 4: Tests pass.** `pytest tests/test_values.py -v`

- [ ] **Step 5: Commit**

```bash
git commit -m "feat: parameter values that carry their own scale"
```

---

### Task 2: `set_param` takes one positional value

**Files:** Modify `pyquadcortex/protocol/client.py`, `pyquadcortex/protocol/__init__.py`; Test: `tests/test_client.py`

**Consumes:** everything from Task 1.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_unit_type_converts_through_the_catalog():
    qc = _scale_client()
    qc.set_param(LaneOutput(0), "VOLUME", Db(0.0))
    written = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.76923, abs=1e-4)


def test_encoded_goes_straight_to_the_wire_and_needs_no_catalog():
    qc = client.QuadCortex(FakeTransport())         # no catalog at all
    qc.set_param(Block(0, 1), 3, Encoded(0.25))
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].float_value == pytest.approx(0.25)


def test_real_and_encoded_zero_are_different_things():
    """The pair that makes the type mandatory: unity against silence."""
    qc = _scale_client()
    qc.set_param(LaneOutput(0), "VOLUME", Real(0.0))
    unity = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    qc.set_param(LaneOutput(0), "VOLUME", Encoded(0.0))
    off = qc._t.sent[-1].preset.chains[0].output_control[0].params[0]
    assert unity.param_values[0].float_value == pytest.approx(0.76923, abs=1e-4)
    assert off.param_values[0].float_value == pytest.approx(0.0)


def test_a_bare_number_is_refused_and_the_message_rewrites_the_call():
    qc = _scale_client()
    with pytest.raises(TypeError, match="Real\\(-3.1\\)|Db\\(-3.1\\)"):
        qc.set_param(LaneOutput(0), "VOLUME", -3.1)


def test_a_string_is_itself():
    qc = _option_client()
    qc.set_param(Block(0, 2, 4003), "IR PATH", "/media/x.wav")
    written = qc._t.sent[-1].preset.chains[0].models[0].params[0]
    assert written.param_values[0].string_value == "/media/x.wav"
```

- [ ] **Step 2: Run and watch them fail**

- [ ] **Step 3: Implement**

Signature becomes `set_param(self, target, param, value=None, *, scene=None, promote=True)`. Dispatch, in this order:

```python
if isinstance(value, bool):        ...existing two-option-switch path, unchanged...
elif isinstance(value, str):       prm.param_values.add().string_value = value
elif isinstance(value, Encoded):   wire = float(value)          # no catalog
elif isinstance(value, Real):
    spec = spec or target.spec_at(index, self._get_catalog)
    value.check_unit(spec)
    wire = target.normalize(index, float(value), self._get_catalog, spec)
else:
    raise TypeError(...)           # show the call rewritten
```

The bare-number message must show the fix, because the fix is mechanical:

```
set_param needs a value that says what scale it is on. You passed -3.1.
  Real(-3.1)      the parameter's own scale
  Db(-3.1)        the same, and checked against the catalog's unit
  Encoded(-3.1)   the device's own 0..1
```

Keep `Encoded` off the catalog path - that is what preserves the free indexed write.

- [ ] **Step 4: Migrate the suite.** 43 `real=` and 51 `value=` call sites across `pyquadcortex/`, `examples/` and `tests/`. Mechanical: `real=X` becomes the unit type where the parameter's unit is known and `Real(X)` otherwise, `value=X` becomes `Encoded(X)`, `text=X` becomes `X`. **Do them by hand or with a reviewed script; a blanket regex has damaged this codebase before** - a `count=0` substitution once rewrote method signatures across `client.py`.

- [ ] **Step 5: Full suite green.** `pytest -q`

- [ ] **Step 6: Commit**

---

### Task 3: Reads hand back the same types

**Files:** Modify `pyquadcortex/protocol/client.py` (`param_state`, `option_at`), `pyquadcortex/protocol/catalog.py` (`Parameter.to_real`); Test: `tests/test_client.py`

- [ ] **Step 1: Survey first.** `param_state` (client.py:3822) and `to_real` (catalog.py:337) are the two that return a number a caller reads as a value. `beats` and `param_options` return enums and strings and are out of scope. Confirm nothing else does before changing anything.

- [ ] **Step 2: Write the test**

```python
def test_a_read_says_what_units_it_is_in():
    spec = _db_parameter()
    got = spec.to_real(0.76923)
    assert isinstance(got, values.Db)
    assert float(got) == pytest.approx(0.0, abs=1e-3)
    assert repr(got) == "Db(0.0)"
```

- [ ] **Step 3: Implement.** `to_real` returns the type matching `self.units`, falling back to `Real` where the unit has no type or is empty. A lookup `{catalog units string: type}` lives in `values.py` beside the classes, built from their own `CATALOG_UNITS` so it cannot drift.

- [ ] **Step 4: Tests pass, full suite green.**

- [ ] **Step 5: Commit**

---

### Task 4: The boundary clarification and ADR-0016

**Files:** Modify `docs/STEERING.md`, `docs/ADR.md`

- [ ] **Step 1: STEERING.** The "One translation boundary" row's exception currently reads "The protocol layer, which keeps its zero-based indexes and raw scales." Change to say the protocol layer keeps raw COORDINATES, and its scales come from the catalog - with the distinction the spec draws: rows 1-4 and scene letters are a screen convention this library chooses, dB is the device's own unit published in its own catalog, and reading it is quoting rather than translating.

- [ ] **Step 2: ADR-0016.** Follow the existing ADR shape exactly (Status, Decision, Context, Options, Open Questions, Rationale, Consequences). Two things to record:
  - The layer decision and the sharpening above.
  - **Static unit checking, deferred.** Copy the verified mechanism from the spec: `class Param(int, Generic[U])`, mypy 2.3.1 rejects the mismatch, the constant stays an `int`. The three conditions - only bites on generated constants, needs a checker in CI, costs `params.py` its `IntEnum`s. And why deferring is safe: purely additive. This goes in the ADR and not only the spec, because a working document is not where the next person looks.

- [ ] **Step 3: Commit**

---

### Task 5: Docs and examples

**Files:** `docs/migration.md`, `docs/api.md`, `changelog.md`, `examples/*.py`, `docs/protocol.md`

- [ ] **Step 1: `docs/migration.md`.** A table: `value=X` -> `Encoded(X)`, `real=X` -> the unit type, `text=X` -> `X`. Lead with the `Real(0.0)` versus `Encoded(0.0)` pair, because that is the one a mechanical migration can get wrong: on a lane VOLUME they are unity and silence.

- [ ] **Step 2: Examples.** Every example uses the unit type, never `Encoded`, unless the parameter genuinely has no catalog entry.

- [ ] **Step 3: `docs/api.md` and `changelog.md`.**

- [ ] **Step 4: The never-advertised rule needs a test**, or it rots:

```python
def test_no_docstring_or_example_reaches_for_encoded_where_a_unit_type_serves():
    """`Encoded` is an escape hatch, not a route. It stays available and
    undocumented on methods that take something better."""
```

Read the source of `examples/` and the public docstrings in `client.py`; allow `Encoded` only where a comment on the same or previous line says why.

- [ ] **Step 5: Commit**

---

### Task 6: Offline verification

- [ ] Full suite green.
- [ ] `grep -rn "real=\|value=\|text=" pyquadcortex/ examples/` returns nothing for `set_param`.
- [ ] `tests/test_import_cleanliness.py` still passes - `values.py` must import nothing heavy and certainly not `hid`.
- [ ] `tests/test_namespace.py` records every removed export.

---

### Task 7: Hardware, in ONE batch

The unit is not connected. Ask for it ONCE, with everything ready to run.

- [ ] **Step 1: Write the hardware test first**, so the session is short: one write per unit type against a real parameter of that unit, read back after a reconnect (a read straight after a write returns the PREVIOUS value - this trap has produced two wrong conclusions in this project).
- [ ] **Step 2: Ask him to connect**, saying exactly what will be driven and that it is state-neutral per ADR-0005.
- [ ] **Step 3: Run** `pytest tests/hardware --hardware -q` in full, not just the new file.
- [ ] **Step 4: Commit** with the results in the message.

---

### Task 8: PR

- [ ] Open it, spawn an agent to run `/triage-pr` on it, verify each finding yourself before acting, fix what is real, then hand over the link.

## Self-Review

**Spec coverage.** Types -> Task 1. One positional value -> Task 2. Reads -> Task 3. Layer decision and deferred generics -> Task 4. Never-advertised rule -> Task 5 step 4. Errors -> Task 2 step 3. Testing -> throughout plus Task 7.

**Placeholders.** None; every code step carries its code.

**Type consistency.** `Value`/`Encoded`/`Real`/`Db` from Task 1 are used by name in Tasks 2, 3 and 5. `CATALOG_UNITS` is defined in Task 1 and read by the lookup in Task 3.

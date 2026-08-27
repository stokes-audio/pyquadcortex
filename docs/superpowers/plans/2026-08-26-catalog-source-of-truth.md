# Catalog as the source of truth for scales - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Teach `catalog.py` to read the attributes the device actually publishes, so parameter conversion stops being wrong for 615 parameters and the invented "placeholder range" concept can be deleted.

**Architecture:** `Parameter` grows the fields the XML already carries (`skew`, `options`, `dynamic`, `min_label`, `exp_assignable`). A resolver turns the 8 symbolic `min`/`max` constants into numbers held in `units.FIRMWARE_CONSTANTS`. `to_normalized`/`to_real` apply one power law. `units.MEASURED_SPANS` collapses from 44 hand-measured entries to 14 firmware numbers, and the measurements become a regression table in the tests. A generated `options.py` publishes the option names the catalog was always carrying.

**Tech Stack:** Python 3.11, protobuf, pytest. Run tests with the main checkout's venv:
`PYTHONPATH=$PWD /Users/jonathanstokes/dev/work/personal/pyquadcortex/.venv/bin/python -m pytest`

> **Implementation note, added after the work landed.** This document records
> what was AGREED, and three things changed while building it. `units.Span` was
> deleted rather than kept, because once `Parameter` carried the taper there was
> nothing left for it to hold; `Parameter.span` was never added and the cab path
> is `_layout_spec`, returning a `Parameter`. `lane_level_db`, `tempo_bpm` and
> their inverses were KEPT, not deleted - `device/translate/` delegates to them
> and has no catalog to reach. And the floor turned out to need keying by the
> resolved law rather than by the catalog's constant name; see the review
> findings in the PR.


## Global Constraints

- The one conversion law, confirmed on hardware 2026-08-26:
  `real = min + (max - min) * wire ** (1 / skew)`
- `skew` spellings: a number, `LIN_SKEW` = 1.0, `LOG_SKEW` = 0.3. Dirty values in
  the shipped catalog: `" 0.4"` (leading space, 2 parameters) and `""` (2
  parameters).
- Every conversion between a screen value and a wire value lives in
  `pyquadcortex/device/translate/` - this work is all inside
  `pyquadcortex/protocol/`, which is outside that boundary by design.
- `import hid` must not appear at module scope anywhere.
- Never gitignore or delete `pyquadcortex/protocol/proto/*_pb2.py`.
- Docstrings state their evidence: confirmed on hardware vs inferred from the schema.
- Hard breaks, no aliases. Every break gets a `docs/migration.md` entry.
- Removed exports need a `tests/test_namespace.py` entry so the parity guard
  records them rather than losing them.
- Never auto-add an agent name as commit co-author. No em-dashes.

## File Structure

| File | Responsibility |
|---|---|
| `pyquadcortex/protocol/catalog.py` | Parse every attribute we can name a use for; apply the law |
| `pyquadcortex/protocol/units.py` | The numbers the XML does not spell out, plus port helpers |
| `pyquadcortex/protocol/targets.py` | Address a parameter; borrow the cab layout |
| `pyquadcortex/protocol/options.py` | GENERATED. Option enums |
| `scripts/generate_options.py` | Generator for the above |
| `pyquadcortex/protocol/client.py` | `set_param_option` takes an enum or a bool |
| `tests/test_catalog.py` | Parser and law |
| `tests/test_scales.py` | NEW. The campaign's readings as a regression table |
| `tests/test_options.py` | NEW. Generated module matches the catalog |

---

### Task 1: The catalog reads `skew` and applies the law

**Files:**
- Modify: `pyquadcortex/protocol/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `Parameter.skew: float`, `catalog.parse_skew(raw: str | None) -> float`

- [ ] **Step 1: Write the failing test**

```python
@pytest.mark.parametrize("raw, expected", [
    (None, 1.0), ("LIN_SKEW", 1.0), ("1", 1.0), ("1.0", 1.0),
    ("LOG_SKEW", 0.3),          # confirmed on hardware, NOT a log sweep
    ("0.3", 0.3), ("4.9594844", 4.9594844),
    (" 0.4", 0.4),              # the shipped catalog has a leading space
    ("", 1.0),                  # and two parameters with nothing at all
    ("nonsense", 1.0),
])
def test_parse_skew_cleans_what_the_device_ships(raw, expected):
    assert catalog.parse_skew(raw) == pytest.approx(expected)


def test_to_real_applies_the_taper():
    """Confirmed on hardware 2026-08-26: wire 0.25 read 217 Hz on screen."""
    p = catalog.Parameter(index=1, name="HPF FREQ", minimum=20.0, maximum=20000.0,
                          default=0.0, units="Hz", type="float", skew=0.3)
    assert round(p.to_real(0.25)) == 217
    assert p.to_normalized(217.447) == pytest.approx(0.25, abs=1e-4)
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_catalog.py -k "skew or taper" -v`
Expected: FAIL, no attribute `parse_skew`.

- [ ] **Step 3: Implement**

Add `skew: float = 1.0` to `Parameter`. Add:

```python
LIN_SKEW = 1.0
#: `LOG_SKEW` is NOT a logarithmic sweep. Solved from two hardware readings on
#: 2026-08-26 - an Envelope Filter FREQ (100..10000 Hz) read 197 Hz at wire 0.25
#: and its RESO (1..10) read 4.45 at wire 0.75 - which give exponents 3.3366 and
#: 3.3330 independently, both 1/0.3. A true log sweep would have read 316 and
#: 5.62.
LOG_SKEW = 0.3


def parse_skew(raw: str | None) -> float:
    """The taper exponent's reciprocal, from the catalog's `skew` attribute."""
    if raw is None:
        return LIN_SKEW
    text = raw.strip()
    if text == "LIN_SKEW":
        return LIN_SKEW
    if text == "LOG_SKEW":
        return LOG_SKEW
    try:
        value = float(text)
    except ValueError:
        return LIN_SKEW
    return value if value > 0 else LIN_SKEW
```

Rewrite the two converters to apply it:

```python
    def to_real(self, normalized: float) -> float:
        span = self.maximum - self.minimum
        if span == 0:
            return self.minimum
        clamped = min(1.0, max(0.0, normalized))
        return self.minimum + span * clamped ** (1.0 / self.skew)

    def to_normalized(self, real: float) -> float:
        span = self.maximum - self.minimum
        if span == 0:
            return 0.0
        fraction = min(1.0, max(0.0, (real - self.minimum) / span))
        return fraction ** self.skew
```

Read it in `parse_model_repo`: `skew=parse_skew(p.get("skew"))`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/catalog.py tests/test_catalog.py
git commit -m "feat: the catalog's skew is the taper, and one law covers it"
```

---

### Task 2: Symbolic `min` and `max` resolve to firmware numbers

**Files:**
- Modify: `pyquadcortex/protocol/units.py`, `pyquadcortex/protocol/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `units.FIRMWARE_CONSTANTS: dict[str, float]`, used by
  `catalog._as_float(value, fallback, constants)`

- [ ] **Step 1: Write the failing test**

```python
def test_a_symbolic_bound_resolves_to_its_firmware_number():
    xml = ('<Models><Category id="12" name="Cabsim Guitar (M)">'
           '<Model id="12000" name="Default Cabsim">'
           '<Parameter name="LEVEL" type="float" units="dB"'
           ' min="MIN_CABSIM_DB" max="MAX_CABSIM_DB" skew="4.9594844"'
           ' min_string="OFF"/>'
           '</Model></Category></Models>')
    cat = catalog.parse_model_repo(make_payload(xml))
    p = cat[12000].parameters[0]
    assert (p.minimum, p.maximum) == (-40.0, 6.0)


def test_an_unknown_symbolic_bound_is_loud():
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="Z" min="MIN_FUTURE_THING" max="1"/>'
           '</Model></Category></Models>')
    with pytest.raises(ValueError, match="MIN_FUTURE_THING"):
        catalog.parse_model_repo(make_payload(xml))
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_catalog.py -k symbolic -v`
Expected: FAIL, `minimum` is 0.0.

- [ ] **Step 3: Implement**

In `units.py`, replacing `MEASURED_SPANS`:

```python
#: The numeric bounds the catalog names but does not spell out. `min` and `max`
#: are sometimes a NAME - `min="MIN_CABSIM_DB"` - and the value lives in the
#: firmware. Each entry records how its number is known.
#:
#: This table exists because `_as_float` used to fall back to 0.0 and 1.0 for a
#: name it could not read, which is what invented the "placeholder range" this
#: library carried for several releases.
FIRMWARE_CONSTANTS = {
    # A PCOM cab spells the very same LEVEL knob out: min="-40" max="6".
    # Confirmed on screen at three points through the skew=4.9594844 taper -
    # wire 0.01/0.50/1.00 read -21.8/0.0/6.0 dB, 2026-08-26.
    "MIN_CABSIM_DB": -40.0, "MAX_CABSIM_DB": 6.0,
    # steps=241 over the span means 0.1 dB steps, which fixes it at 24 dB wide.
    # Measured 2026-08-25 on Parametric-8 at 0.0/0.10/0.50/1.00 -> -12.0/-9.6/
    # 0.0/+12.0, and separately on Parametric-3 and the Output Equalizer.
    "MIN_EQ_DB": -12.0, "MAX_EQ_DB": 12.0,
    # The lane, mixer and splitter LEVEL family. Measured 2026-08-25: lane
    # VOLUME -3.1 at 0.71 and +12.0 at 1.0; MIXER LEVEL -24.4 at 0.30; splitter
    # LEVEL TO B -3.1 at 0.71. Wire 0.0 reads OFF, which min_string declares.
    "MIN_MIXER_DB": -40.0, "MAX_MIXER_DB": 12.0,
    # The send side of the FX loop. Measured 2026-08-26 at five points including
    # both ends - -39.6 at 0.01, -36.0 at 0.10, -20.0 at 0.50, -10.0 at 0.75,
    # 0.0 at 1.00. It tops out at unity: a send cannot boost.
    "MIN_FXLOOP_OUT_GAIN_DB": -40.0, "MAX_FXLOOP_OUT_GAIN_DB": 0.0,
    # The return side. Measured 2026-08-26: -1.0 at 0.75, -14.0 at 0.50,
    # -34.8 at 0.10.
    "MIN_FXLOOP_IN_GAIN_DB": -40.0, "MAX_FXLOOP_IN_GAIN_DB": 12.0,
    # steps=201 over the span means whole bpm, fixing it at 200 wide. Measured
    # 2026-08-25 at 59/111/120 bpm against wire 0.095/0.355/0.400.
    "MIN_TEMPO": 40.0, "MAX_TEMPO": 240.0,
    # NOT measured. NC_Recorder OUT LEVEL - see DO_NOT_PROBE. steps=41 and
    # defaultValue=MAX_INPUT_TRIM are all we have, so these are the widest
    # bounds consistent with the family and are marked unverified in the docs.
    "MIN_INPUT_TRIM": -40.0, "MAX_INPUT_TRIM": 0.0,
    # NOT measured. The two EQ FREQUENCY knobs, skew 0.177, steps=200.
    "MIN_EQ_FREQ": 20.0, "MAX_EQ_FREQ": 20000.0,
}
```

In `catalog.py`, make `_as_float` consult it and raise on an unknown name:

```python
def _as_float(value, fallback=0.0):
    if value is None:
        return fallback
    text = value.strip()
    try:
        return float(text)
    except ValueError:
        pass
    if text in units.FIRMWARE_CONSTANTS:
        return units.FIRMWARE_CONSTANTS[text]
    raise ValueError(
        f"the catalog names a bound this build has no number for: {text!r}. "
        f"Add it to units.FIRMWARE_CONSTANTS with the evidence for its value. "
        f"Falling back to 0..1 is what created the placeholder-range bug."
    )
```

Note: `units.py` must not import `catalog.py` (that direction already exists the
other way). Import `units` inside `catalog.py`.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_catalog.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/catalog.py pyquadcortex/protocol/units.py tests/test_catalog.py
git commit -m "feat: a symbolic bound resolves rather than collapsing to 0..1"
```

---

### Task 3: Delete the placeholder concept

**Files:**
- Modify: `pyquadcortex/protocol/catalog.py`, `pyquadcortex/protocol/targets.py`,
  `pyquadcortex/protocol/units.py`, `pyquadcortex/protocol/__init__.py`
- Test: `tests/test_catalog.py`, `tests/test_targets.py`, `tests/test_namespace.py`

**Interfaces:**
- Removes: `Parameter.range_is_placeholder`, `Parameter._reject_placeholder`,
  `units.MEASURED_SPANS`, `units.UNCONVERTIBLE`, `units.EQ_GAIN_SPAN`,
  `units.CAB_LEVEL_UNITY`, `units.lane_level_db`, `units.db_to_lane_level`,
  `units.tempo_bpm`, `units.bpm_to_tempo`
- Keeps: `units.input_level_db`, `units.db_to_input_level` (input PORTS are not
  catalog models), `units.UNITY_LEVEL`, `units.DO_NOT_PROBE`, `units.Span`,
  `units.measured_to_wire`, `units.measured_from_wire`

- [ ] **Step 1: Write the failing test**

```python
def test_the_placeholder_concept_is_gone():
    """There was never such a thing - see docs/ADR.md ADR-0015."""
    assert not hasattr(catalog.Parameter, "range_is_placeholder")
    assert not hasattr(units, "MEASURED_SPANS")
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_catalog.py -k placeholder -v`
Expected: FAIL.

- [ ] **Step 3: Implement**

Delete the members above. In `targets.normalize`, drop the `MEASURED_SPANS`
lookup entirely - the catalog now answers - leaving:

```python
    def normalize(self, index, real, get_catalog, spec=None):
        span = self._layout_span(index, get_catalog)
        if span is not None:
            return measured_to_wire(span, real)
        spec = spec if spec is not None else self.spec_at(index, get_catalog)
        ...unchanged refusals...
        return spec.to_normalized(real)
```

Rewrite `_layout_span` to borrow from the catalog rather than a table: a cab
model lists two mic selectors while the wire carries the whole `Default Cabsim`
layout, so look up `get_catalog()[CABSIM_LAYOUT].parameters[index]` and return
its `Span`. Add `Parameter.span` returning
`Span(minimum, maximum, exponent=1/skew, unit=units_string)`.

Add the removals to `tests/test_namespace.py` as `DELIBERATE_REMOVALS` with the
reason, beside the existing `DELIBERATE_RENAMES`, and teach the parity test to
accept a name on either list.

Remove the four deleted helpers from `protocol/__init__.py`'s imports and
`__all__`.

- [ ] **Step 4: Run the whole suite**

Run: `pytest -x -q`
Expected: PASS. Some `tests/test_targets.py` cases pinned to the old fitted
numbers will need updating to the catalog's - that is the point of the change,
and Task 4 replaces them wholesale.

- [ ] **Step 5: Commit**

```bash
git add -A
git commit -m "feat: there is no such thing as a placeholder range"
```

---

### Task 4: The campaign's readings become the regression suite

**Files:**
- Create: `tests/test_scales.py`
- Modify: `tests/test_targets.py` (drop the span tables it now duplicates)

**Interfaces:**
- Consumes: `catalog.Parameter.to_real`, `units.FIRMWARE_CONSTANTS`

- [ ] **Step 1: Write the test**

One table, every reading taken during the campaign, asserted against a
conversion the catalog derives on its own. Each row is
`(model_id, index, wire, screen, tolerance, when)`.

```python
#: Every screen reading taken during the placeholder campaign, 2026-08-25 and
#: 2026-08-26. These were once the SOURCE of the spans; the catalog turned out
#: to publish them, so they are now the evidence that it does not lie.
READINGS = [
    # (model, index, wire, screen dB/Hz, tolerance, date)
    (12000,  2, 0.01, -21.8, 0.05, "2026-08-26"),   # cab MIC 1 LEVEL, taper
    (12000,  2, 0.50,   0.0, 0.05, "2026-08-26"),
    (12000,  2, 1.00,   6.0, 0.05, "2026-08-26"),
    (4000,   0, 0.00, -12.0, 0.05, "2026-08-25"),   # Parametric-8 band 1 GAIN
    (4000,   0, 0.10,  -9.6, 0.05, "2026-08-25"),
    (4000,   0, 0.50,   0.0, 0.05, "2026-08-25"),
    (4000,   0, 1.00,  12.0, 0.05, "2026-08-25"),
    (4003,   1, 0.25, 217.0, 0.5,  "2026-08-26"),   # Low-High Cut HPF FREQ
    (4003,   3, 0.75, 7678.0, 0.5, "2026-08-26"),   # LPF FREQ
    (4003,   4, 0.25, -10.0, 0.05, "2026-08-26"),   # OUTPUT, no skew
    (24003,  5, 0.25, 197.0, 0.5,  "2026-08-26"),   # Envelope Filter FREQ, LOG_SKEW
    (24003,  7, 0.75,   4.45, 0.005, "2026-08-26"), # RESO, LOG_SKEW
    (25000,  0, 0.095, 59.0, 0.5,  "2026-08-25"),   # TEMPO
    (25000,  0, 0.400, 120.0, 0.5, "2026-08-25"),
]


@pytest.mark.parametrize("model_id, index, wire, screen, tol, when", READINGS)
def test_the_catalog_reproduces_what_the_screen_showed(
        shipped_catalog, model_id, index, wire, screen, tol, when):
    p = shipped_catalog[model_id].parameters[index]
    assert p.to_real(wire) == pytest.approx(screen, abs=tol), (
        f"{p.name} on model {model_id} read {screen} on the unit's screen at "
        f"wire {wire} on {when}"
    )
```

`shipped_catalog` is a fixture parsing a committed XML fixture - add
`tests/fixtures/model_repo.xml.gz` from the unit dump so the suite stays offline.

- [ ] **Step 2: Run and verify**

Run: `pytest tests/test_scales.py -v`
Expected: every row PASS. A failing row means the catalog and the unit disagree,
which is a finding, not a test to relax.

- [ ] **Step 3: Add the completeness guard**

```python
def test_every_symbolic_bound_the_catalog_ships_has_a_number(shipped_catalog):
    """A firmware update that adds one must fail loudly, not become 0..1."""
    # parse_model_repo already raises; this proves it against the real file.
    assert shipped_catalog[12000].parameters[2].minimum == -40.0
```

- [ ] **Step 4: Commit**

```bash
git add tests/test_scales.py tests/fixtures/model_repo.xml.gz tests/test_targets.py
git commit -m "test: the measurements are now evidence that the catalog is right"
```

---

### Task 5: The catalog reads the remaining attributes

**Files:**
- Modify: `pyquadcortex/protocol/catalog.py`
- Test: `tests/test_catalog.py`

**Interfaces:**
- Produces: `Parameter.options: tuple[str, ...]`, `Parameter.dynamic: bool`,
  `Parameter.min_label: str`, `Parameter.max_label: str`,
  `Parameter.exp_assignable: bool`, `Parameter.show_as_integer: bool`

- [ ] **Step 1: Write the failing test**

```python
def test_option_names_come_from_the_catalog():
    """`set_param_option` used to say they do not. They always did."""
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="MODE" type="comboBox" min="0" max="2" steps="3"'
           ' stepNames="Normal,Vibrato,Vibrato Bright Off"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.options == ("Normal", "Vibrato", "Vibrato Bright Off")
    assert p.dynamic is False


def test_a_dynamic_list_is_marked():
    """Its entries include upstream blocks, so the preset is authoritative."""
    xml = ('<Models><Category id="1" name="x"><Model id="1" name="y">'
           '<Parameter name="SOURCE" type="comboBox" dynamic="true" min="0"'
           ' max="2" steps="3" stepNames="Off,In 1,R1C1"/>'
           '</Model></Category></Models>')
    p = catalog.parse_model_repo(make_payload(xml))[1].parameters[0]
    assert p.dynamic is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `pytest tests/test_catalog.py -k "option_names or dynamic" -v`

- [ ] **Step 3: Implement**

Add the fields, and in `parse_model_repo`:

```python
    options=tuple(s.strip() for s in p.get("stepNames").split(","))
            if p.get("stepNames") else (),
    dynamic=p.get("dynamic") == "true",
    min_label=p.get("min_string", ""),
    max_label=p.get("max_string", ""),
    exp_assignable=p.get("expAssignable") != "false",
    show_as_integer=p.get("showAsInteger") == "true",
```

Make `option_count` prefer `len(self.options)` when the list is fixed, keeping
`steps` for the dynamic case where the catalog's count is known to be wrong.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_catalog.py -v`

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/catalog.py tests/test_catalog.py
git commit -m "feat: the catalog carries option names, labels and assignability"
```

---

### Task 6: Generated option enums

**Files:**
- Create: `scripts/generate_options.py`, `pyquadcortex/protocol/options.py`
- Test: `tests/test_options.py`

**Interfaces:**
- Produces: 111 `IntEnum` classes, `options.OPTION_LABELS: dict[type, tuple[str, ...]]`

- [ ] **Step 1: Write the generator**

Mangling, exactly:

```python
SPELLING_FIXES = {
    # The device's own typo, on 16 INVERT parameters. We send its spelling; the
    # member reads correctly.
    "Noral": "NORMAL",
}


def member_name(label: str) -> str:
    fixed = SPELLING_FIXES.get(label.strip(), None)
    if fixed:
        return fixed
    text = label.strip().upper()
    text = text.replace("+", "PLUS").replace("%", "PCT")
    if text.startswith("-"):
        text = "MINUS_" + text[1:]
    text = re.sub(r"[^A-Z0-9]+", "_", text).strip("_")
    if not text:
        text = "BLANK"
    if text[0].isdigit():
        text = "N" + text
    return text
```

Skip lists whose labels are `Off,On` or `OFF,ON` - those parameters take a bool.
Skip `dynamic="true"` lists. Name each enum after the parameter name that most
often uses it, in CamelCase; break ties by the lowest model id, and suffix a
collision with `_2`.

- [ ] **Step 2: Generate and eyeball**

Run: `python scripts/generate_options.py --payload <dump>.bin`
Expected: 111 enums. Read the names; they are public API.

- [ ] **Step 3: Write the test**

```python
def test_every_generated_enum_matches_the_catalog(shipped_catalog):
    for enum_type, labels in options.OPTION_LABELS.items():
        assert len(enum_type) == len(labels)
        for member, label in zip(enum_type, labels):
            assert member.value == labels.index(label)


def test_the_device_typo_is_corrected_in_the_name_not_the_wire():
    assert options.Invert.NORMAL.value == 0
    assert options.OPTION_LABELS[options.Invert][0] == "Noral"
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_options.py -v`

- [ ] **Step 5: Commit**

```bash
git add scripts/generate_options.py pyquadcortex/protocol/options.py tests/test_options.py
git commit -m "feat: the option names the catalog always carried, as enums"
```

---

### Task 7: `set_param_option` takes an enum, and booleans take a bool

**Files:**
- Modify: `pyquadcortex/protocol/client.py`
- Test: `tests/test_client.py`

- [ ] **Step 1: Write the failing test**

```python
def test_an_option_enum_selects_by_index(fake_transport, shipped_catalog):
    qc = make_client(fake_transport, shipped_catalog)
    qc.set_param_option(Block(0, 1, model_id=7015), "DYN MODE", options.DynMode.GATE)
    sent = fake_transport.last_preset_message()
    assert sent.param_values[0].float_value == pytest.approx(1.0)


def test_a_two_option_parameter_takes_a_bool(fake_transport, shipped_catalog):
    qc = make_client(fake_transport, shipped_catalog)
    qc.set_param(Block(0, 1, model_id=7040), "SYNC", True)
    assert fake_transport.last_preset_message().param_values[0].float_value == 1.0
```

- [ ] **Step 2: Run to verify it fails**

- [ ] **Step 3: Implement**

`set_param_option` resolves an `IntEnum` member to its index directly, a `str`
against the preset's `dynamic_steps` as now, and an `int` as an index. Drop the
required `source=` argument when the parameter's list is fixed - only a
`dynamic` list needs a preset. Correct the docstring, which currently says the
option names are not in the catalog.

`set_param` accepts a `bool` where the parameter has exactly two options,
writing 0.0 or 1.0. Note `measured_to_wire` already refuses a bool for `real=`,
and that stays: a bool is a value, not a measurement.

- [ ] **Step 4: Run tests**

Run: `pytest tests/test_client.py -v`

- [ ] **Step 5: Commit**

```bash
git add pyquadcortex/protocol/client.py tests/test_client.py
git commit -m "feat: pick an option by enum, and a switch by bool"
```

---

### Task 8: `expAssignable`, exposed and then tested

**Files:**
- Modify: `pyquadcortex/protocol/targets.py`
- Create: `tests/hardware/test_exp_assignable.py`

- [ ] **Step 1: Expose it**

`Parameter.exp_assignable` lands in Task 5. Nothing refuses on it yet: ADR-0010
requires a differential capture first, and none of the 14 has been tested.

- [ ] **Step 2: Run the capture**

`Pattern Tremolo` (7040) is on the grid at row 1; its `STEPS` (index 10) is one
of the 14, and its `DEPTH` (index 4) is not. Assign a pedal to each, reconnect,
and read both back. A read straight after a write returns the PREVIOUS value -
reconnect first.

- [ ] **Step 3: Act on the result**

If `STEPS` refuses and `DEPTH` takes it, add the refusal to
`refuse_if_unassignable` with the capture as `evidence`. If both take it, the
field stays informational and the disagreement between the catalog and the unit
goes in `docs/domain-model.md`'s appendix.

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat: the catalog names 14 parameters that decline a pedal"
```

---

### Task 9: Documentation

**Files:**
- Modify: `docs/protocol.md`, `docs/ADR.md`, `docs/migration.md`,
  `docs/domain-model.md`, `docs/manual-coverage.md`, `CLAUDE.md`, `changelog.md`

- [ ] **Step 1: ADR-0015**

The catalog is the source of truth for scales. Measurements are evidence, and
evidence lives in tests. Record what the old approach cost: 44 hand-measured
entries covering 19 models, several days of screen readings, and a wrong linear
conversion on 615 parameters that nobody had noticed.

- [ ] **Step 2: `docs/protocol.md`**

Replace the "Some catalog ranges are placeholders" section. It is wrong. Put the
law, the three `skew` spellings, the eight symbolic families and the hardware
confirmation table in its place.

- [ ] **Step 3: `docs/migration.md`**

A new version-pair section. Lead with the silent one: `to_normalized` and
`to_real` return different numbers for 615 parameters - same name, same
signature, better answer. Then the removed names and their replacements.

- [ ] **Step 4: `docs/domain-model.md` appendix**

The attributes we can see and cannot yet explain: `toggleOn` / `toggleOff` /
`toggleStep` (212 parameters), `displayPos`, `align`, `selfTestValue`, `blob`
(changes per fetch), and the device's own typo `isplayPos`.

- [ ] **Step 5: `CLAUDE.md`**

Add the rule: a bound the catalog names needs a `FIRMWARE_CONSTANTS` entry with
its evidence, in the same commit.

- [ ] **Step 6: Commit**

```bash
git add docs/ CLAUDE.md changelog.md
git commit -m "docs: one law, eight families, and what the placeholder cost"
```

---

## Self-Review

**Spec coverage.** Catalog attributes -> Tasks 1 and 5. Symbolic bounds -> Task 2.
`units.py` shrink -> Task 3. Option enums -> Tasks 6 and 7. `expAssignable` ->
Task 8. Tests -> Task 4 plus each task's own. Breaks and ADR -> Task 9. No gaps.

**Placeholders.** None. Every code step carries its code.

**Type consistency.** `parse_skew` (Task 1) is used by `parse_model_repo` in
Tasks 1, 2 and 5. `FIRMWARE_CONSTANTS` (Task 2) is read by `_as_float` in Task 2
and asserted in Task 4. `Parameter.span` (Task 3) is consumed by `_layout_span`
in the same task. `OPTION_LABELS` (Task 6) is read by `set_param_option` in
Task 7.

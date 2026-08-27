# Typed parameter values (Phase 2)

Agreed 2026-08-27. Phase 2 of the plan in ADR-0014; ADR-0015 is what made it
possible.

## The problem

`set_param` takes the value two ways and neither says what it means:

```python
qc.set_param(t, VOLUME, value=0.71)   # the wire's normalized 0..1
qc.set_param(t, VOLUME, real=-3.1)    # the parameter's own units
```

`real=-3.1` is dB on an EQ band and milliseconds on a delay. Nothing at the call
site says which, the two arguments are mutually exclusive by convention rather
than by type, and a variable holding `-3.1` carries no clue at all.

## The layer, and why

**The protocol layer.** Settled 2026-08-27 after the rules were found to
disagree with each other.

`CLAUDE.md` says conversions live in `device/translate/` "and nowhere else in
the package outside `pyquadcortex/protocol/`" - protocol is carved out.
`docs/STEERING.md` says the pattern does not apply to "the protocol layer, which
keeps its zero-based indexes **and raw scales**". One permits, the other forbids.

The distinction that resolves it, and it sharpens the boundary rather than
bending it:

* **Rows 1-4, slots 1-8, scene letters** are a screen convention THIS LIBRARY
  CHOOSES. The boundary exists so that choice is made in exactly one place.
* **dB, Hz, bpm** are the DEVICE'S OWN units, published in its own catalog with
  its own bounds and taper. Reading them is not translating, it is quoting.

So the protocol layer keeps raw **coordinates**, and its scales come from the
catalog. `docs/STEERING.md` is updated to say that, in the same PR.

The decisive constraint: nothing under `protocol/` may import from `device/`, so
a type defined in the model could not be used by the protocol layer at all.
Putting them in protocol lets issue #13 (OM-M1.5) consume one vocabulary instead
of inventing a parallel one.

## What the catalog says about the population

Counted from the shipped catalog, 3,809 parameters:

| what | count | takes |
|---|---|---|
| a unit and no option list | 1,490 | a unit type |
| unitless, with a real range | 2,315 | `Shown` - see below |
| an option list | 539 | an enum or a bool, unchanged |
| bounds nobody has measured | 1 | `Normalized` only |

Units, by parameter count: `dB` 499, `%` 487, `Hz` 276, `ms` 176, `s` 22,
`Semitones` 18, `Cents` 5, and a tail of `x`, `bits`, `st`, `cents`, `dB/oct`,
`BPM` at 1-2 each.

Note `Cents`/`cents` and `Semitones`/`st` are **the same unit spelled two ways**.
A type collapses them; a string comparison would not.

## The design

### One positional value

```python
qc.set_param(LaneOutput(0), VOLUME, Db(-3.1))
qc.set_param(Tempo(), TEMPO, Bpm(120))
qc.set_param(block, "ATTACK", Milliseconds(12))
qc.set_param(block, "MIX", Percent(35))
qc.set_param(block, "GAIN", Shown(5.0))          # unitless, 0..10 on its own scale
qc.set_param(block, 21, Normalized(0.5))         # an index the catalog omits
qc.set_param(block, IR_PATH_SLOT_1, "/media/...")  # a string is itself
```

`value=`, `real=` and `text=` all go away.

### `Shown` is the general case; a unit type is a checkable claim on top

The 2,315 unitless parameters are the corner that shapes this. A drive's `GAIN`
runs 0..10 with no unit. `Db` is wrong, `Normalized` is wrong (it is not a 0..1
control), and a bare `5.0` is genuinely ambiguous - on a unitless 0..1 control
it could equally mean the wire.

So:

* **`Shown(x)`** means "the number the screen shows, in whatever unit it shows".
  Every parameter accepts it.
* **`Db(x)`, `Hertz(x)`, `Milliseconds(x)`, `Seconds(x)`, `Percent(x)`,
  `Semitones(x)`, `Cents(x)`, `Bpm(x)`** are `Shown` plus an assertion about the
  unit. Passing `Db` to a parameter the catalog calls `Hz` is a `TypeError`
  naming both.
* **`Normalized(x)`** is the wire's own 0..1, accepted everywhere.

That makes the unit types optional precision rather than a wall: a caller who
does not care writes `Shown`, and a caller who wants the mistake caught writes
`Db`. The library's own examples always write the unit type.

The tail units (`x`, `bits`, `dB/oct`) get no type. They take `Shown`, and the
reason is written beside the type list: two parameters each does not earn a
public name, and `Shown` is not a worse answer for them, only a less specific
one.

### `Normalized` stays allowed everywhere, and is never advertised

It is what the wire carries, and an index the catalog does not describe still
needs it. But **no example and no docstring mentions it on a method that takes
something better.** The escape hatch stays; the documentation does not point at
it.

### Reads return the same types

`param_state` and the block readers hand back a typed value, so `-3.1 dB`
round-trips as `Db(-3.1)` rather than `0.7096`, and `repr` says what it is.

### Where it lives

A new `pyquadcortex/protocol/values.py`. Not `units.py`, which is data - the
firmware constants and the port scales - and should stay that.

## What this makes structural, and what it does not

Worth being precise, because the earlier sketch overclaimed.

**Structural:** the unit. `Db` on an `Hz` parameter cannot happen. That is the
`Cents`/`cents` class of mistake gone, and the `real=-3.1`-of-what problem gone.

**NOT structural:** whether a bound is measured. `NC_Recorder OUT LEVEL` carries
`units="dB"`, so `Db(-3.1)` is well-typed for it; the refusal stays a runtime
`ControlNotDrivable`. Making that structural would need a type per parameter,
which is not worth 1 parameter in 3,809.

**Also not structural:** the Off detent. 190 parameters say their bottom is a
word and nobody has measured where the numbers resume, so `Db` on those is
honest about the unit and optimistic about the range. That is queue item 2 and
it is a measurement problem, not a typing one - but the spec should not pretend
otherwise.

## Errors

* Wrong unit: `TypeError`, naming the unit given and the unit the catalog
  publishes.
* A bare number where a value type is required: `TypeError` that shows the call
  rewritten - the fix is mechanical and the message should just say it.
* Out of range: the existing `ValueError` from `Parameter._reject_outside_range`,
  unchanged.
* Unmeasured bounds: the existing `ControlNotDrivable`, unchanged.
* A bool: already refused, unchanged.

## Testing

* Every unit type round-trips through `to_real`/`to_normalized` for a parameter
  of that unit.
* The wrong-unit `TypeError` fires for each pair the catalog actually has.
* `Cents` accepts a parameter spelled `cents`, and `Semitones` one spelled `st`.
* Every existing `real=` test becomes a typed-value test; every `value=` test
  becomes `Normalized`.
* A source-reading test that no example and no docstring mentions `Normalized`
  where a unit type would serve - the "never advertised" rule is exactly the
  kind that rots without one.
* Hardware: one write per unit type, read back after a reconnect.

## Breaks

Every call site. `value=` -> `Normalized(...)`, `real=` -> the unit type,
`text=` -> the bare string. `docs/migration.md` gets a table, and the
mechanical ones are worth showing as a sed-able before/after.

No name survives with a changed meaning, which is the one break shape that
needs shouting about, and this does not have it.

## Open question for review

**Is `Shown` the right name?** It is the type most callers touch, because 2,315
parameters are unitless. Alternatives considered: `Amount` (vague), `Display`
(reads like a verb), `Native` (jargon), `Unitless` (wrong - it is also what a
dB parameter takes when you do not want the check). `Shown` says "the number on
the screen", which is exactly what it is.

## Relationship to issue #13

#13 (OM-M1.5) is the device-layer version: "a knob displaying -6.0 dB reads
-6.0 and I never meet a raw 0..1 scale". It consumes these types rather than
defining its own. Its `KnobParam.value` is one of them, and its "unit as
displayed" criterion is satisfied by the type carrying it.

Two of #13's criteria are NOT served by this and should be read before it
starts: the unmeasured Off detents above, and the fact that `stepNames` is not
guaranteed to be the screen's wording - the metronome beats were two-of-four
wrong and nothing has audited the other 112 lists.

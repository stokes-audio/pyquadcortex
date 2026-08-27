# The catalog is the source of truth for scales

Agreed 2026-08-26. Supersedes the approach in PR #32.

> **Implementation note, added after the work landed.** This document records
> what was AGREED, and three things changed while building it. `units.Span` was
> deleted rather than kept, because once `Parameter` carried the taper there was
> nothing left for it to hold; `Parameter.span` was never added and the cab path
> is `_layout_spec`, returning a `Parameter`. `lane_level_db`, `tempo_bpm` and
> their inverses were KEPT, not deleted - `device/translate/` delegates to them
> and has no catalog to reach. And the floor turned out to need keying by the
> resolved law rather than by the catalog's constant name; see the review
> findings in the PR.


## What we found

`catalog.py` reads 7 attributes per `<Parameter>`. The device publishes 24. Four
of the discarded ones carry facts this project spent days measuring by hand.

### `skew` - the taper

```xml
<Parameter name="LEVEL" units="dB" min="MIN_CABSIM_DB" max="MAX_CABSIM_DB"
           skew="4.9594844" min_string="OFF"/>
```

One law covers the whole catalog:

```
real = min + (max - min) * wire ** (1 / skew)
```

`skew` has three spellings: a number, `LIN_SKEW` (1.0), and `LOG_SKEW` (0.3 -
NOT a logarithmic sweep, despite the name). Two entries are dirty: `" 0.4"`
carries a leading space and two parameters carry `""`.

Confirmed on hardware 2026-08-26, four readings over three unrelated blocks in
two different units:

| block | parameter | skew | wire | predicted | screen |
|---|---|---|---|---|---|
| cab (any) | `MIC n LEVEL` | 4.9594844 | 0.01 / 0.50 / 1.00 | -21.82 / 0.00 / 6.00 dB | -21.8 / 0.0 / 6.0 |
| Low-High Cut | `HPF FREQ` | 0.3 | 0.25 | 216.7 Hz | 217 Hz |
| Low-High Cut | `LPF FREQ` | 0.3 | 0.75 | 7678.3 Hz | 7678 Hz |
| Low-High Cut | `OUTPUT` | none | 0.25 | -10.0 dB | -10.0 dB |
| Envelope Filter | `FREQ` | LOG_SKEW | 0.25 | 197.4 Hz | 197 Hz |
| Envelope Filter | `RESO` | LOG_SKEW | 0.75 | 4.450 | 4.45 |

The two `LOG_SKEW` readings solve independently to exponent 3.3366 and 3.3330,
both `1/0.3`. A true log sweep would have read 316 Hz and 5.62; linear, 2575 and
7.75.

`Parameter.to_normalized` and `to_real` are straight lines today, so **615
parameters convert wrongly** through `real=`.

### Symbolic `min` and `max` - the placeholder concept never existed

Zero parameters are published as `0..1` with a unit. What happens is that `min`
and `max` are sometimes a NAME, and `_as_float` falls back to `0.0` and `1.0`:

| family | count | parameters |
|---|---|---|
| `MIN_EQ_DB` | 16 | the 8 band gains; `steps=241` confirms -12..12 in 0.1 dB |
| `MIN_CABSIM_DB` | 12 | cab `LEVEL`; a PCOM cab spells the same knob `min="-40" max="6"` |
| `MIN_FXLOOP_OUT_GAIN_DB` | 9 | `LEVEL`, `SEND LEV`, `THRU` |
| `MIN_FXLOOP_IN_GAIN_DB` | 6 | `LEVEL`, `RET LEV` |
| `MIN_MIXER_DB` | 8 | `LEVEL A/B`, `LEVEL TO A/B`, `MIXER LEVEL`, `VOLUME` |
| `MIN_EQ_FREQ` | 2 | `FREQUENCY`, skew 0.177 |
| `MIN_INPUT_TRIM` | 1 | `NC_Recorder OUT LEVEL`, `steps=41` |
| `MIN_TEMPO` | 1 | `TEMPO`, `steps=201` confirms 40..240 |

The families are the device's own grouping, and they match the groupings the
measurement campaign arrived at independently.

### `stepNames` - option names ARE in the catalog

`set_param_option`'s docstring says they are not. It is wrong. 539 parameters
carry `stepNames`; 12 are `dynamic="true"` (the list includes upstream blocks,
so it must still be read from the preset) and 527 are fixed. Those 527 use only
113 distinct lists, of which 2 are `Off,On` / `OFF,ON` over 247 parameters.

### `expAssignable` - 14 parameters decline an expression pedal

Never tested. See "Refusals" below.

### Also discarded

`min_string` / `max_string` / `mid_string` (191 parameters read `OFF` at the
bottom), `dynamic`, `showAsInteger`, `tooltip`, `toggleOn` / `toggleOff` /
`toggleStep` (212), `displayPos`, `selfTestValue`, and the device's own
typo `isplayPos`.

## The catalog dump is trustworthy

`research/catalog/ModelRepo.xml` in the lab repo and a fresh fetch from the unit
are both 556,732 bytes. The only difference is the `blob` attribute on 338
models - a same-length token that changes per fetch. All 3,809 parameters are
identical in every attribute.

## Decisions

1. **The catalog wins.** Measurements are evidence, and evidence belongs in
   tests. `MEASURED_SPANS` collapses to the firmware constants the XML does not
   spell out.
2. **One enum per distinct option list**, named after the parameter that uses
   it. `Off,On` parameters take `True` / `False` and get no enum.
3. **Verify the law, not each value.** The law is confirmed at skew 0.3, 4.96
   and 1.0. Individual numbers come from the catalog.
4. **One PR.**

## Design

### `catalog.py`

`Parameter` gains `skew: float = 1.0`, `options: tuple[str, ...] = ()`,
`dynamic: bool = False`, `min_label: str = ""`, `max_label: str = ""`,
`exp_assignable: bool = True`, `show_as_integer: bool = False`.

`to_normalized` / `to_real` apply the law. `range_is_placeholder` and
`_reject_placeholder` are deleted.

A resolver turns symbolic `min` / `max` into numbers from `FIRMWARE_CONSTANTS`.
Unparseable `skew` falls back to 1.0.

Attributes seen but not understood go in `docs/domain-model.md`'s appendix.

### `units.py`

`Span` survives as the value type but is built from the catalog.
`MEASURED_SPANS` (44 entries keyed by `(model_id, index)`) becomes
`FIRMWARE_CONSTANTS` (14 numbers keyed by the device's own constant names),
each carrying its evidence.

Deleted: `lane_level_db`, `db_to_lane_level`, `tempo_bpm`, `bpm_to_tempo`,
`UNCONVERTIBLE`, `EQ_GAIN_SPAN`, `CAB_LEVEL_UNITY`.

Kept: `input_level_db` / `db_to_input_level` (input PORTS are not catalog
models, so nothing replaces these), `UNITY_LEVEL`, `DO_NOT_PROBE`.

`floor_wire` stays measured. `min_label="OFF"` says the bottom of the range
shows a word; only measurement knows where the numbers resume.

### `options.py` (generated)

111 `IntEnum`s, member value = option index. Mangling rules live in the
generator: uppercase; space, `/`, `.` and `-` become `_`; a leading digit gets
`N`; a leading `-` becomes `MINUS_`.

`OPTION_LABELS` maps each enum to its verbatim `stepNames`, so the device's
exact strings are never lost - dynamic lists still match by string.

`SPELLING_FIXES` in the generator holds reviewed corrections to the device's
typos, one line each with a reason. First entry: `Noral` -> `NORMAL`, on 16
`INVERT` parameters.

### Refusals

`Parameter.exp_assignable` is exposed as information. ADR-0010 requires a
differential capture before a refusal, and none of the 14 has been tested.
`Pattern Tremolo` is on the grid and its `STEPS` is one of them, so the capture
runs; the refusal lands only if it confirms, and the disagreement is written
down if it does not.

## Tests

- The campaign's screen readings become a regression table asserted against
  conversions the catalog derives on its own.
- Every symbolic name in the shipped catalog must have a `FIRMWARE_CONSTANTS`
  entry, so a firmware update that adds one fails loudly instead of silently
  becoming `0..1`.
- Parser tests for `" 0.4"`, `""`, `LIN_SKEW`, `LOG_SKEW`.
- The generated `options.py` still matches the catalog.

## Breaks

`to_normalized` returns different numbers for 615 parameters - same name, same
signature. This goes in `migration.md` under its own heading, because a silent
behaviour change is the hardest kind to notice.

Removed names get `DELIBERATE_RENAMES` entries per the existing convention.

## ADR

A new ADR records the principle: the catalog is the source of truth for scales;
measurements are evidence, and evidence lives in tests.

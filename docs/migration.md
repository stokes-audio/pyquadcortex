# Migration

What to change in your own code when a release renames or removes something.

One section per version pair, newest first. Each lists only the things that
BREAK - a new method or a new argument needs no entry here, because nothing you
already wrote stops working. The changelog is the place for what is new; this is
the place for what moved.

While the major number is 0 these sections are expected to exist. The changelog
says why: everything is verified against one unit on one firmware, protocol
facts are still being corrected at a live rate, and the API is deliberately
still moving. A rename that makes the library read correctly is worth doing
while that is true, and this file is the cost of doing it.

---

## 0.40.0 to the next release

### Every setting takes a typed value, not just `set_param`

| before | after |
|---|---|
| `set_input_level(port, 0.5)` | `set_input_level(port, Db(24.0))`, or `Encoded(0.5)` |
| `set_output_level(port, 0.5)` | `set_output_level(port, Encoded(0.5))` |
| `set_input_port(port, level=0.5)` | `set_input_port(port, level=Db(24.0))` |
| `set_output_port(port, level=0.5)` | `set_output_port(port, level=Encoded(0.5))` |
| `set_usb_port(level=0.5)` | `set_usb_port(level=Encoded(0.5))` |
| `set_master_volume(0.3)` | `set_master_volume(Encoded(0.3))` |
| `set_global_eq(1, gain=0.75)` | `set_global_eq(1, gain=Db(6.0))`, or `Encoded(0.75)` |
| `set_global_eq(1, frequency=0.4)` | `set_global_eq(1, frequency=Encoded(0.4))` |
| `set_global_eq_band(i, 0.6)` | `set_global_eq_band(i, Encoded(0.6))` |
| `set_global_eq_output(level=0.5)` | `set_global_eq_output(level=Encoded(0.5))` |
| `set_hold_timing(800)` | `set_hold_timing(Milliseconds(800))` |
| `set_tuner_reference(2.0)` | `set_tuner_reference(Hertz(2.0))` |
| `set_expression(t, p, minimum=0.0, maximum=0.9)` | `set_expression(t, p, minimum=Encoded(0.0), maximum=Encoded(0.9))` |

**Which type, and why it is not always your choice.** Three cases:

- **A known scale**, so the unit type works: an input port's gain
  (-12..+60 dB, from four measured points) and a Global EQ band's gain
  (-12..+12 dB, which is the MANUAL's span on two points and is weaker evidence
  - see `units.SETTING_SPANS`).
- **No known scale**, so `Encoded` is the only thing accepted: output level,
  USB level, master volume, Global EQ frequency/Q/output level, and a Global EQ
  parameter addressed by raw index. A `Db` there raises `ControlNotDrivable`
  naming what would have to be measured - it is not converted against a guess.
- **No wire scale at all**, so `Encoded` is REFUSED: the HOLD threshold takes
  `Milliseconds` and the tuner reference takes `Hertz`, because the wire carries
  the real number rather than a 0..1 position.

**`set_expression` got better, not just stricter.** Its sweep ends are positions
of the parameter being assigned, so they take the same values a write to that
parameter takes:

```python
# before
qc.set_expression(LaneOutput(0), "VOLUME", pedal=1,
                  minimum=0.0, maximum=db_to_lane_level(3.2))
# after - the heel is the Off detent, below the dB scale, so it stays Encoded
qc.set_expression(LaneOutput(0), "VOLUME", pedal=1,
                  minimum=Encoded(0.0), maximum=Db(3.2))
```

**Reading a value back and writing it again needs a wrapper.** Expression
endpoints come off the wire as plain floats, so a restore is
`minimum=Encoded(was.expression_min)`.

**Selectors did not change.** `impedance`, `input_type`, `ground_lift`,
`hp_select`, `dry_wet`, `filter_type`, `mute` and `enabled` are switches and
option lists, not values on a scale, and still take an enum or a bool.

`translate.hz_to_tuner_reference()` now returns `Hertz`, so its result can be
passed straight to `set_tuner_reference()`.

### `set_param` takes one value, and it must say which scale it is on

`value=`, `real=` and `text=` are gone.

| before | after |
|---|---|
| `set_param(t, p, value=0.71)` | `set_param(t, p, Encoded(0.71))` |
| `set_param(t, p, real=-3.1)` | `set_param(t, p, Db(-3.1))`, or `Real(-3.1)` |
| `set_param(t, p, text="/media/x.wav")` | `set_param(t, p, "/media/x.wav")` |
| `set_metronome_volume(real=-20.0)` | `set_metronome_volume(Db(-20.0))` |
| `set_metronome_volume(0.5)` or `(value=0.5)` | `set_metronome_volume(Encoded(0.5))` |

A bare number is refused, and the error shows the call rewritten.

`scene` and `promote` are keyword-only now. Nobody plausibly passed them
positionally past three `None`s, but if you did, name them.

### Reads hand back a typed value, so `str()` changed

Not just writes. `Parameter.to_real()`, `Parameter.floor` and `param_state()`'s
`values` used to give plain floats and now give `Db`, `Real`, `Encoded` and the
rest. They ARE floats - arithmetic, comparisons and `json.dumps` are unchanged -
but `repr` says the type, and `str()` delegates to `repr` on a float subclass:

```python
str(p.to_real(0.5))     # was '-14.0',  is now 'Db(-14.0)'
f"{p.floor}"            # was '-39.5',  is now 'Db(-39.5)'
f"{p.floor:.1f}"        # '-39.5' either way - a format spec is unaffected
```

So a log line or a UI label built with `str()` or a bare `{}` will show the type
name. Wrap it in `float()`, or give the f-string a format spec. This is the one
change here that alters output without raising anything, which is why it has its
own section rather than a table row.

**Read this part before running a find-and-replace.** `real=` and `value=` are
NOT the same number in different clothes. On a lane VOLUME, `-40..+12 dB`:

```python
set_param(LaneOutput(0), "VOLUME", Real(0.0))     # 0 dB - unity
set_param(LaneOutput(0), "VOLUME", Encoded(0.0))  # the Off detent - silence
```

Every knob has two number lines - the screen's and the device's - and the type
says which one your number is on. A mechanical migration is safe as long as
`real=` becomes `Real`/a unit type and `value=` becomes `Encoded`, because that
preserves which line each call was already using. Swapping them silently
inverts a volume.

`Db`, `Percent`, `Hertz`, `Milliseconds`, `Seconds`, `Semitones`, `Cents` and
`Bpm` are `Real` plus a claim that gets checked: hand `Db` to a parameter the
catalog calls Hz and you get a `TypeError` rather than a wrong write. Use plain
`Real` where you do not want the check, or where the parameter has no unit -
1,780 of them do not.

Only `Encoded` works with no device attached. The other two read the parameter's
scale from the catalog, and the catalog comes from the unit.

### DANGEROUS: `MetronomeBeat.OFF` now means the OPPOSITE of what it meant

Read this before the rest. It is the only break here where a name survives, the
type-checker stays quiet, and the meaning inverts.

```python
qc.set_beat(3, MetronomeBeat.OFF)     # before: beat 3 is SILENT
                                      # after:  beat 3 is an ordinary CLICK
```

The four states were named by ear in an earlier session and two were backwards.
Driven properly on 2026-08-27 - one bar at 60 bpm with all four states on the
four beats, listened to and looked at - they are the device's own words:

| index | now | before | sounds like | drawn as |
|---|---|---|---|---|
| 0 | `OFF` | `NORMAL` | the plain click | solid circle |
| 1 | `MUTE` | `OFF` | silent | outlined circle |
| 2 | `DOWN` | `ACCENT` | the big accent | solid circle, dot ABOVE |
| 3 | `ON` | `QUIET` | a small accent | solid circle, dot BELOW |

`OFF` and `ON` are about the ACCENT, not about whether the beat sounds. **To
silence a beat, use `MUTE`.**

`NORMAL`, `ACCENT` and `QUIET` are gone, so code using those fails at import,
which is what you want. Only `OFF` is the trap: it still exists, it moved from
1 to 0, and it flipped from silent to audible. Search for it.

Note `QUIET` was the worst of the four - it named index 3, which is the LOUDER
of the two ordinary states.

### `to_real` and `to_normalized` return DIFFERENT NUMBERS for 615 parameters

Read this one first, because nothing about it is visible at a call site: the
names, the arguments and the types are unchanged, and the answers are better.

The device publishes a `skew` attribute describing each knob's taper. This
library did not read it, so every conversion was a straight line. 615 parameters
are not straight lines. If you have calibrated anything against the old output -
a stored mapping, a fixture, a value you tuned by ear until it sounded right -
recheck it.

```python
# a Low-High Cut's HPF FREQ, catalog range 20..20000 Hz
p.to_real(0.25)     # before: 5015.0    after: 216.7    the unit shows 217
```

The new numbers are the ones the unit displays; see `docs/protocol.md`, "A
parameter's scale is in the catalog".

### An out-of-range value is refused, not clamped

Also silent, and also unchanged at the call site.

```python
p.to_normalized(999.0)     # before: 1.0    after: ValueError
```

Two behaviours were in the library at once - the catalog path clamped and the
measured-span path refused - and unifying them on the catalog meant picking one.
A clamped write looks like it worked and lands somewhere else.

The bottom of the range is the knob's FLOOR, not its minimum, where those
differ. A cab LEVEL's law runs to -40 dB and its quietest real position is -21.8
dB; asking for -30 dB used to convert to wire 0.0005 and mute the microphone.

### Converting real units now needs a catalog

A real value reads the device's own description of the parameter, so it fetches one.
Previously a handful of parameters were served by a hand-measured table and
worked with no device attached - `Tempo()` in particular.

If you convert without a device, use the standalone helpers, which are unchanged:
`protocol.bpm_to_tempo`, `protocol.tempo_bpm`, `protocol.db_to_lane_level`,
`protocol.lane_level_db`, `protocol.input_level_db`, `protocol.db_to_input_level`.

Addressing a parameter by wire INDEX and writing `Encoded` still needs no catalog.

### Removed: the placeholder-range machinery

There is no such thing as a placeholder range, so the things that described one
are gone.

| removed | replacement |
|---|---|
| `catalog.Parameter.range_is_placeholder` | nothing - it was never true of any parameter |
| `units.MEASURED_SPANS` | `units.FIRMWARE_CONSTANTS`, keyed by the device's own constant names |
| `units.UNCONVERTIBLE` | nothing - it was empty, and the case it described does not arise |
| `units.EQ_GAIN_SPAN` | `catalog[4000].parameters[0]` and its `.minimum` / `.maximum` / `.skew` |
| `units.CAB_LEVEL_UNITY` | `catalog[12000].parameters[2].to_normalized(0.0)` |
| `units.Span`, `units.measured_to_wire`, `units.measured_from_wire` | `catalog.Parameter.to_real` / `.to_normalized`, which now apply the taper |

`ValueError` for an unconvertible parameter now says "nobody has measured"
rather than "placeholder range". One parameter reaches it: `NC_Recorder`'s
`OUT LEVEL`, whose block crashes the unit when placed.

### `set_param_option` no longer needs `source=`

The option names are in the catalog. They always were.

```python
# before - a preset was required for every list
p = qc.read_preset(Setlist.USER, "30A")
qc.set_param_option(Block(0, 1), "DYN MODE", "Gate", source=p)

# after - and the choices have names. The block must know its model, which is
# what blocks() gives you; a hand-built Block(row, col) does not.
block = protocol.blocks(p)[0]
qc.set_param_option(block, "DYN MODE", options.DynMode3.GATE)
```

`source=` is still required for a DYNAMIC list, whose entries include one per
block earlier in the chain. Twelve parameters qualify; a side-chain `SOURCE` is
the one to know. Passing a preset anywhere else is harmless and saves a fetch.


### A parameter is addressed by a TARGET, not by a collection-specific method

Ten methods became four. Import the address from
`pyquadcortex.protocol.targets` (or `pyquadcortex.protocol`) and say where the
parameter lives:

| before | after |
|---|---|
| `set_param(row, column, param_index=i, value=v)` | `set_param(Block(row, column), i, Encoded(v))` |
| `set_param(row, column, param="X", model=m, real=r)` | `set_param(Block(row, column, m), "X", Db(r))` |
| `set_lane_output(row, param, value=v)` | `set_param(LaneOutput(row), param, Encoded(v))` |
| `set_input_gate(row, param, value=v)` | `set_param(LaneInput(row), param, Encoded(v))` |
| `set_mixer_param(row, param, value=v)` | `set_param(Mixer(row), param, Encoded(v))` |
| `set_splitter_param(row, param, value=v)` | `set_param(Splitter(row), param, Encoded(v))` |
| `set_tempo_param(param, value=v)` | `set_param(Tempo(), param, Encoded(v))` |
| `set_param_scene_mode(row, column, i, on)` | `set_param_scene_mode(Block(row, column), i, on)` |
| `set_lane_output_scene_mode(row, i, on)` | `set_param_scene_mode(LaneOutput(row), i, on)` |
| `set_expression(row, column, param, ...)` | `set_expression(Block(row, column), param, ...)` |
| `clear_expression(row, column, param)` | `clear_expression(Block(row, column), param)` |

Both changes ship in the same release, so the "after" column shows the FINAL
form - there is no intermediate state to migrate through.

`param_index=` is gone; the parameter is the second positional argument, and
`param=` still works as a keyword. `model=` moves onto the `Block`, because the
model is a property of the cell rather than of the call.

`QuadCortex.TEMPO_PARAMS` is now `targets.Tempo.NAMES`.

### A grid cell is a `Block`

| before | after |
|---|---|
| `set_block(row, column, model)` | `set_block(Block(row, column, model))` |
| `remove_block(row, column)` | `remove_block(Block(row, column))` |
| `move_block(fr, fc, tr, tc)` | `move_block(Block(fr, fc), Block(tr, tc))` |
| `set_bypass(row, column, on)` | `set_bypass(Block(row, column), on)` |
| `set_stomp_assignment(row, column, fs)` | `set_stomp_assignment(Block(row, column), fs)` |
| `clear_stomp_assignment(row, column)` | `clear_stomp_assignment(Block(row, column))` |
| `set_capture(row, column, cap, model=m)` | `set_capture(Block(row, column, m), cap)` |
| `set_ir(row, column, ir, model=m)` | `set_ir(Block(row, column, m), ir)` |
| `set_param_option(row, column, p, o, source)` | `set_param_option(Block(row, column), p, o, source)` |
| `param_options(preset, row, column, i)` | `param_options(preset, Block(row, column), i)` |

`Block.model_id` means **what is, or is to be, in this cell**. So `set_block`
takes it from the cell rather than as a separate argument, and `blocks()`
round-trips: read a block, place it somewhere else.

`Block` is a frozen dataclass rather than a `NamedTuple`, so it no longer
unpacks as a tuple. Attribute access - `block.row`, `block.column`,
`block.model_id` - is unchanged, and that is how every use in this repo read it.

### Submodules moved

Public names are unchanged: `from pyquadcortex.protocol import X` still works
for every one of them. Only direct submodule imports move.

| before | after |
|---|---|
| `client.BlockRefused`, `client.ControlNotDrivable` | `errors.…` |
| `client.UNITY_LEVEL`, `client.lane_level_db`, `client.db_to_lane_level`, `client.input_level_db`, `client.db_to_input_level`, `client.tempo_bpm`, `client.bpm_to_tempo` | `units.…` |
| `client.Block` | `targets.Block` |


### The protocol API moved to `pyquadcortex.protocol`

Change one import line. `pyquadcortex.connect()` now returns the DOMAIN MODEL's
`Device`; the message-level client is one namespace deeper.

| before | after |
|---|---|
| `from pyquadcortex import X` | `from pyquadcortex.protocol import X` |
| `pyquadcortex.connect()` | `pyquadcortex.protocol.connect()` |
| `pyquadcortex.proto` | `pyquadcortex.protocol.proto` |
| `pyquadcortex.client` | `pyquadcortex.protocol.client` |
| `pyquadcortex.enums` | `pyquadcortex.protocol.enums` |
| `pyquadcortex.session` | `pyquadcortex.protocol.session` |

Every name the package exported is reachable under `pyquadcortex.protocol` with
the same behaviour, apart from the one rename below. `tests/test_namespace.py`
enumerates the pre-flip export list and proves it, so this table cannot quietly
fall out of date.

`qcctl` is unchanged. If you installed the package in editable mode before the
move, reinstall it so the console script points at the new module path.

### `ExpressionBypassMode` is now `ExpressionSwitchMode`

| before | after |
|---|---|
| `ExpressionBypassMode` | `ExpressionSwitchMode` |

Same values, same numbering, same meaning - `STOP` 0, `SWITCH` 1, `HEEL_TOE` 2.
No alias: the old name described one of the three things the enum governs. It is
the unit's **SWITCH ON** control, and it applies to a block's bypass *and* to a
Lane Output Control's MUTE and SOLO. Only the bypass is a bypass.

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

### `to_real` and `to_normalized` return DIFFERENT NUMBERS for 617 parameters

Read this one first, because nothing about it is visible at a call site: the
names, the arguments and the types are unchanged, and the answers are better.

The device publishes a `skew` attribute describing each knob's taper. This
library did not read it, so every conversion was a straight line. 617 parameters
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

`real=` reads the device's own description of the parameter, so it fetches one.
Previously a handful of parameters were served by a hand-measured table and
worked with no device attached - `Tempo()` in particular.

If you convert without a device, use the standalone helpers, which are unchanged:
`protocol.bpm_to_tempo`, `protocol.tempo_bpm`, `protocol.db_to_lane_level`,
`protocol.lane_level_db`, `protocol.input_level_db`, `protocol.db_to_input_level`.

Addressing a parameter by wire INDEX and writing `value=` still needs no catalog.

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

# after - and the choices have names
qc.set_param_option(Block(0, 1), "DYN MODE", options.DynMode3.GATE)
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
| `set_param(row, column, param_index=i, value=v)` | `set_param(Block(row, column), i, value=v)` |
| `set_param(row, column, param="X", model=m, real=r)` | `set_param(Block(row, column, m), "X", real=r)` |
| `set_lane_output(row, param, value=v)` | `set_param(LaneOutput(row), param, value=v)` |
| `set_input_gate(row, param, value=v)` | `set_param(LaneInput(row), param, value=v)` |
| `set_mixer_param(row, param, value=v)` | `set_param(Mixer(row), param, value=v)` |
| `set_splitter_param(row, param, value=v)` | `set_param(Splitter(row), param, value=v)` |
| `set_tempo_param(param, value=v)` | `set_param(Tempo(), param, value=v)` |
| `set_param_scene_mode(row, column, i, on)` | `set_param_scene_mode(Block(row, column), i, on)` |
| `set_lane_output_scene_mode(row, i, on)` | `set_param_scene_mode(LaneOutput(row), i, on)` |
| `set_expression(row, column, param, ...)` | `set_expression(Block(row, column), param, ...)` |
| `clear_expression(row, column, param)` | `clear_expression(Block(row, column), param)` |

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

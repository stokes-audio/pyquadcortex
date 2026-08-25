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

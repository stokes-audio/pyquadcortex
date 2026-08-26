# changelog

What changed between released versions, from the point of view of someone
installing the package. The git history has the detail and the reasoning; this
file answers the narrower question "I upgraded, what is different for me?".

Versions follow the usual 0.x convention: the minor number moves for new
capability and the patch number for fixes. While the major number is 0, a
breaking change moves the **minor** too - it does not move the major, because
that number is reserved for the 1.0.0 conditions below. Anything may still
change while the major number is 0.

That is a deliberate signal, not neglect. Everything here is verified against ONE
unit on one firmware, protocol facts are still being corrected at a live rate, and
the roadmap plans a reshape of the public API (the domain model in
`docs/roadmap.md`). 1.0.0 happens when all three stop being true: the domain model
has landed or been deliberately dropped, the library has been verified on a second
unit or firmware, and the protocol record has gone a sustained stretch without a
correction.

## Unreleased

### `real=` speaks dB for the EQ bands, the mixer and the splitter

Four more families of parameter now take a value in the units the screen shows,
because their spans were measured rather than inherited:

```python
qc.set_param(Block(0, 1, models.Equalizer.PARAMETRIC_8), "1 GAIN", real=6.0)
qc.set_param(Mixer(0), params.MixerParam.MIXER_LEVEL, real=-6.0)
qc.set_param(Splitter(0), params.SplitterParam.LEVEL_TO_B, real=-3.1)
```

- **Block EQ band gains** are `dB = -12 + 24 * wire`. Measured on the
  Parametric-8 at four points including both ends, and separately on the
  Parametric-3 and Output Equalizer, which are different catalog entries and so
  were not assumed to inherit it.
- **The mixer and splitter levels** are `dB = -40 + 52 * wire`, all five of them
  - `LEVEL A`, `LEVEL B`, `MIXER LEVEL`, `LEVEL TO A`, `LEVEL TO B`. The library
  has claimed this for several releases on the strength of a measurement of the
  lane VOLUME; it is now measured on those controls themselves, and the claim
  held.

**Wire 0.0 is an OFF detent, not -40 dB.** The splitter's `LEVEL TO A` displays
"OFF" there, exactly as the lane VOLUME does - so that belongs to the whole level
family. For silence write `value=0.0`.

Everything else still refuses `real=`, which is the honest answer for a span
nobody has measured. Two things worth knowing before relying on EQ gain: a band's
TYPE decides whether GAIN does anything at all (Lo Pass and Hi Pass disable it,
and a gain written there is stored and ignored), and `N BYPASS = 1` means the
band is **ON** - a disabled band displays `0.0 dB` whatever it stores.


### BREAKING: one `set_param` for everything, addressed by a target

Six ways to set a parameter became one. Say WHERE it lives:

```python
from pyquadcortex.protocol import Block, LaneInput, LaneOutput, Mixer, Splitter, Tempo

qc.set_param(Block(0, 2, model_id), "GAIN", real=-6.0)
qc.set_param(LaneOutput(0), "VOLUME", real=-3.1)
qc.set_param(LaneInput(0), "INPUT GAIN", real=12.0)
qc.set_param(Mixer(0), "LEVEL A", value=UNITY_LEVEL)
qc.set_param(Splitter(0), "LEVEL TO B", value=0.25)
qc.set_param(Tempo(), "TEMPO", real=120)
```

`set_lane_output`, `set_input_gate`, `set_mixer_param`, `set_splitter_param`,
`set_tempo_param` and `set_lane_output_scene_mode` are gone, and so are
`set_lane_output_expression` / `clear_lane_output_expression`, which existed
only in this same unreleased window. Every grid operation now names its cell
with a `Block`. **[docs/migration.md](docs/migration.md) has the full before /
after table.**

`blocks()` already returned `Block(row, column, model_id)`, so what you read is
now what you write to:

```python
for block in protocol.blocks(preset):
    qc.set_param(block, "GAIN", real=-6.0)      # model_id is already on it
```

### Parameter names are constants now, not string literals

`pyquadcortex.protocol.params` is generated from the device catalog, as
`models.py` already was, with one `IntEnum` per model:

```python
from pyquadcortex.protocol import params

qc.set_param(LaneOutput(0), params.LaneOutputParam.VOLUME, real=-3.1)
qc.set_param(Tempo(), params.TempoParam.TEMPO, real=120)
qc.set_param(LaneInput(0), params.LaneInputParam.INPUT_GAIN, real=12.0)
```

A member IS its wire index, so passing one **skips the catalog fetch a name
needs** - the typed route is also the cheapest. Names still work; nothing is
forced.

Two things the catalog could not have told you, both measured:

- **A cab's repeated parameters are two MICROPHONES**, not IR slots or channels,
  so they are `MIC_1_DISTANCE` / `MIC_2_DISTANCE`. Confirmed against the unit's
  own editor: mic 1 showed POSITION 2.9 / DIST 3.0 against wire 0.29 / 0.30, and
  mic 2 showed 5.6 / 3.3 against 0.56 / 0.33.
- **The catalog UNDER-DESCRIBES cabs** - it lists 2 parameters where the wire
  carries 22. All 140 cab models share the one `Default Cabsim` layout, measured
  across Bass/Guitar and mono/stereo, so a cab is chosen by its `models.*` id and
  driven through `params.Cabsim`:

  ```python
  qc.set_block(Block(0, 5, models.CabsimBassM.N212_DARKGLASS_NEO))
  qc.set_param(Block(0, 5), params.Cabsim.MIC_1_DISTANCE, real=3.0)
  ```

An IR Loader's repeated block genuinely IS two IR slots, so those read `IR_1_PATH`
/ `IR_2_PATH` - and they agree with the `IR_PATH_PARAMS` that `set_ir(slot=)` was
already using. Where a name repeats, BOTH occurrences are numbered: an unnumbered
first member would read like the real one and hide that it is one of a pair.

A hardware test regenerates from the connected unit and fails if the committed
file has drifted, since a generated-and-committed file is otherwise its own
yardstick.

### An expression pedal reaches every parameter, not just a block's

`set_expression(target, param, ...)` and `clear_expression(target, param)` work
against any target. Measured on hardware one write at a time: blocks, the input
gate, the mixer, the splitter and the lane output all accept an assignment, on
float **and** `switch`-typed parameters. So a pedal can now drive a noise gate's
INPUT GAIN or a mixer's LEVEL A, neither of which had ever been tried.

Parameter TYPE turned out to be irrelevant - the manual gives every assignable
parameter a MIN/MAX sweep, and a block's BYPASS is the separate feature
`set_expression_bypass` drives.

**Two parameters still refuse**, and they are the only refusal in the library: a
Lane Output Control's MUTE and SOLO raise `ControlNotDrivable`.

### Also

- **`ControlNotDrivable`, `BlockRefused`** now live in `protocol.errors`, the
  unit converters and `UNITY_LEVEL` in `protocol.units`, and the targets in
  `protocol.targets`. Public import paths are unchanged -
  `from pyquadcortex.protocol import X` still works for all of them.
- **`Block` is a frozen dataclass**, not a `NamedTuple`, so it no longer unpacks
  as a tuple. Attribute access is unchanged.
- **`QuadCortex.TEMPO_PARAMS`** is now `targets.Tempo.NAMES`.
- **`set_param` no longer defaults `value` to 0.0.** A call that names no value
  raises instead of silently writing zero.

### BREAKING: the protocol API moved to `pyquadcortex.protocol`

> Every breaking change in this release is listed side by side in
> [docs/migration.md](docs/migration.md), if a table is what you want.

**Change one import line.** `from pyquadcortex import X` becomes
`from pyquadcortex.protocol import X`, and `pyquadcortex.connect()` becomes
`protocol.connect()`:

```python
from pyquadcortex import protocol

with protocol.connect() as qc:      # was: pyquadcortex.connect()
    qc.switch_scene(1)
```

That is the whole migration for the names the package exported. Every one of them
is reachable under `pyquadcortex.protocol`, with the same behaviour - same
classes, same methods, same arguments, same results. Nothing about the protocol
API changed except where it is imported from. A test enumerates the old export
list and proves it.

One name has since been renamed on purpose, in this same unreleased window:
`ExpressionBypassMode` is now `ExpressionSwitchMode` (below). That is a separate
break from the move, and the test records it as a deliberate rename rather than
letting the name quietly vanish.

**Submodule paths took the same step**, and no test can prove that part for you
because those were never top-level exports. If you import a submodule directly,
add `protocol.` to it:

| before | after |
|---|---|
| `pyquadcortex.proto` | `pyquadcortex.protocol.proto` |
| `pyquadcortex.client` | `pyquadcortex.protocol.client` |
| `pyquadcortex.enums` | `pyquadcortex.protocol.enums` |
| `pyquadcortex.session` | `pyquadcortex.protocol.session` |

`pyquadcortex.proto` is the one to check for: decoding a capture with the shipped
protobuf bindings is the documented way to do it, and the line in
`docs/capture.md` used to read `from pyquadcortex.proto import
ProductionAutomation_pb2 as pa`.

`qcctl` is unchanged. If you installed the package in editable mode before this
change, reinstall it so the console script points at the new module path.

### BREAKING: `ExpressionBypassMode` is now `ExpressionSwitchMode`

Rename the import; nothing else changes. Same values, same numbering, same
meaning:

```python
from pyquadcortex.protocol import ExpressionSwitchMode   # was ExpressionBypassMode
```

There is deliberately **no alias**. The old name described one of the three
things this enum governs. It is the unit's **SWITCH ON** control, and it applies
to a block's bypass *and* to a Lane Output Control's MUTE and SOLO, which store
their settings in the same `expression_bypass_info`. Only the bypass is a bypass.

While renaming it, two behaviours of the unit got written down that were not
recorded before: the mode decides which of the other controls exist, and the two
are mutually exclusive in the modes measured. `SWITCH` greys out SWITCH DELAY;
`HEEL_TOE` greys out LATCH EMULATION. The library still lets you send either, so
a combination the touchscreen cannot produce is reachable from the host and has
never been tested - worth knowing before you rely on one.

### An expression pedal can be assigned to a Lane Output Control

- **`set_lane_output_expression(row, param, pedal, minimum, maximum)`** assigns a
  pedal to a lane's VOLUME or PAN, and **`clear_lane_output_expression(row,
  param)`** unassigns it. `set_expression` never could: the Lane Output Control
  has no column, which is the same reason `set_param` cannot reach it and
  `set_lane_output` exists.

  A pedal used as a volume and mute control, silent at the heel:

  ```python
  qc.set_lane_output_expression(row=0, param="VOLUME", pedal=1,
                                minimum=0.0, maximum=db_to_lane_level(3.2))
  ```

  The sweep ends are the normalized 0..1 the wire carries, which the unit
  displays as a percentage - 0.830769 shows as 83.08%.

- **`clear_expression(row, column, param)`** does the same for a block parameter.
  `set_expression` has never had a counterpart.

- **MUTE and SOLO refuse, and that is the device's doing.** They are the ONLY
  two parameters in the library a host cannot assign a pedal to. Measured with
  four message shapes, including the byte-identical message VOLUME accepts in
  the same session, plus a `Grid` DELETE - none landed, in either direction.
  The touchscreen writes the very same field, so the control is understood and
  not drivable, and these methods raise rather than failing quietly the way the
  device does (ADR-0007). Assign it on the unit; the library reads it back.

  It is a **measured list, not a rule**, and three tempting rules are false:
  switch-typed parameters are not refused (the Jewel's HIGH CUT, the Mixer's
  PHASE and the Splitter's TYPE all take one), bypass-like parameters are not
  refused (the input gate's BYPASS takes one, and takes a clear), and
  `output_control` does not reject `expression` in general (VOLUME and PAN, in
  the same block, take one).

- **Expression assignment is confirmed on every other collection.** Blocks, the
  input gate, the mixer and the splitter all accept one, on both float and
  switch parameters - so a pedal can now drive a noise gate's INPUT GAIN or a
  mixer's LEVEL A, neither of which had ever been tried. The coverage table
  records what was measured.

- **`scene_mode` is not sent.** An early probe carried it and worked, which made
  it look required. Assigning on the touchscreen settled it: the unit leaves the
  flag alone, and the manual excludes an expression-assigned parameter from Scene
  data anyway.

### `set_lane_output(real=)` now speaks dB for VOLUME

```python
qc.set_lane_output(row=0, param="VOLUME", real=-3.1)     # was: raises
```

The lane VOLUME publishes the placeholder range `0..1 "dB"`, so `real=` used to
refuse it. Its TRUE span is measured at both ends - -40..+12 dB, unity at 10/13 -
so the conversion now goes through that instead of through the catalog.

It is **the only** placeholder parameter that converts. The other 51 across 23
models - EQ band gains, cab levels, send/return and FX loop levels, mixer and
splitter levels - still refuse, because their spans have never been measured and
they are demonstrably not all the same scale. Recovering them is tracked
separately.

**Why now.** `import pyquadcortex` should hand you the Quad Cortex, not the wire.
The model of the unit is being built, and it takes the top-level name; the protocol
layer keeps everything it had, one import deeper. This library is deliberately 0.x
with roughly no users, so the break is as cheap today as it will ever be. The
decision is ADR-0006.

### `pyquadcortex.connect()` now returns a `Device`

The model's front door. Today it tells you what you are connected to and not much
else:

```python
import pyquadcortex

with pyquadcortex.connect() as device:
    print(device.firmware, device.serial)
```

Presets, scenes, the grid and the rest are being added story by story - see
[docs/domain-model.md](https://github.com/stokes-audio/pyquadcortex/blob/main/docs/domain-model.md)
for where it is going. Nothing is stubbed out to look finished, so if it is not
there yet, use the protocol layer.

To use both layers in one script, wrap a connection you already have with
`Device.from_client(qc)`. It does not take ownership: closing the `Device` leaves
your connection open.

### The model keeps up with the unit on its own

Anything a `Device` tells you is what the unit is doing now, including changes you
make on its touchscreen while your script is running. You do not have to re-read
anything, and nothing you read comes with a "this might be out of date" warning.

It works because the unit says when things change, and the model listens from the
moment it connects. Connecting is also when the unit volunteers most of what it
knows, in one burst, so the model usually has your answer before you ask for it.
Where the unit says nothing - its firmware version, for one - the model asks, once,
the first time you want it.

```python
import pyquadcortex

with pyquadcortex.connect() as device:
    print(device.firmware)      # asks the unit
    print(device.firmware)      # free
```

Two things it will not do. It will not hand you a value the unit never sent: a
field the unit left out raises rather than coming back as an empty string, and
asking again can still succeed. And it will not answer at all once you close the
`Device` - what it remembers stopped being true of the unit the moment the
connection went away.

If the unit mentions something the model does not yet understand, the model stops
trusting that part of what it remembers and asks the unit next time you read it.
Slower, and right. `device.state` shows you what it currently holds and what it is
about to re-read.

Presets, the grid and the Directory are not in the cache yet - they arrive with the
surfaces that read them. Nor is reconnecting after the unit sleeps or the cable
comes out; that is still your code's job for now.

### New: listen to everything the unit sends

The unit talks without being asked. Turn a knob on its touchscreen, recall a
preset, let the metronome run, and it pushes messages about it. Until now those
messages were only reachable if you happened to be waiting for that exact one,
and anything else was dropped. `add_listener` hands you all of them:

```python
from pyquadcortex import protocol

def watch(message):
    print(type(message).__name__)

with protocol.connect() as qc:
    stop = qc.add_listener(watch)
    ...
    stop()                     # or qc.remove_listener(watch)
```

Your function is called for every message, and it takes nothing away from the
rest of the library: a call that was waiting for a reply still gets it.

Two rules, because your function runs on the thread that reads from the USB
device:

- **Do not block in it.** Whatever it does delays the next message being read.
- **Do not read from the device in it.** That thread is the one that would have to
  deliver the answer, so the call could never be answered - and the connection
  would stall behind it for as long as it waited. Rather than let that happen, the
  library raises `RuntimeError` if you try. Note what you need and read it from
  your own thread.
- **Treat the message as read-only.** It is the same object the rest of the
  library sees, not a copy.

To hear the burst of state the unit sends when a client connects - nearly
everything it knows, including the preset currently loaded - register before the
handshake, because it arrives seconds after `connect()` returns:

```python
with protocol.connect(before_handshake=lambda t: t.add_listener(watch)) as qc:
    ...
```

The decision behind the two rules is ADR-0009. This is the groundwork for the
model keeping itself current without asking twice.

### Groundwork: the model will talk in the numbers on your screen

Rows will be 1 to 4, slots 1 to 8, scenes and footswitches letters, levels the dB
the unit displays, and the tempo the bpm it displays. The wire counts from zero
and stores raw scales, and the model now converts in exactly one place so nothing
else has to remember to.

**Nothing that reads a row or a level exists yet** - the preset and grid surfaces
are still being built - so what you can use today is the three value types this
groundwork brought with it, exported from `pyquadcortex`:

```python
from pyquadcortex import PresetAddress, FootswitchLetter, SceneLetter

PresetAddress.parse("28C")          # bank 28, position C
PresetAddress.parse("28X")          # ValueError, here rather than at write time
FootswitchLetter.E                  # a footswitch is a letter, never a number
```

The footswitch rule is worth the sentence it costs. A footswitch index and a
block's column are different numbers that agree most of the time, which is how a
bug hid for months: a block in column 3 assigned to footswitch E is stored under
key 4. No model API takes a bare footswitch number, so a column cannot be passed
where a footswitch belongs.

Until the Directory arrives, `PresetAddress` is most useful for checking an
address before you hand it to the protocol layer.

### Withdrawn: the Tempo menu's MODE is "not on the wire"

The 0.23.0 entry below records, under **Settled**, that the Tempo menu's MODE
(global vs per-preset) is not on the wire. **That claim is withdrawn.** It was
carried in the documentation from 0.33.0 through 0.40.0 and is wider than the
evidence behind it.

What was actually measured is that MODE is never BROADCAST. Three independent
tests watched for a broadcast when the switch was changed and saw nothing, and
the instrument in the later two is worth trusting - 70 of the device's 72 message
types decoded, with a liveness heartbeat proving the link was up. But all three
listened, and none of them asked. **A control the device never announces may
still answer a READ.** Nothing has tried one.

So MODE is an open protocol investigation rather than a settled dead end. **It was
asked, and it answered** - see the next entry.

### The Tempo menu's MODE switch is readable and writable

`qc.tempo_mode()` returns a `TempoMode` - `PRESET` or `GLOBAL` - and
`qc.set_tempo_mode(TempoMode.GLOBAL)` moves the switch. It is the DEVICE tempo
block's parameter 1, carried in `GlobalTempo.params`.

**This is a global setting**, despite riding a tempo message. It affects every
preset and there is nothing to save afterwards, so read it first if you intend to
put it back. It does not move either tempo block: the unit keeps the preset's
settings and the device's at the same time, and MODE only picks which one plays.

The entry above withdrew the claim that this control was not on the wire. It was
on the wire the whole time - though not via a naive READ, see the caveat below. The three tests that found nothing were measuring
something real and narrower - the unit emits no CHANGE EVENT when the switch moves -
and the mistake was reading that as "cannot be asked". The current value in fact
rides the tempo stream the unit sends anyway. Confirmed on the wire, on the unit's
own screen, and by the tempo actually in effect, which switched between the two
blocks' stored values.

The method that found it - capture the whole readable state in each position
and diff, rather than looking for the field you expect - is now what ADR-0010
requires before any control is written down as having no wire path.

Watch out for one thing if you read `GlobalTempo` yourself: it alternates two
message shapes, one carrying the running clock and one carrying the 25
parameters. Wait for a reply that actually has parameters. Taking the first
`GlobalTempo` to arrive is what produced the original dead end.

### `TEMPO` takes bpm: the span is 40 to 240

`set_tempo_param("TEMPO", real=120)` now works, and `tempo_bpm()` /
`bpm_to_tempo()` convert if you want the numbers directly. Previously `real=`
was refused here, because the catalog publishes a placeholder range for this
parameter and converting against it gives a number that means something else.

The span was measured off the screen instead: 59 bpm at `0.095`, 111 at `0.355`,
120 at `0.400`, each exact to the displayed integer. The endpoints are the fit's
rather than driven, and they land on the 40-240 range the unit's manual
documents.

### Regenerating the protobuf bindings can no longer walk the pin backwards

Nothing you install changes. This is about the generated bindings that ship in
the wheel, and it matters to anyone who regenerates them.

`grpcio-tools` carries its own copy of protoc, so whichever version is installed
decides the gencode written into the bindings. The dev extra's floor was
`>=1.68`, low enough that `pip install -e ".[dev]"` could resolve to a generator
emitting gencode 7.35.0 against bindings committed at 7.35.1 - and lower still
through a venv that picked up `grpcio-tools` some other way, or the script's
fallback to a system `protoc`, which no floor constrains. So regenerating could
silently downgrade them. Nothing caught it: the protobuf runtime only checks
`runtime >= gencode`, so older bindings import cleanly and pass the whole suite
while `pyproject.toml`'s pin no longer describes them.

The floor is now `grpcio-tools>=1.83.0`, the oldest release whose protoc emits
gencode 7.35.1, and it moves in the same commit as any gencode bump.
`scripts/compile_protos.sh` refuses to install bindings older than the committed
ones and leaves the tree untouched when it does; `tests/test_packaging.py`
checks on every PR that the committed gencode and the pin floor are the same
number. The bindings themselves are unchanged - regenerating is its own change
with its own pin bump (ADR-0001, ADR-0008).

## 0.40.0 - 2026-08-10

### The lane/mixer level span is -40..+12 dB, not -100..+30

A field report with three simultaneous screen-and-wire readings settled it:
`dB = -40 + 52 * value`, with 0 dB still at `UNITY_LEVEL`. The two releases that said
-100..+30 dB were fooled by arithmetic: both spans put 0 dB at exactly 10/13
(100/130 = 40/52), and unity was the only point the original measurement had, so no
amount of re-measuring unity could have caught it. A span needs a point away from the
reference.

**New: `lane_level_db(value)` and `db_to_lane_level(db)`**, mirroring the input-gain
pair, since the catalog publishes a placeholder range for these parameters and `real=`
raises - callers were doing the arithmetic by hand from a docstring that was wrong.

Also from the same report: the knob's lowest numeric step is **-39.5 dB**, below which
the unit shows "Off" - wire `0.0`. So `0.0` is an Off position, not -40 dB; for
silence write `0.0` rather than converting the bottom of the dB scale.

## 0.39.0 - 2026-08-08

### Master Volume is writable after all

**New: `set_master_volume(volume)`**, normalized 0..1. This reverses a claim that
shipped for several releases: `master_volume()` was documented read-only, and
`set_master_volume` deliberately did not exist, on the strength of a hardware
measurement that a write was accepted and ignored.

That measurement was wrong, and wrong in a way worth knowing about: **a read
straight after a write returns the PREVIOUS value.** Write-then-read therefore
reports every result one step late, and a write that lands looks exactly like a
write that was refused. Reconnect, or wait, before believing a read-back of
anything.

The write is a real level change, not a display change - a host write of 0.30 took
the unit's overlay to 30 and audibly dropped the output. Afterwards the physical
knob soft-takes-over: it does nothing until turned past the value that was set,
then resumes control, which is exactly what the manual describes Cortex Control
doing to the hardware wheel. Master Volume is its own gain stage downstream of the
stored port levels, so writing it changes no `IOSettings` level.

Values outside 0..1 raise `ValueError` and are never sent. The wire is 0..1 while
the unit shows 0-100, so `set_master_volume(30)` is the mistake to expect, and
this is a control that feeds an amplifier.

Never send `calibrate` alongside a level. It is an action, not a flag: it opens
the full-screen Master Volume Calibration dialog and waits for a human to sweep
the knob. `set_master_volume()` never sends it, and a test enforces that.

### A hardware-in-the-loop test suite

`pytest tests/hardware --hardware` runs against a connected unit. Without the flag
nothing in `tests/hardware/` is collected at all - not skipped, not collected - so
the offline suite stays honest with no unit attached.

A successful run leaves the unit exactly as it found it, and a failed run restores
what it can and names what it could not. Quit Cortex Control first; it holds the
USB interface exclusively. See `tests/hardware/readme.md`, and ADR-0005.

The first thing it measures is a quantity that was already known, as a control. An
earlier version of the same file reported 2-11 ms for five write types, which
looked like a discovery and was really a predicate matching the wrong message.

### Stomp momentary: the rule nobody knew about

`set_stomp_momentary()` already existed and its wire shape was right, but its
documentation was thin enough to mislead. Two corrections, both from hardware:

- The map is keyed by **footswitch index, not column**. Every earlier sample had
  the two equal, so this had been assumed rather than shown. A block at column 3
  assigned to footswitch E settles it: the key is `4`.
- **The write only lands on a footswitch driving exactly one block.** Aim it at a
  switch with two or more and the device accepts it, echoes nothing, and reads
  back unchanged. The unit greys out its own Latching/Momentary toggle in the same
  case, so this is a device rule rather than a transport wart. Check with
  `stomp_assignments()` first; there is no error to catch.

No API change to this method. If you were writing momentary to a multi-block
footswitch it was never taking effect, and now the docs say why.

Whether the control exists at all had been in doubt: manual 4.0.0 mentions
"momentary" only for the expression toe switch and Looper X. The touchscreen's
**Assign footswitch** modal carries the toggle, so the manual is simply behind.

## 0.38.0 - 2026-08-06

Every beat of the metronome's bar is separately controllable, and now reachable.

### Per-beat accents

The Tempo page lets you set each beat of the bar independently. Those cells are tempo
parameters 10 to 22 - the catalogue's `STEPSTATE0` to `STEPSTATE12`, beats 1 to 13 - and
each is a four-option list:

```python
from pyquadcortex import MetronomeBeat, beats

qc.set_time_signature(TimeSignature.FOUR_FOUR)   # FIRST - this rewrites the beats
qc.set_beat(1, MetronomeBeat.ACCENT)             # emphasize the downbeat
qc.set_beat(3, MetronomeBeat.OFF)                # skip beat 3
qc.set_beats([MetronomeBeat.ACCENT, MetronomeBeat.NORMAL,
              MetronomeBeat.OFF, MetronomeBeat.QUIET])

beats(qc.read_current_preset())    # {1: ACCENT, 2: NORMAL, 3: OFF, 4: QUIET, ...}
```

`MetronomeBeat` is `NORMAL`, `OFF`, `ACCENT`, `QUIET`, numbered in the order a cell cycles
when touched - which is NOT a loudness order, so do not read meaning into the sequence.
Wire values are `option / 3`.

**Set the time signature before the beats.** Changing it rewrites them, because the device
re-lays the accent pattern out for the new bar.

`beats()` always reports 13 entries whatever the signature, because the unit always stores
13. Beats past the end of the bar are kept and simply not sounded.

### Two fixes this uncovered

`Parameter.option_count` now counts `empty`-typed parameters that publish a step count.
The per-beat cells are typed `empty` with `steps=4`, so they were unreachable through
`set_tempo_option` despite the count sitting in the catalogue. Safe to widen because the
catalogue is small enough to check exhaustively: 16 parameters are typed `empty` - these 13
and three `DUMMY` entries with no steps - so requiring steps admits exactly the beats.

**Two tests had been silently skipping for several releases.** The test fixture's
`TempoControl` stopped at parameter 3, and two tests guarded themselves with
`pytest.skip` when the fixture "did not go that far" - including the one asserting that
all four typed metronome setters send the right index and value. The fixture now carries
all 23 parameters and those guards are assertions instead, so a regression fails rather
than disappears.

### Corrections

- Two releases claimed tempo indices 8 and 9 were **absent from the device catalogue**.
  They are not: `SOUND` (steps=6) and `ROUTING` (steps=5) are described at exactly those
  indices. Only NAMES ever disagreed with the catalogue, never coverage. Index 23 is the
  one genuinely undescribed parameter, and it is **not** a 14th beat.
- `docs/api.md` showed `set_metronome_volume(0.0)` commented as "silence its metronome
  (there is no mute flag)". Both halves were wrong: `0.0` is -60 dB and still audible, and
  the mute is parameter 4. It now shows `set_metronome_muted(True)`.

### Verification, plainly

**Traced on the wire, with a person touching the unit:** the beat-to-index mapping, all
four wire values, the cycle order, and that a cell wraps after four touches. The captured
sequence is one touch on beat 3, three on beat 4, four on beat 1, from a 4/4 baseline.

**From the device's own catalogue:** `steps=4`, the `STEPSTATE0`-`STEPSTATE12` names, the
`empty` type, and that 16 parameters carry that type across the whole catalogue.

**From the operator's eyes and ears:** which state is louder. Corroborated by a
factory-default 4/4 carrying `ACCENT` on beat 1 and nothing else, but the naming of
`ACCENT` versus `QUIET` rests on a human report, not a measurement.

**Reasoned, not measured:** that 13 cells exist because 13/4 is the largest signature. The
13 cells are real and catalogued; tying the count to that signature is inference. How many
beats a COMPOUND signature sounds - whether 6/8 draws six cells or two - is untested, which
is why `set_beat` range-checks against 13 rather than the current signature.

## 0.37.0 - 2026-07-31

The metronome mute, traced on hardware rather than inferred - which closed the question
0.36.0 answered only half of.

### The unit's MUTE and tempo parameter 4 are the same control

0.36.0 corrected parameter 4's polarity and renamed it `START`/`PLAYBACK`, but left a
question open: the unit's Tempo page has a MUTE control and nothing in the library mapped to
it, so a real mute was presumed missing. **It was not missing - it is parameter 4.** Captured
with a person pressing the unit's own MUTE button:

```
MUTE on   ->  tempoProgramData{params{index: 4, value: 0}}   + Looper X param 21 -> 0
MUTE off  ->  tempoProgramData{params{index: 4, value: 1}}   + Looper X param 21 -> 1
```

One control, three names: **MUTE** on the unit, `START` in the device catalogue, PLAYBACK in
the manual - and it is INVERTED against the label a player sees, `1.0` being audible. The
unit has no start/stop control at all; the transport always runs and muting is how a player
silences it.

### Added

- **`set_metronome_muted(bool)`** - speaks in the label the unit shows, so
  `set_metronome_muted(True)` silences the click. The exact inverse of
  `set_metronome_running()`; they are the same parameter and cannot disagree.

### Changed

- `set_tempo_param("MUTE")`'s refusal message was itself wrong: it claimed parameter 4 is
  "not a mute", which the hardware disproves. It now says what is true - the unit does call
  it MUTE, it is inverted, so honouring the name would unmute what you asked to mute.
- **Lane Output `MUTE` polarity measured**: `1.0` = muted, the intuitive direction, by ear.
  Which makes explicit the worst naming trap here - **two parameters called MUTE with
  OPPOSITE polarities**, the lane's and the tempo block's. Documented as such in
  `set_lane_output`, the protocol notes and the ears-only table. Lane `SOLO` remains
  unmeasured and is now labelled as such rather than left to look verified.
- Recorded: a lane mute does NOT silence the metronome, which has its own `ROUTING`
  (parameter 9) and bypasses lane outputs. And the preset's 24th tempo parameter (index 23,
  absent from the catalogue) stayed untouched through every Tempo-page control traced, so it
  is not the mute - what it is remains unknown.

### Verification, plainly

Wire-traced this session: the mute's polarity in both directions, the Looper X mirror moving
in lockstep in the same burst, and the absence of any start/stop control. Measured by ear:
lane `MUTE` = 1.0 mutes, and the metronome surviving a lane mute. Reasoned from the
catalogue only: that index 23 exists and is undescribed.

## 0.36.1 - 2026-07-31

Documentation only, from a listening session at the unit. No code changes.

### A preset recall interrupts the audio - every recall, including a redundant one

Chased with a person playing through the rig, after an unexplained one-second dropout in an
earlier session. Seven candidate causes were eliminated by ear - block parameter writes,
tempo parameter writes, bypass toggles, the metronome transport on and off, a session
disconnect, a reconnect with its full handshake and state flood, and a plain
`read_current_preset()`. **None of them touches the audio.** What does is a preset RECALL:
about a second, because loading a preset reloads the engine - the same gap a footswitch
preset change makes, so expected device behaviour rather than a fault.

**And it happens on every recall, including a redundant one.** A follow-up test - four
consecutive recalls, three of them the same already-loaded preset - cut the audio all four
times; only the duration varied, the genuine preset change being longer. An earlier guess
that a redundant recall was free came from not listening for it, and is retracted.

The consequence worth shipping: **`read_preset()` recalls, so a verify-by-re-reading loop
stutters a rig on EVERY iteration**, even when it reads the same slot and nothing changes -
on top of resetting the active scene and discarding unsaved edits, both already known.
`read_current_preset()` reads the live grid with no side effects at all. Recorded in
`read_preset`'s docstring, the protocol notes, and the "Settings only your ears can verify"
table.

## 0.36.0 - 2026-07-31

Three field reports and a hardware session. The theme is settings whose only symptom is
audio, and the corrections are to this library's own earlier claims as often as to the
device's behaviour.

A third field report - the same 36-preset build, now including a day of debugging with a
person at the unit - found the failure shape that matters most on a musical instrument:
writes that are accepted, read back perfectly, and leave the rig silent or clicking.
Items overlapping the second report shipped in 0.35.0; this entry is what is new.

### Transport: device loss is now reported, not swallowed

Unplugging the USB cable used to leave the RX thread spinning silently against a dead
handle, with callers finding out only through eventual timeouts. Now: a read failure is
retried once (a lone blip is transient); two failures in a row confirm loss. Every
transport call then raises **`DeviceLostError`**, blocked requests are woken to
fail fast instead of waiting out their timeouts, and the RX and keepalive threads wait
quietly - which also ends the old loop's hot-spin on a dead handle (three log lines in
9 ms, measured against 0.34.0). The asymmetry is documented where it matters: a READ
raising means the device is gone, a WRITE raising means nothing at all - and **the error
TEXT is unreliable in both directions**. A follow-up session measured four loss
transitions: the retry produced the honest "Device is disconnected" twice and the stale
0xE0005000 stall-lookalike twice, so nothing branches on the message; the retry exists
for blip immunity, and "a read raised" is the whole signal.

### The tuner hunt, resolved as far as the firmware allows

A capture session with a person at the unit went looking for the message a physical tuner
close sends, so a host could send it and undo the invisible engagement. **It sends
nothing.** Opening emits `Tuner{frequency: 0}` (one per open, two cycles gave two);
closing emits nothing at all; and the unit's own MUTE control is byte-identical to
`set_tuner_mute()`, so nothing was being withheld. Replaying the open, `Tuner{DELETE}` and
`ShowTuner{DELETE}` all left the rig silent. The lossless release genuinely requires a
human, and no protocol work will change that on d14e.

- **`restore_audio()`** is the host-side escape hatch that DOES work: it clears the mute
  preference, leaving the unit engaged but audible (engagement alone is harmless, verified
  by ear). It returns whether it had to act, and it costs the player's silent-tuning
  preference - the honest tradeoff, documented as one.
- **`set_tuner_input()` and `set_tuner_mute(True)` now warn** when the write will leave the
  outputs silent, so the failure that cost a field session a morning announces itself.
- `show_tuner()`'s docstring stops saying "until the real message is found" and says there
  is no such message.

Also confirmed by ear this session: the metronome transport polarity (`True` clicked,
`False` stopped), which had rested on factory-content inference plus a field report.

### Connect rides through the openable-but-silent window

There is a real window - ~9 s after a reboot, ~11.7 s after a cold boot, measured - where
the device is enumerated and openable but the control protocol does not answer, so a
successful open proves nothing about readiness. `connect()` now retries the full
handshake (each attempt starts a fresh session id) for up to `handshake_patience`
seconds, 30 by default; the give-up error names the window and points at
troubleshooting when the silence outlasts it. The default was 15 for about an hour -
then an end-to-end verification (host-triggered reboot, unattended reconnect) measured
this unit's window at ~17 s and the 15 s budget failing, so the report's ~12 s estimate
and the first default were both too optimistic. The whole loss-and-recovery path is now
verified live: `DeviceLostError` fired on a real reboot carrying exactly the misleading
stall-lookalike text the correction predicted, and `connect()` rode the window
unattended.

### Added (for state tracking, second batch)

- **`PowerOption`** (SHUTDOWN, REBOOT, STANDBY, WAKE_UP) - and the fact that matters
  more than the enum: **standby does not disconnect.** The USB session stays healthy and
  the unit announces sleep/wake with partial pushes carrying only `power_option`, while
  reboot and shutdown send NOTHING before the reads start raising. One field
  distinguishes "asleep" from "gone".
- The **Grid edit echo is a sparse KEYED delta** - 23 bytes for one parameter write,
  with `row` and `column` explicitly set - the exact opposite of a recalled preset's
  keyless chains. Echo latencies measured: 113-116 ms (parameter), 290-420 ms (block).
  A cached preset can merge echoes directly; this asymmetry is now documented as the
  load-bearing fact it is.
- Two open questions CLOSED in the protocol notes: `FileMessage.type` is the
  presets/IRs/captures category selector (the connect burst enumerates 0 -> 2 -> 1),
  and preset tags are confirmed unwritable by every route including the unit's own save.
- Documented: state pushes are often PARTIAL (merge only present fields);
  `GlobalTempo`'s pair-wise arrival is two alternating shapes; lock mode locks the
  touchscreen and volume knob only - host writes land while it is engaged; reboot ~55 s
  and cold boot recovery timings in troubleshooting.

### Added (for state tracking)

- **`preset_dirty()`** - whether the live grid has unsaved changes. Answers in 2-11 ms,
  flips false across a save (verified live), and is also pushed unsolicited, so trackers
  can subscribe instead of polling. `is_dirty` has no field presence: absent IS false.
- **`RecallReason`** (OTHER, UNDO, SAVE) - `RecallPreset.reason` says WHY the preset
  changed. Measured: host recalls and READ replies carry OTHER, a save's push carries
  SAVE (verified live this session); UNDO is defined but unobserved. Lets a tracker tell
  a save's echo from a genuine preset change.
- Protocol notes: connect-burst timing measured (~9 s to the seed push, consistent,
  against the older 10-25 s worst case); `GlobalTempo` streams one message pair per BEAT
  so its rate follows the tempo; a single touchscreen knob turn broadcasts ~40 `Grid`
  messages; `lock_screen_and_volume_knob` noted in the settings docs.

### The polarity correction: tempo parameter 4 is START, and 1.0 means RUNNING

This library documented it backwards for two releases ("MUTE", 1.0 = muted), and the
report's evidence is decisive: all 17 factory presets hold 0.0 there with the volume at a
normal level - under the old reading, every factory preset on every unit would click
constantly - and writing 1.0 started the metronome on 36 presets. The catalog's START
name was right all along; the wrong polarity had been inferred from the NAME of the
Looper X parameter it mirrors into, and a mirror proves linkage, never meaning.

- `set_metronome_running(bool)` is the new front door, and `TEMPO_PARAMS` maps `START`
  and `PLAYBACK` to index 4.
- **The name `"MUTE"` is refused with an explanation** rather than kept as an alias: a
  silent alias preserves the inverted-write footgun for anyone following the old docs.
- `set_metronome_volume()` no longer claims 0.0 is silent. The range is genuine
  **-60..+9 dB** (wire 0.0 IS -60 dB, quiet but plainly audible), it now takes `real=` in
  dB, and true silence means stopping the transport - which exists, contrary to the old
  "there is no mute flag" claim.

### The tuner engagement hazard, documented (fix needs a capture session)

Any host write to the Tuner - `set_tuner_input()` included - engages an INVISIBLE tuner
state; combined with the mute preference, the outputs go silent with no on-screen cause.
It survives ~100 recalls and 60 saves, read-back is blind to it, and only opening and
closing the tuner on the unit releases it. `show_tuner()` is a measured no-op in both
directions and its docstring now says so plainly. Both setters carry the warning; the
disengage message is queued as the next hardware capture session, after which
`close_tuner()` and automatic disengage will follow.

### Added

- **"Settings only your ears can verify"** - a documented list (api.md, cross-referenced
  from every affected method and from troubleshooting) of settings whose only symptom is
  audio: tuner engagement, the metronome transport, the metronome level. Read-back
  verifying these is a category error - the read confirms the device stored the value,
  not that the rig sounds right. The report's cross-cutting observation, adopted verbatim
  as policy.
- The capture guide gains the report's factory-content polarity check: before believing a
  toggle's polarity, ask whether the whole factory library would behave absurdly under
  your reading.

## 0.35.0 - 2026-07-30

Everything here comes from a second field report, against 0.34.0 as installed from PyPI -
36 presets built in one session, consumed strictly through the published docs and
``help()``, which is exactly the audit this layer needed.

### The capture-bypass conclusion, scoped

0.34.0 said Neural Capture blocks "bypass exactly like any other block". True of the live
grid; wrong about the first save. **A bypass written to a capture placed in the same
session is dropped by the save that first materialises it**, while an ordinary block in
the same row keeps it - reproduced here exactly as the report describes, along with its
workaround (save, recall, re-write the bypass, save again), which is now in
`set_bypass()`'s docstring. Same family as the capture load resetting parameters, except
parameters written after the load DO survive the save and bypass does not.

### Added

- **`bypass_state(preset, row, column)`** and **`param_state(preset, row, column, index)`** -
  read-side counterparts of `set_bypass()` and `set_param()`, so verifying a write no
  longer means walking the proto. The proto's shape is a trap this absorbs: the bypass
  table is positional, and the `row`/`column` fields inside it read 0 on every entry -
  filtering on them returns cell (0,0) thirty-two times.
- **`set_input_port(confirm=True)`** - polls `io_settings()` until the port reflects every
  field written. The docs said "a read straight after a write can report the old value";
  the field data says ONE clean re-read is still not authoritative - a stale value on the
  first read after four writes cost a full build run. `confirm=True` absorbs it, and the
  timeout explains staleness rather than reporting a refusal.

### Fixed (documentation)

- `docs/api.md` had an unclosed code fence that swallowed the per-scene example AND the
  entire tempo section - the exact material a scene-building session needed. Restored,
  with per-scene `set_param`/`set_bypass` examples and the new readers, and a test now
  requires balanced fences in every published doc.
- The api.md method table presented eleven module-level functions (`blocks`, `splits`,
  `free_rows`, `row_status`, `params_equal`, `field_present`, ...) as methods of the
  connection object. They are now written `pyquadcortex.name(...)`, the intro says what
  that means, and a test checks every table entry exists where the table says it does.
- `tempoProgramData` is a REPEATED field read as `preset.tempoProgramData[0]`; the
  docstring read as if it were a message.
- The catalog's per-block knobs attribute is named in api.md: `Model.parameters`, not
  `.params` (which is the wire proto's name and the natural wrong guess).
- `set_capture()` documents that it raises `BlockRefused` on DSP refusal - captures are
  expensive blocks, so it is the likely place to meet one.
- The readme is now `README.md`, so the conventional-case raw URL resolves.

## 0.34.0 - 2026-07-30

Worked from an 11-item field report out of an 18-preset build session (2026-07-30).
Every item is resolved: shipped as API, exonerated with the real trap documented, or
closed as a measured device limitation. Also carries the input-level dB conversion and
HOLD TIMING work from earlier in the week.

### Added

- **`read_current_preset()`** - the LIVE grid, unsaved edits included, with no side
  effects. `RecallPreset{READ}` answers with the device's current editing state, which
  kills the save-to-a-scratch-slot inspection cycle and separates "my write never applied"
  from "it applied and was later reset". The single biggest ask in the field report.
- **`active_scene()`** - which scene the unit is on, read rather than tracked. Several
  writes target "the active scene" and a recall moves it out from under you; now the
  assumption is checkable.
- **`row_status(preset)`** - per-row topology: `occupied`, `free`, or `reserved` (the
  parallel lane of a branch on the row above, spoken for even when empty). This is
  `free_rows()`'s reasoning made visible; an empty row is not necessarily an available
  row, and a naive "no blocks means free" check builds inside someone else's branch.
- **`set_capture(params=...)`** - parameters applied AFTER the capture loads. Loading a
  capture silently resets the block's other knobs, so anything written beforehand is
  lost - a VOLUME of 0.56 read back at the default 0.5, and only a non-default value
  makes the bug visible at all. The docstring now leads with the ordering trap, and
  `model=None` points an existing block at a new capture without re-placing it.
- **`params_equal(a, b, option_count=...)`** - compare wire values by MEANING. List
  parameters compare by selected option, absorbing the rescaling that happens when a
  block is added and the option count changes on rows never written to; plain values
  compare within a float32-honest tolerance; NaN equals NaN (factory content stores
  it). `GAIN_REDUCTION_PARAM` names the input-gate live meter that never round-trips.
### Closed: preset tags (a device limitation, not a library gap)

Tags are not preserved by ANY save path. Factory presets carry them on the wire; the
unit's OWN Save As produced a copy with none, same as every host route, and the unit's UI
offers no tag editor. They are build-chain/cloud metadata that no library can write - so a
preset derived from a factory one is less well labelled than its source whatever tool made
it. The instrument category is separate, survives saves, and is now fully mapped: the
`Instrument` enum matches the unit's "Preferred Instrument" picker in its on-screen order -
Guitar 1, Bass 2, **Synth 3** (new), Vocal 4, **Other 5** (new) - each value confirmed by
setting it on the unit and reading the listing back, which also killed an old "bit flags"
description that Other = 5 refuted.

### Investigated: tuner input coverage

Swept every `Input` id with settled read-backs, then confirmed against the unit's own
picker: its seven options match the accepted set one for one, including `USB_5`/`USB_6`
("USB input 5/6" on screen). **`RETURN_1_2` is refused by the device** - the write
reverts - so combined-returns tuning does not exist and nothing covers all four inputs. A
rig on four inputs tunes them one at a time.

### Investigated: "capture blocks ignore bypass" (the report's blocker)

Five probe rounds, and the answer is good news for the live grid: **Neural Capture
blocks bypass like any other block** - though a second field report against 0.34.0 later
scoped this: a bypass written before the preset's FIRST save is dropped by that save (see
the next release's entry). The live-grid conclusion stands. What the report hit is a compound trap, now documented:
`read_preset()` recalls the slot it reads, which resets the active scene - so a read
interleaved between switching scenes and writing bypass silently retargets the write.
Their capture blocks had `sceneMode` set (factory content does), making their writes
scene-targeted; their other blocks did not, making those writes global (all eight slots
at once). Same write, different landing, and the difference correlated perfectly with
"is it a capture" without being caused by it.

Measured along the way: `sceneBypass[0]` means the ACTIVE scene (as documented); entries
beyond it are ignored, so there is no direct write to another scene's slot; the bypass
table persists for EMPTY cells, so a freshly placed block inherits the cell's old bypass
state; and `ColBypass.sceneMode` is NOT host-writable (alone or accompanied, both
ignored) - a `set_bypass_scene_mode()` drafted for this investigation was removed once it
proved to do nothing.

### Changed

- Tempo settings **index 4 is MUTE and 1.0 means muted** - the naming dispute the docs
  carried as unresolved is settled by propagation: writing it changes a Looper X
  parameter the catalog itself names METRONOME MUTE. That propagation is also the first
  entry in a new "device-mirrored parameters" table in the protocol notes, for diffs
  that see rows they never touched change.
- `set_input_port()` and the protocol notes now say plainly that **`input_port_id` is
  the `Input` enum, not 1/2/3/4** - Return 1 is 4 and Return 2 is 5, because combined
  ids are interleaved.
- The protocol notes carry an explicit real-vs-internal split of `out_portid` values
  (1-15 and 19 reach jacks; 16-18 are row-to-row routing; nothing is validated).

- **`input_level_db()` / `db_to_input_level()`** - convert an input port's wire `level`
  to and from the dB the unit displays. The scale is `dB = -12 + 72 * level` (input gain
  runs -12 to +60 dB), solved from four owner-set trims read on the screen and on the
  wire at the same moment, and matching the spec sheet's "+60dB max input gain". Input
  ports only - lane and mixer levels use a different span (`UNITY_LEVEL`).

## 0.33.1 - 2026-07-29

The PyPI project page, fixed. Docs only - no code changes.

### Fixed

- **Every readme link is now absolute.** PyPI renders the long description with no base
  URL, so the relative links that work on GitHub all 404ed on the project page. They now
  point at the GitHub repo explicitly, and a test enforces it so a relative link can never
  ship again - including inside badge links, whose nested image syntax hid the one that
  survived the first pass.
- The readme no longer claims "five runnable examples" while linking to nine - counts in
  prose drift, so it just says where the examples are. And it links the examples directory
  once instead of both the directory and its readme, which GitHub renders together anyway.

## 0.33.0 - 2026-07-29

The first release on real PyPI: `pip install pyquadcortex` now works without pointing at
TestPyPI. Everything before this version was published to TestPyPI only.

### Changed

- `set_ir()`'s documentation records that it is confirmed end to end - a loader pointed at
  an IR from the host shows that IR on the unit, with no warning icon. (The code shipped in
  0.32.0; only the read-back had been verified at the time.)
- `docs/roadmap.md` gained a wishlist of device features the unit has and the library does
  not - IR import, Neural Capture creation, Looper transport, cloud - each recording how far
  the investigation got. Two more are set aside by decision rather than pending: the tuner's
  live needle, and firmware updates (the one unrecoverable mistake available over this
  protocol).
- The tuner's Live Tuner needle is now its own row in the coverage table, marked
  unsupported, rather than hiding inside a "partly" beside three tuner features that work.

## 0.32.0 - 2026-07-29

IRs are loadable. The blocker was that `IR PATH` is not a path.

### Added

- **`list_irs(folder=None)`** - the Impulse Responses the unit can actually load, each with
  the `key` and `name` `set_ir()` needs. IRs are `FileMessage.type: 1`, a category selector a
  listing request must set. Pass `"2_q"` for "My IRs" only.

  The 588 entries under `/opt/neuraldsp/impulse_responses` are excluded on purpose: they are
  assets belonging to purchased desktop plugins, they carry a name and **no key**, and the
  unit's own IR browser does not show them.

- **`set_ir(row, column, ir, slot=0)`** - point an IR Loader at a library entry. Every loader
  has **two** IR slots whatever its name suggests, and `slot` picks one.

### Changed

- **`IR PATH` takes the library entry's KEY, not a path.** Read off a block loaded by hand on
  the unit: `IR PATH = "CIR_eb6d6d347e75f988010a9746580c31c"` with
  `IR NAME = "Rex 57 on axis"` beside it, matching that entry's `key` and `name` exactly.
  Confirmed by pointing a loader at a different IR from the host and reading back the
  library's own strings byte for byte, on both slots.

  An earlier session burned real time guessing path forms - bare name, full path, path with
  `.wav` - all of which the device stores unchanged without complaint. Two things conspired:
  the parameter is *called* `IR PATH`, and the only IR listing available then reported entries
  with a name and no key, so the field that mattered was absent from the data on hand.

- The IR library documentation no longer implies those 588 plugin entries are usable. Listable
  is not loadable, and a block pointed at one shows a warning icon and "<IR NAME> is missing"
  on the unit - the only failure signal, since the host cannot see it.

### Added (footswitch modes)

- **All six HYBRID pairings are mapped**, and `hybrid_mode(top, bottom)` builds them from the
  new `FootswitchMode` enum. A hybrid gives footswitches **A-D one mode and E-H another**, so
  a composite encodes an ORDERED pair - 3 to 8, in lexicographic order over PRESET, SCENE,
  STOMP. That makes 4 and 7 the same pairing in opposite arrangements, which is the manual's
  "tap the right edge to swap the Modes rows", and it explains the original capture where
  merging Preset with Stomp reported 7. `describe_mode()` names any value; `HYBRID_MODES` is
  the table.

- **`set_mode_cycle()` now refuses value 9.** The device ACCEPTS it and the unit is left with
  a "<blank> + Scene" indicator and **non-functional footswitches** - no error, and the value
  reads back cleanly. It also refuses what the device would silently mangle: values above 9
  (dropped from the cycle), more than one hybrid in a cycle (`[3, 4, 5]` comes back as `[3]`),
  and a hybrid as the only slot (`[7]` alone is refused and the unit reverts).

  Worth noting how close this came to being published as fact: the accept/reject sweep was
  mechanical and reported seven usable composites. Only reading the unit's screen showed that
  one of the seven breaks the instrument. A device that stores a value is not a device that
  supports it.

- **`mode_cycle()`** - the configured mode slots, read from a push that actually contains
  them. `mode()` matches any push carrying `mode`, and the device frequently sends that field
  alone, so reading the cycle through `mode()` could hand back an empty list from a partial
  push and make a configured unit look unconfigured. That misread produced two contradictory
  answers about which mode values the device accepts before it was spotted.

- Measured, with settle-then-decide reads: the device **accepts mode values 0-9 and rejects 10
  and above** (an out-of-range value is dropped, leaving the rest of the cycle). 0, 1 and 2 are
  the base modes, so 3-9 are seven composite (HYBRID) values. What each composite MEANS is
  still unknown - and note that acceptance may only reflect a range check rather than seven
  meaningful pairings, so no encoding should be inferred from it. A composite also cannot be
  the only slot: `[7]` alone is refused and the device reverts to its default.

### Still open

- **Importing** an IR from the host. `File{CREATE, type: 1, total_bulk_create_count: 1,
  folder{key: "2_q"}, ir_payload}` starts a real "Importing IRs" operation and reports it
  finished, but nothing is imported and eight payload encodings failed. Use Cortex Control's
  drag-and-drop, which is the documented route. Note the USB link died during a run of those
  attempts and needed a power cycle.

## 0.31.0 - 2026-07-28

**`favorites()` now returns your Favorites.** In 0.29.0 and 0.30.0 it was a deprecated alias
for `recents()`, on the strength of a conclusion that turned out to be my measurement error
rather than a device limitation.

### Changed

- **`favorites()` reads the Favorites list**, returning entries (possibly an empty list)
  rather than a raw message. The request's `is_favorites` flag selects which list the device
  answers with: measured 10/10 with the flag and 0/5 without.

  The catch, and the reason this was missed: **the reply does not set the flag.** Both lists
  come back with `is_favorites` absent, so a predicate like `m.is_favorites == True` rejects
  every valid answer - which is exactly what the earlier code did, producing a clean
  repeatable timeout that read like a device refusing to answer. `favorites()` correlates on
  `request_id`, which the device does echo.

  If you wrote code against 0.29.0 or 0.30.0 expecting `favorites()` to return Recents, call
  `recents()` instead. Neither version reached PyPI, so this affects TestPyPI installs only.

- An empty Favorites list answers with a real empty push, so `[]` means "none favourited"
  rather than "no answer". The first read after connecting is often dropped, so `favorites()`
  retries before raising, and its `TimeoutError` says so.

- Favorites entries carry `name`, `folder_key`, `folder_name` and `is_factory`, and feed
  straight into `find_preset()`, `recall_preset()` and `remove_favorite()` - round-tripped on
  hardware.

- `docs/capture.md` now lists **three** ways an instrument reports silence that is not there,
  this being the third. All three looked identical from outside and all three were the tool
  rather than the device. The heuristic that fell out is worth more than any of them: a flaky
  negative is usually the device, a perfectly consistent negative is often the observer.

## 0.30.0 - 2026-07-28

Favorites can be written, and HOLD TIMING makes sense now. Both came from watching the unit
do the thing rather than guessing at it.

### Added

- **`add_favorite(entry)` / `remove_favorite(entry)`** - mark and unmark a preset as a
  Favorite, using the exact message the unit sends when you use multiselect and the heart
  button. Confirmed on hardware for both a factory and a user preset.

  Pass an item straight from `recents()`. A mismatched `folder_key` or `is_factory` is
  **ignored in silence** - "Fuzz This" lives in the Factory Library, and naming it under My
  Presets produced no error and no favourite. `verify=True` (the default) waits for the
  device to echo the changed entry back and raises `TimeoutError` explaining exactly that,
  which is how a silent mismatch becomes visible. (0.30.0 said the Favorites list could not
  be read at all, which 0.31.0 corrects - see below.)
- **`set_hold_timing(ms)` / `hold_timing_ms()`** - the HOLD action timing in milliseconds.
  The unit offers 500-1000 ms in 100 ms steps and stores the INDEX, so the 3 the field
  reported was 800 ms on screen. The device stores any integer there unvalidated, so these
  convert and check rather than passing a raw index through.

### Changed

- Two dead ends are now recorded as dead ends rather than left as open questions. There is
  **no per-preset favourite flag** anywhere in the schema (`ProductData` has 21 fields and
  none is one), and **no folder** carries `FolderInfo.is_favorites` - none of 810 folder
  pushes set it. "Favorites and Recent" is a view over `RecentsFavorites` rather than a
  folder that can be listed - but reading Favorites does have a route, which 0.31.0 found.
- The IR library is not what it appears. The 588 entries under
  `/opt/neuraldsp/impulse_responses` all carry plugin prefixes (333 `NG_`, 134 `ME_`, 97
  `ML_`, 18 `CW_`, 6 `JP_`) and none was loadable on the unit tested, whose IR browser was
  empty. A block pointed at one shows a warning icon and "<IR NAME> is missing" on screen -
  so the firmware does resolve these strings and does fail loudly, just not anywhere a host
  can observe. Listable is not loadable, and the correct path format remains unknown.

## 0.29.0 - 2026-07-28

Three more of the coverage table's untested rows, and the method that turned out to be
reading the wrong list.

### Added

- **`recents()`** - the unit's Recents list. This is the method that used to be called
  `favorites()`.
- All four `midi_clock_out` values (OFF, DIN only, USB only, both) confirmed writable
  through `update_settings()`.

### Changed

- **`favorites()` never returned Favorites.** One message type carries both lists,
  distinguished by an `is_favorites` flag the method never checked, so it returned whatever
  arrived first - which is Recents, identifiable by the newest saved preset sitting at its
  head. A `READ` asking for `is_favorites=True` draws no reply at all, so Favorites has no
  known read path over USB. `favorites()` still works as a deprecated alias for
  `recents()`; it just now says what it does.
- The Recents list is NOT read-only, though 0.29.0 said so on the strength of a whole-list
  write being ignored. Watching the unit recall a preset shows both lists are maintained one
  ENTRY at a time, as a `DELETE` then `CREATE` pair - so the earlier write was simply the
  wrong shape. Favouriting uses the same pair with `is_favorites` set. Neither is wrapped in
  a method yet: Favorites cannot be read on demand, so a host write of it cannot be verified.
- **IR Loaders are mapped**, which is a prerequisite for loading one. Models 29001-29008,
  and every loader has TWO IR slots (parameters 0-7 and 8-15) needing **two** strings each:
  `IR PATH` (2, 10) and `IR NAME` (22, 23) - not the single `<hash><name>` string a Neural
  Capture block uses, and the IR library offers no hash anyway. All four strings write and
  survive a save. What is NOT established is which path form the firmware resolves: a bare
  name, a full path and a path with `.wav` all store back byte-identical, and so does
  outright nonsense, so read-back is not evidence an IR loaded. Documented rather than
  wrapped in a method, because a method implying it works would be overclaiming.
- Documented that `params[].index` is unset on every entry the device sends, so **position
  in the list is the parameter index**.

## 0.28.0 - 2026-07-28

Sixteen device settings moved from "reachable in principle" to tested, and one packing bug
came out of it.

### Fixed

- **`set_usb_port()` no longer drops fields.** Sending `level` and `dry_wet` together
  applied the level and silently discarded the dry/wet. The USB port packs like the other
  I/O ports, so the method now sends one field per message and all three land. This was
  found by testing for the fault rather than by tripping over it a third time.

### Added

- **`set_tuner_mute()`** - the Tuner menu's MUTE, for silent tuning. Confirmed writable.
- Fifteen `GeneralSettings` fields are now confirmed writable through
  `update_settings()`, each tested alone and restored: `stomp_mode_auto_assign`,
  `swap_tempo_tuner_access`, `enable_dynamic_delay_compensation`,
  `gig_view_stomp_access_enabled`, `hold_timing`, `midi_channel`, `midi_over_usb`,
  `midi_clock_in_enabled`, `ignore_duplicate_pc`, `disable_internet_connection_check`,
  `dimmed_led_brightness` and the dimming toggles, alongside the brightnesses and
  `scene_block_bypass` that were already known.

### Changed

- Three settings do not behave as their names suggest, and the docstrings now say so:
  `internal_midi_clock_enabled` **refuses** writes (with external clock either way, so the
  tempting explanation is wrong); `dimmed_led_brightness` is **capped just below**
  `led_brightness`, so a high value quietly lands lower; and `hold_timing` is an **index**,
  not the milliseconds the manual's 500-1000 ms range implies - it defaults to 3 and stores
  any integer unvalidated.
- `tuner()`'s docstring no longer claims `frequency` is the detected pitch that ignores
  writes. It is the reference-pitch offset from 440 Hz and it is writable - which
  `set_tuner_reference()` had already established, so the two docstrings contradicted each
  other. The genuine gap is `enable_meter`, which refuses a write, leaving the live needle
  unreadable over USB.
- The MIDI settings the manual lists in a MIDI submenu live in `GeneralSettings`, not in
  the undecoded `MIDISettings`. The coverage table had them as unreachable; four of the
  five are writable today.
- **The coverage summary was overstating coverage by 11 rows** - it claimed 65 yes and 13
  no where the table held 54 and 22. It was maintained by hand. A test now recomputes it
  from the table, so it cannot drift again. The honest figure after this release is 59 yes,
  11 partly, 21 no, 10 n/a of 101.
- The capture guide documents the two ways a listener reports silence that is not there: an
  unregistered message type is discarded before you see it, and filtering the device's
  constant chatter makes a dead USB link look like a quiet one. Both produced wrong
  conclusions here. The tempo MODE finding was re-run with neither flaw and still holds -
  nothing is broadcast when that control changes.

## 0.27.0 - 2026-07-28

Documentation, not code. The library had outgrown its own front door: 107 methods, and a
readme whose "what you can do" section was a 28-row table.

### Changed

- **The readme is half the size** (424 lines to 229) and leads with **four** things rather
  than twenty-eight: edit a preset, build scenes, drive the unit, and find what is on it -
  each with a short snippet. A reader deciding whether this library is for them should not
  have to read a reference table first.
- **[docs/api.md](docs/api.md)** is new and holds what moved: the full method groups, and
  the longer pieces on blocks and the catalog, building a chain, how factory presets build
  scenes, the tempo controls, captures, and the three ways a global-settings read can
  mislead you.
- **[docs/troubleshooting.md](docs/troubleshooting.md)** takes the forty lines on the USB
  link dying, which was the largest thing on the front page and relevant to almost nobody
  arriving.

### Added

Four examples, because the existing five demonstrated the 0.9.0 subset and nothing added
since:

- **`scene_map.py`** - builds a preset whose eight scenes alternate between a main path
  and a parallel lane, using per-scene mixer levels. This is how factory presets do it,
  and it is the single most Quad-Cortex-specific thing the library can do.
- **`footswitches.py`** - STOMP assignments and Preset MIDI Out.
- **`device_settings.py`** - read-only by default; prints the global settings and I/O, and
  demonstrates the read-modify-restore pattern those require.
- **`use_capture.py`** - browses the Neural Capture library and places one.

Plus **[examples/readme.md](examples/readme.md)** describing all nine, and a note on the
two things that bite: rows are zero-based here and 1-4 on screen, and global settings have
no save and no undo.

All four new examples were run against hardware, not just checked for syntax.

## 0.26.0 - 2026-07-28

Neural Capture, and a fix to the capture tooling that had been quietly limiting every
session before this one.

### Fixed

- **The RX path now decodes every message type it can**, 70 of the 72 in the device's
  enum, instead of only the 45 this project had registered by hand. Undecodable inbound
  messages are dropped before dispatch, so anything watching decoded traffic was blind to
  half the schema - and **a feature whose message type was unregistered looked exactly
  like a feature that broadcasts nothing at all**.

  That is not hypothetical: "New Neural Capture" appeared to do nothing on the unit AND
  produce nothing on the wire. With the fix, the very same tap showed
  `NeuralCapture{try_to_show_dialog: true}` immediately. Any earlier "nothing was
  broadcast" conclusion in these notes should be treated as suspect if the feature's type
  was not registered at the time.

### Added

- **`captures()`** - browse the Neural Capture library, over two thousand entries on the
  observed unit, presented on screen as Factory Captures V1, Factory Captures V2 and My
  Captures.
- **`set_capture(row, column, entry)`** - point a capture block at one of them.

  A capture block's model id is only the block TYPE; which capture it plays is the string
  parameter `file_name` at index 5, holding the library file's 64-character content hash
  concatenated directly with its display name. Verified end to end: the owner created a
  capture on the unit, and a block placed and pointed at it from the host came up named
  correctly.
- **`show_capture_dialog()`**, with a warning attached. The unit hands its capture flow to
  a connected host, so a client that stays silent SUPPRESSES the on-device wizard, and one
  that answers `true` without drawing a UI puts the device into a flow with no interface
  anywhere. To use the unit's own wizard, disconnect.

### Corrected

An earlier note reasoned that a capture id must be a per-unit SLOT, because thirteen of
seventeen factory presets reference id 14000 from positions no single capture could
occupy. The observation was right and the conclusion was wrong: they all use the same
block model with different `file_name` strings. The mistake was assuming the model id
carried the capture's identity.

The catalog documentation is corrected too - it does NOT enumerate captures, and does not
grow when one is saved.

## 0.25.0 - 2026-07-28

### Documented, and it reverses an earlier finding

**A HYBRID mode slot is a composite value in `available_modes`, and creating one is
drivable from the host.** Merging Preset with Stomp on the unit and then confirming the
menu reported `available_modes{7, 1}` - the hybrid as 7, Scene as 1 - with cycling
alternating `mode: 7` and `mode: 1`. Sending `[7, 1]` through `set_mode_cycle()` creates
the same arrangement and `set_mode(7)` selects it, both verified.

0.21.0 recorded that merging "produces no broadcast at all, so whatever holds the pairing
has not been seen". That was wrong, and the reason is worth keeping: the earlier session
merged and un-merged WITHOUT confirming the menu, and this state is not published until
commit. The owner had proposed exactly that explanation at the time.

It does not generalise, though - the Tempo menu's MODE control stayed silent through an OK
press AND a save, so "silent" has more than one cause.

What the composite value encodes is still unknown: 7 was Preset+Stomp, by elimination.
Read the value back after making a pairing once on the unit.

## 0.24.0 - 2026-07-28

### Fixed

- **`set_input_port()` and `set_output_port()` could silently drop fields.** The device
  discards some port fields when they arrive alongside another in the same port entry, so
  a call setting several at once would apply only some of them. Both now send **one field
  per message**.

  This had already produced two wrong conclusions in this project's own notes: output
  `mute` was recorded as unwritable, and input `impedance`'s failure was explained away by
  the manual's remark that impedance is disabled for Mic inputs. Neither was true - both
  fields work perfectly when sent alone. Setting all four input fields in one call now
  lands all four, verified on hardware.

### Verified

The typed metronome setters round-trip through hardware end to end -
`TempoSubdivision.EIGHTH_TRIPLET` stored 0.6667, `MetronomeSound.COWBELL` 0.4,
`MetronomeRouting.OUT_1_2` 0.5, `TimeSignature.SEVEN_EIGHT_2_3_2` 0.95.

## 0.23.0 - 2026-07-28

### Added

Complete, named enums for the metronome's four list controls - read off the unit's own
dropdowns and ordered, so these are the real option sets rather than a partial guess:

- **`TempoSubdivision`** (4): QUARTER, EIGHTH, EIGHTH_TRIPLET, SIXTEENTH.
- **`MetronomeRouting`** (5): MULTI, HEADPHONES, OUT_1_2, OUT_3_4, SEND_1_2.
- **`MetronomeSound`** (6): BLIP, BLOCK, COWBELL, DIGITAL, DRUM_KIT, SOFT_KIT.
- **`TimeSignature`** (21): 2/4 through 13/4, the 3/8 family, and the compound 5/8 and
  7/8 signatures with their accent groupings.

Plus typed setters that take them and range-check anything else:
`set_tempo_subdivision()`, `set_metronome_sound()`, `set_metronome_routing()`,
`set_time_signature()`.

Each list's length matches the count the catalog publishes, the ordering was confirmed by
selecting the last entry and watching the wire store exactly 1.0, and every earlier
one-off pairing agrees - 1/8 notes stored 0.3333, 3/4 stored 0.05, the factory default is
4/4, Block stored 0.2, OUT 3/4 stored 0.75.

### Corrected

An earlier note put ROUTING option 0 at the headphones. It is MULTI. That guess came from
assuming an operator's starting point matched the factory default, which is not a safe
inference and produced a wrong answer.

### Settled

The Tempo menu's MODE (global vs per-preset) is **not on the wire**. A dedicated test
ruled out the commit theory: toggling to GLOBAL, pressing OK, toggling back, pressing OK
again and then saving the preset produced no traffic of any kind.

Also noted: changing the time signature rewrites some `STEPSTATE` parameters, which hold
the per-beat accent pattern.

## 0.22.0 - 2026-07-28

Type-safety for the list-valued parameters, which is the shape the rest of the library
already uses for fixed positions - `Input`, `Output`, `Scene`, `Footswitch`,
`MidiOutType`, `SceneBypassBehavior`, `GlobalEQFilter`, `ExpressionBypassMode`,
`LooperState`.

### Added

- **`Parameter.option_count`, `option_to_value()`, `value_to_option()`.** For a list
  parameter the catalog's `steps` IS the option count and the wire value of option N is
  `N / (count - 1)`. Verified against the tempo controls, whose stored values fit
  exactly: the second subdivision is 0.3333, the second time signature 0.05, the fourth
  routing 0.75.

  Documented where this does NOT hold: a parameter whose options enumerate the preset's
  blocks publishes a static `steps` that disagrees with reality - a Doubler's TRIGGER says
  45 while the real list is 19 to 25 - so for those the preset's `dynamic_steps` remains
  authoritative.
- **`set_tempo_option(param, option)`** - sets a list-valued tempo control by option
  number, range-checked against the count, rather than by a raw normalized float.
  Verified on hardware for SUBDIVISIONS, TIME SIGNATURE, SOUND and ROUTING.

### Corrected

An earlier claim that "the catalog names only 8 tempo parameters" was wrong, and the
mistake was mine: an early survey printed only the first 8 and the truncation got written
down as a fact. The catalog describes 23 parameters for model 25000, indices 10 to 22
being `STEPSTATE0` to `STEPSTATE12`. What it actually gets wrong is two NAMES, which is
what `TEMPO_PARAMS` exists for.

### Not shipped, deliberately

No enums naming the tempo options. Seven pairings are now confirmed - subdivisions 0 and
1, time signatures 1 and 2, sound 1, routing 0 and 3 - but the device supplies no option
names for these parameters and the manual does not enumerate them, so most options remain
unnamed. A partial enum reads as a complete one.

## 0.21.0 - 2026-07-28

### Added

- **Tempo parameters by their screen names.** `QuadCortex.TEMPO_PARAMS` maps the unit's
  Tempo menu onto wire indices, built by using each control in a named order: TEMPO 0,
  LED LIGHT 2, VOLUME 3, MUTE 4, PAN 5, TIME SIGNATURE 6, SUBDIVISIONS 7, SOUND 8,
  ROUTING 9. `set_tempo_param()` resolves those names first.

  The map exists because two of the catalog's names differ from the screen: index 4 is
  MUTE on screen (PLAYBACK in the manual) and START in the catalog, and index 7 is
  Subdivisions and NOTELENGTH.
- **`tempo_params(preset)`** to read them back. This is needed because in the STORED
  preset all 24 arrive with `index` absent, so position is the index - the same
  convention as `models[]`. A host write does set `index`; only the device's stored form
  omits it.

### Documented

- **Merging two modes into a HYBRID slot produces no broadcast.** Merging on the unit
  emitted no `Mode` message at all, and `available_modes` still held three entries.
  Un-merging then reported the new ORDER, so slot ordering is broadcast while the hybrid
  pairing is not.
- **The Tempo menu's MODE control also broadcast nothing.** The likely reason is that the
  menu was not confirmed with OK until later and MODE governs where the tempo is
  persisted, so it may only apply on commit - recorded as a question to test rather than
  as unreachable.

## 0.20.0 - 2026-07-28

### Added

- **`set_global_eq_output(level=, out12=, out34=)`** - the Global EQ's OUT tab, which is
  what indices 25 to 27 turned out to be: the overall level, and the two output-pair
  assignments. `out12` is confirmed; `out34` is by elimination, being the only index left
  and never seen written. The OUT level's dB mapping is not established, because the knob
  was watched moving continuously so no value could be tied to a reading on screen.
- **`set_global_eq(band, ..., enabled=)`** - offset 4 within a band is the manual's EQ
  BAND BYPASS, where **1.0 means the band is active**. Confirmed by toggling band 1's
  bypass on the unit, and consistent with every band shipping at 1.0. That accounts for
  all 28 Global EQ parameters.

Also documented: `GlobalEQMessage.bypassed` is the INVERSE of the unit's On/Off control,
so `bypassed: true` is the EQ off - which is how the observed unit ships.

### Closed as not applicable

Two fields that looked like gaps are not:

- **`BinaryPreset.volume` and `pan`** are ignored by every route tried, AND the unit has
  no control for them - they read 1.0 and 0.5 on every preset examined, factory and user
  alike. Inert fields rather than missing support.
- **`BinaryPreset.scene_tempo`** likewise: ignored, reads back empty, and the unit has no
  per-scene tempo at all. Its Tempo menu offers a MODE of global or per preset, nothing
  finer.

## 0.19.0 - 2026-07-28

### Added

- **`set_global_eq(band, gain=, frequency=, q=, filter_type=)`** - the Global EQ by
  band number rather than wire index, with `GlobalEQFilter` for the filter shapes
  (PEAK, HIGH_PASS, LOW_PASS, HIGH_SHELF, LOW_SHELF).

  The layout is 5 parameters per band at offsets GAIN 0, FREQUENCY 1, Q 2, TYPE 3, so
  band N sits at `(N - 1) * 5`. That started as a guess from two data points and was
  properly checked before shipping: changing each of band 1's controls in turn showed
  which index moved, and the whole 28-parameter list then lines up exactly as a
  five-band parametric EQ should - identical gains, identical Qs, monotonically
  increasing frequencies, and shelf/peak/peak/peak/shelf filter types. The filter
  mapping is confirmed twice over: by cycling the control on the unit, and by those
  shipped defaults.

  Offset 4 is 1.0 on every band and is NOT identified, so it is not exposed. Indices
  25 to 27 sit outside the bands and are likewise unidentified.
- **`LooperState.OVERDUBBING = 6`**, observed by pressing OVERDUB during playback and
  again to leave it. `3` is still unobserved - overdub was the obvious guess for it,
  which is a reason not to guess again.

### Confirmed

The tuner's reference pitch is an offset in **Hz**: 442 gave 2.0 and 445 gave 5.0. Two
points on a line, so the scale is settled rather than inferred. And MIDI sources 8 and 9
are the expression pedals - the unit labels them "Exp 1"/"Exp 2" in the MIDI Out list and
"Expression pedal 1" on the detail screen.

## 0.18.0 - 2026-07-28

### Added

- **`copy_preset(from_setlist, position, to_setlist, ...)`** and
  **`duplicate_setlist(source, dest)`**. Neither is a device operation, and finding
  that out is the point: the unit's copy/paste broadcasts the same
  `File{CREATE, folder{key, files{...}}}` shape as a Save As, just aimed at another
  folder key, and its setlist duplicate only NARRATES progress through
  `BulkOperation` - replaying that copies nothing. So both are compositions of
  recall + save, and `save_current_preset` was already able to target ANY folder key,
  which is now confirmed and tested.

  Both are documented with what that mechanism costs: they recall each source preset,
  so they change what is loaded on the unit and take seconds per preset, and they
  carry audio state rather than metadata.

## 0.17.0 - 2026-07-28

### Fixed

- **`ExpressionBypassMode` was reversed.** It is `STOP = 0`, `SWITCH = 1`,
  `HEEL_TOE = 2` - not the manual's listed order, which is what 0.15.0 assumed.
  Anyone who set `ExpressionBypassMode.STOP` on 0.15.0 or 0.16.x actually set
  Heel-Toe. All three values are now confirmed, each set deliberately on the unit
  with a scene change fencing them apart so the value landed on in each window was
  unambiguous, and each round-trips through a save.

  The earlier reading came from assuming what had been set rather than reading where
  the control landed after a cycle. The corrected numbering also explains the unit's
  SWITCH ON control cycling numerically: from Heel-Toe (2) a press gives Stop (0),
  then Switch (1), then Heel-Toe again.

### Documented

- **Global EQ parameter layout: 5 per band, GAIN first.** Band N's group starts at
  `(N - 1) * 5`, with GAIN at offset 0 - band 1's GAIN is index 0 and band 3's is
  index 10, both reading 0.75 for +6 dB on a -12..+12 dB range. The other four
  offsets in a band are not individually identified. Note that a `parameters` block
  carrying no `parameter_index` IS index 0, the zero simply not being serialized.
- The master volume is a separate gain stage: turning the knob changed no port level
  across 114 pushes, so the nearest host equivalent is setting the individual output
  levels. The headphone output's own level is not writable.

## 0.16.1 - 2026-07-28

Documentation only.

- **The master volume is a gain stage of its own.** Across 114 pushes while the knob was
  turned, no port level changed - so it is applied downstream of the stored levels rather
  than rewriting them. The nearest host-side equivalent is setting the individual output
  levels, which are writable; the headphone output's level is not, refusing a write even
  when sent alone.
- The list of open questions that need someone at the unit has moved out of this repo. It
  is about how the library is being built rather than how it works, which is not what this
  repo documents.

## 0.16.0 - 2026-07-28

Second interactive capture session, and the results split neatly: three features gained,
one method removed because testing proved it does not work, and one claim deliberately
left unconfirmed.

### Added

- **`looper()` state names** - `LooperState`, mapped by watching each transport control
  pressed in a known order: 1 idle, 2 playing, 4 recording, 5 armed. `3` was never
  observed and is deliberately absent. Two behaviours worth knowing: with nothing plugged
  in the Looper sits in ARMED indefinitely, because RECORD waits for a signal to cross
  the threshold and the other controls stay inert; and REVERSE and HALF SPEED do not
  change `state` at all, they set `in_reverse` and `half_speed` while playback continues.
- **`master_volume()`** - the level as a normalized 0..1, mapping linearly to the 0-100
  the unit shows (47 on screen read 0.471074373).
- **`pin_model()`, `unpin_model()`, `pinned_models()`** - pinning a model to the top of
  its category. The write carries **no action field** (an UPDATE is ignored, which is why
  an earlier attempt looked refused), and it **APPENDS** rather than replacing, so
  pinning something twice leaves two entries. DELETE removes every entry for an id.
- **`delete_setlist(name)`** - removes a setlist and its contents.

### Removed before it shipped

There is no `set_master_volume()`. The read mapping was clear enough to write one, but a
`MasterVolume` UPDATE carrying a new level is accepted and changes nothing - the knob
appears to be the only way to move it, which fits the device's own pushes carrying
`calibrate` rather than a setpoint.

### Left unconfirmed on purpose

`ExpressionBypassMode.STOP = 2` remains the only confirmed value. The cycle captured in
this session - `2, 0, 1, 2` across three presses of one control - could not be aligned
with the order reported from the screen, and it conflicts with the earlier session where
Stop was chosen directly and stored 2. Rather than pick a reading, HEEL_TOE and SWITCH
stay marked as assumed from the manual's ordering.

### Documented

Global EQ index 10 is band 3's GAIN: setting +6 dB on the unit left it at 0.75,
consistent with a -12..+12 dB range. The rest of the 28-index layout is still unknown.
Duplicating a setlist is still unsettled - reproducing the unit's create-then-BulkOperation
sequence creates the destination and leaves it empty, so that message reports progress
rather than performing the copy.

## 0.15.0 - 2026-07-28

First interactive capture session: five things that host-side probing could not settle,
because in each case the write this library was attempting was simply the wrong one.

### Added

- **`set_param_option(row, column, param, option, source)`** - choose a list (comboBox)
  parameter's option by NAME. Such a parameter stores ``index / (count - 1)``, and the
  names live in the preset rather than the catalog, so it takes a preset to read them
  from. `option_value()` and `option_at()` expose the arithmetic.
- **The side-chain SOURCE is reachable, and it is an ordinary parameter.**
  `Model.sidechain_source_flag` turns out to be device bookkeeping that ignores writes;
  what the unit sends is a normal keyed parameter write to a `comboBox` the catalog
  names `SOURCE`. Its options are the fixed inputs followed by one entry per block
  earlier in the chain, which is exactly what the manual says is selectable.
- **`set_output_mute(port, muted)`** - output mute works, but **only when it travels
  alone**: a port entry carrying `mute` alongside `ground_lift` left the port unmuted,
  which is why an earlier attempt read as a refusal.
- **`set_tuner_reference(offset_hz)`** - the tuner's reference pitch is an OFFSET in Hz
  from 440. Changing FREQ to 442 on the unit broadcast `1.99999809`, so an earlier write
  of `442.0` was far out of range. Writing 5.0 round-trips.
- **`create_setlist(name)`** and `USER_SETLIST_ROOT`. Setlists are siblings under
  `/media/p4/Presets`, NOT children of "My Presets" - the wrong parent is why an earlier
  attempt created nothing. So the MIDI documentation's 'User folders' at bank-select LSB
  2-12 are folders a player creates rather than fixed setlists.
- **`ExpressionBypassMode`**, with `STOP = 2` confirmed from the unit. Heel-Toe and
  Switch follow the manual's ordering of the same control and are not individually
  observed - the enum's docstring says so.

### Still not settled

Duplicating a setlist: the unit's duplicate action broadcasts
`BulkOperation{source_folder, destination_folder}`, but that is the device reporting
progress, and replaying it host-to-device did nothing.

## 0.14.0 - 2026-07-28

Fourth exploration round, still solo. Four more features covered, and one gotcha found
the hard way that changes how any settings write should be built.

### Added

- **`set_master_volume_assignment()`** - which outputs the Master Volume knob governs.
- **`set_global_bypass(cab=..., ir=...)`** - the manual's GLOBAL BYPASS, four booleans
  per collection, bypassing Cab or IR Loader blocks across all presets by row.
- **`set_global_eq_band(index, value)`** - any of the Global EQ's 28 parameters. Sparse
  by index: writing one left the other 27 alone. Which index is which band control is
  not established, so read and compare rather than guess.
- **`set_mode_cycle([...])`** - reorder or remove footswitch mode slots, the manual's
  Modes Configuration menu. The whole list is replaced, which is what the feature is.

### A submessage write replaces the whole submessage

Top-level `GeneralSettings` fields are sparse - send `screen_brightness` alone and only
that changes. A nested SUBMESSAGE is not: sending `master_volume_assignment` with only
`send12` set left the other three flags false, quietly stopping the knob governing
outputs 1/2, 3/4 and the headphones.

So `set_master_volume_assignment()` and `set_global_bypass()` read the current value and
merge before sending. Repeated fields keyed by an index behave like the top level and are
genuinely sparse.

### Documented as not working

`PinnedModels` accepted an UPDATE and pinned nothing. `BinaryPreset.author_name` and
`description` are ignored by a `Grid` update - and the device stamps `author_name` itself
from the signed-in Cortex Cloud account on every user save, so a factory preset's
"Neural DSP" becomes the account name whatever the host sends.

## 0.13.0 - 2026-07-28

Third exploration round, worked through without anyone at the unit. Everything below was
established by driving the device and reading it back; what could NOT be settled that way
is tracked outside this repo rather than left implicit.

### Added

- **`move_block(from_row, from_col, to_row, to_col)`.** `GridMove` had been recorded as
  observed-inbound-only; it drives fine. A cross-row move is how the manual says a
  parallel path gets created, and that is exactly what happens: moving a block from row 0
  to row 1 on a serial preset left the device reporting a branch it had computed itself.
- **`set_split(row, split_column, mix_column)`** and **`clear_split(row)`**. Creating a
  splitter was listed as having no known host shape. It turns out there is nothing to
  create - every even row already carries a splitter, mixer and combined splitter,
  dormant with `-1` columns - so a branch is activated by setting the columns. After
  which `set_splitter_param`, `set_mixer_param` and `set_split_mute` all drive it.
- **`set_expression_bypass()`** - lets a pedal bypass a block, writing both
  `bypass_expression` and `expression_bypass_info` in one message.
- **`set_input_port()`, `set_output_port()`, `set_usb_port()`, `set_midi_thru()`,
  `set_output_pairing()`** - the rest of the I/O settings, sparse and port-keyed.
  `set_input_level()`/`set_output_level()` still work and now delegate.
- **`tuner()`, `show_tuner()`, `set_tuner_input()`, `looper()`** - Tuner and Looper X
  state. `Tuner`, `ShowTuner`, `Looper` and `GigViewButton` are now registered.
- **`list_folders()`** and the `Folder` type. One `File` READ makes the device enumerate
  its whole tree - 399 folders here, not two setlists - including a **2062-entry factory
  Captures Library** grouped into 176 per-amp folders, 588 factory IRs, and every
  installed plugin's artist presets. `list_presets()` already accepted any of those keys;
  that is now documented and tested.
- **`favorites()`** - the unit's Favorites and Recents.
- **`Transport.collect()`** - gather every matching push for a window, for the case where
  one request provokes hundreds rather than one.

### Documented as not working

Writes that were tried and are confirmed no-ops, so nobody repeats them: preset
`volume`/`pan` (via `Grid` and via `ProductData.gain`), `scene_tempo`,
`Model.sidechain_source_flag`, output `mute`, and creating a folder by naming a new key.
Input impedance also did not take, which matches the manual's note that it is disabled
while an input's type is Mic.

Three shapes work but their numbering does not: the Looper's `state`, the
expression-bypass `mode`, and the tuner's reference pitch (`Tuner.frequency` is the
DETECTED pitch - it reads 0 in silence and ignores writes).

## 0.12.0 - 2026-07-27

Second exploration round against the manual audit, opening the global settings
families - the largest area the audit found untouched. Nothing here is per preset:
these change the UNIT, so there is nothing to save and nothing to recall to undo.
Every field below was confirmed by writing it, reading it back, and restoring it.

### Added

- **`settings()` and `update_settings(**fields)`** for `GeneralSettings`, which turns
  out to carry most of the unit's Device Settings and System menus in one message:
  brightness (screen, LED, dimmed), the global Cab and IR bypasses, scene bypass
  behaviour, STOMP auto-assign, hold timing, tempo/tuner swap, Gig View access, latency
  compensation, MIDI channel and clock settings, power button sensitivity, the Master
  Volume per-output assignment, Looper footswitch assignments and disk space.
  `update_settings()` is sparse and validates field names. It deliberately REFUSES
  `power_option` and `reset_wifi_networks`, which are commands rather than settings -
  one can shut the unit down.
- **`set_scene_bypass_behavior()`** and the `SceneBypassBehavior` enum. This one
  matters beyond convenience: it decides what `set_bypass()` actually persists, so
  under `NEVER_OVERWRITE` a bypass write is applied but not kept, which looks exactly
  like a failed write.
- **`io_settings()`, `set_input_level()`, `set_output_level()`.** Port writes are sparse
  and keyed by port id - writing one input's level left the other three byte-identical.
  `io_settings()` also reports impedance, input type, ground lift, mute, the headphone
  and USB routing, expression pedal position, and `plugged` per port.
- **`global_eq()` / `set_global_eq_bypassed()`**, **`mode()` / `set_mode()`**, and
  **`set_gig_view()`**. `Mode.mode` is a SLOT index rather than a named mode, since the
  slots are user-arranged and can be merged into HYBRID modes; `available_modes` lists
  the configured slots.

### Documentation

Three things about global state that will otherwise waste someone's afternoon:

- **State pushes can be partial.** A push following an UPDATE may carry only the field
  that changed, so a reader has to wait for one that actually contains what it wants
  rather than taking the first message of that type. The readers here do that.
- **A read immediately after a write can return the previous value.** These are
  eventually consistent like `File` listings are - a scene-bypass write read back as
  the old value, and reading again a moment later showed the new one. Allow a settle
  before deciding a write was refused.
- **Values are quantized.** Brightness written as 30 reads back 31, and 60 as 59. Port
  levels are float32, so they must be written at full precision to round-trip: writing
  a six-decimal `0.769231` stored something measurably different from the
  `0.769230783` already there, while writing `10/13` reproduced it exactly.

## 0.11.0 - 2026-07-27

The first round of a manual-driven audit: [docs/manual-coverage.md](docs/manual-coverage.md)
lists every feature the Quad Cortex manual describes against what this library can do,
and this release closes the biggest gap it found - the parts of a preset that are not
audio. Each item below was established by performing the action on the unit, reading
what the device broadcast, replaying that shape from the host, and confirming by
save-and-read-back.

### Added

- **`set_stomp_assignment(row, column, footswitch)`** and `clear_stomp_assignment()`,
  for binding a block to a STOMP-mode footswitch, plus `set_stomp_momentary()` and
  `set_stomp_label()` for the maps that travel with it, and `stomp_assignments()` to
  read them. Assigning takes the unit's own two-message sequence - a DELETE of the
  cell's existing assignment, then the new one; an UPDATE alone leaves the old one in
  place. New `Footswitch` enum (A-H = 0-7).
- **`set_expression(row, column, param, pedal, minimum, maximum)`**, assigning an
  expression pedal to a parameter with a sweep range. Setting minimum above maximum
  reverses it, which is how the manual describes inverting a parameter.
- **Per-preset MIDI Out**: `set_midi_out(source, messages)` and
  `set_preset_load_midi_out(messages)`, with `midi_out()` / `preset_load_midi_out()`
  to read them, the `MidiSource` and `MidiOutType` enums, and a `MidiOut` builder
  (`MidiOut.cc()`, `MidiOut.cc_toggle()`, `MidiOut.pc()`, `MidiOut.expression_cc()`).
  These do NOT travel by `Grid` - the preset stores them, but a `Grid` update carrying
  those fields is ignored. `MIDISettings` applies them, and is now registered.
- **`set_split_mute(row, muted)`** for the splitter/mixer MUTE. The manual lists a MUTE
  under both editors and it is ONE control: muting the splitter shows the mixer's MUTE
  already engaged.
- **`set_param(..., text=...)`** for string-valued parameters. A `ParamValue` can carry
  a `string_value`, which is how a cab's microphone is selected.
- **`param_options(preset, row, column, index)`** - the option names of a list
  parameter.

### Documentation

- **Corrected:** comboBox option names were documented as unrecoverable. They are not in
  `ModelRepo`, but the PRESET carries the rendered list in `Param.dynamic_steps`. That
  also answers a question recorded as open: the Doubler `TRIGGER` option index 1 is
  'Follow Input', a fixed entry, and the per-block entries follow the fixed ones - which
  is why the stored value's denominator tracks the preset's block count.
- New protocol sections for per-preset MIDI Out (source layout, type codes, and what
  each of the three generic params means per type), STOMP assignments, expression
  assignment, the split/mix mute, and string-valued parameters.
- `MIDISettings` is write-only in practice: a READ gets no reply, so verify against the
  saved preset.

### Known gaps

- `BinaryPreset.volume` and `pan` are ignored by a `Grid` update and no other route has
  been found.
- `scene_tempo` is untested.
- DSP load remains unreadable; `CPULoad` never arrives, subscribed or not.

## 0.10.0 - 2026-07-27

Everything below was re-derived on hardware against factory presets before being
changed, so each item states what the device does rather than what was reported.

### Fixed

- **`splits()` dropped a branch that never recombines.** A row can report
  `split >= 0` with `mix == -1`: it branches and the lane does not rejoin. Those rows
  were skipped entirely, and the docstring explained the omission as "rows that do not
  branch report -1 for both", which is not what they report. Verified on three factory
  presets - "Strat Ambience" (05B) `(2, -1)`, "Classic Pedalboard" (07C) `(7, -1)`,
  "Stereo Lead" (11B) `(5, -1)`. A branch is now recognised by `split` alone, and
  `Split.rejoins` answers the other question. `Split.lane_row` gives the row the
  parallel lane occupies.
- **`set_block` could fail silently.** A placement the preset has no DSP capacity for
  is accepted on the wire and then is not there, with no error of any kind. It is now
  verified by default against the echo the device sends for each cell it accepts, and
  raises the new `BlockRefused` when none arrives; `verify=False` restores
  fire-and-forget. Reproduced deterministically: a six-block chain added to "OneStar
  Clean Tweed" (02C) places five and drops the bass cab, while the cheaper block after
  it in the same chain lands.
- **`real=` silently produced meaningless values for some parameters.** Where the
  catalog publishes `0..1` with a real-world unit - mixer and splitter levels, lane
  `VOLUME`, `TEMPO` - that range is the wire's own scale, not the span the control
  covers, so converting against it yields a number meaning something else. Those now
  raise `ValueError` (`Parameter.range_is_placeholder` is the test). This corrects a
  documented example: `set_lane_output(param="VOLUME", real=-3.0)` did not attenuate
  3 dB, it silenced the row.
- **`set_splitter_param` and `set_mixer_param` accepted rows that have no splitter or
  mixer.** They now raise `ValueError` for an odd row instead of sending a write into a
  collection the device does not have there.

### Added

- **`set_input_gate(row, param, value=/real=, scene=)`** and
  `INPUT_GATE_CONTROL = 28000`, for the per-row noise gate in
  `chains[].input_control[]` - the last of the four chain sub-collections without a
  setter. `NOISE REDUCTION`, `BYPASS` and `INPUT GAIN` are confirmed writable in both
  directions, per-scene included. `GAIN REDUCTION` is a meter, not a control: the
  catalog types it `grMeter`, and it is sampled at save time, which matters when
  diffing presets.
- **`free_rows(preset)`** - the rows available for an independent chain. A row is free
  only when it holds no blocks AND is not the parallel lane of a branch above it; that
  lane is frequently empty and still spoken for, so block count alone answers the
  question wrongly.
- **`UNITY_LEVEL`** (0.76923077) - what the mixer, splitter and lane level parameters
  hold when nothing is attenuated, measured on every row carrying one across 17 factory
  presets. Knowing it is what distinguishes a deliberately silenced lane from a default.
- **`SCENE_UNLABELLED`**, and `set_scene_label(index, None)` to write it. The unit
  stores an unlabelled scene as a single space, so `label.strip()` detects a blank
  scene and `label == ""` does not.

### Documentation

- **The claim that every row carries a splitter and mixer was wrong.** They exist only
  on rows 0 and 2 - counted across all 68 rows of 17 factory presets - because a branch
  can only originate on an even row with its lane below it. `output_control` and
  `input_control` are padded on all four rows; those four are not.
- **`copy_scene` carries the scene COLOUR as well as the label.** Verified with nothing
  else sent, and on the unit's own screen. So reproducing a scene map needs no
  `set_scene_color` calls for copied scenes.
- **Adding a block rewrites comboBox values on rows never written to.** A selector whose
  options enumerate the preset's blocks has its stored value recomputed when the block
  count changes: on "US TWN Vibrato" (01C) a Doubler `TRIGGER` moves 1/19 -> 1/20 -> 1/23
  as blocks are added, denominator `blocks + 6`, while a recall-and-save with no edit
  leaves it alone. Anyone diffing a preset before and after an edit will meet this.
  comboBox option names are not in `ModelRepo`, so what an index denotes is not knowable
  from the catalog.
- **`param_values` can contain NaN**, in at least four factory presets. Since
  `nan != nan`, a preset compared against itself reports differences - a false failure
  about a build that is in fact identical.
- **Preset `tags` cannot be written, and a saved preset has none.** Three routes are
  confirmed no-ops: `ProductData.tags` on the File CREATE, a File UPDATE carrying them,
  and a `Grid` UPDATE carrying `preset.tags`. The control settles it - a plain save
  reads back with an empty tag list whatever the source had - so nothing stale is
  inherited, and `instrument`, which is settable, is what the unit filters on.
- **DSP load is not readable.** `CPULoad{READ}` times out, adding `"CPULoad"` to the
  connect burst's subscribe READs produces no pushes, and listening across both saw
  none. So headroom cannot be checked before placing a block.
- **Enumeration:** a listing that arrives is complete (five READs on an 18-preset
  setlist, no short listing seen), but a READ does not reliably produce one promptly -
  two of those five saw nothing within 8 s. A timeout means "ask again", not "the
  setlist is empty".
- The per-unit capture-id claim was stated as fact on no evidence; it is now what was
  observed. What IS established: 13 of 17 surveyed factory presets reference capture id
  14000 from positions no single capture could fill at once, so factory presets appear
  to reference capture slots. Whether an id denotes different content on another unit is
  untested here.
- New `docs/protocol.md` sections for the capacity refusal, placeholder ranges, NaN and
  the comboBox behaviour, all listed in the contents; `docs/capture.md`'s noise list
  corrected to what actually arrives.

### Examples

- `inspect_preset.py` reports whether a branch rejoins, which row its lane occupies,
  and which rows are genuinely free; it also skips NaN before comparing per-scene
  values, for the reason above. `build_chain.py` picks its row with `free_rows()`,
  handles `BlockRefused`, and uses `UNITY_LEVEL` for the lane level.

## 0.9.0 - 2026-07-27

### Added

- Two new examples covering the newer API. **`inspect_preset.py`** is read-only and
  prints a preset's blocks by name, its routing, where rows branch into parallel lanes,
  and which parameters differ per scene - a good first thing to run.
  **`build_chain.py`** builds a chain on an empty row: block, input, output, a parameter
  in its own units, and a scene that silences the row. Both avoid bare numbers, and the
  examples are now listed in the readme with what each one touches.
- **[docs/capture.md](docs/capture.md)** - how to read the device's own broadcasts when
  you need a message shape this library does not implement yet, including the pitfalls
  that decide whether a capture is interpretable. Linked from the contributor guide.

### Documentation

- The docs described how several findings were arrived at, which is of no use to someone
  using the library. Rewritten to state how the device behaves and what has been
  verified against hardware. Where a constraint matters it is now stated as a constraint:
  for instance `chain.splitter[]` is a read-only view whose writes are silently ignored,
  which a caller needs to know, without the account of how that was discovered.

## 0.8.0 - 2026-07-27

Field feedback from a couple of dozen sessions and several thousand writes.

### Fixed

- **`wait_for_listing()` no longer aborts on a missed push.** It exists to absorb
  eventual consistency, but called `list_presets()` bare in its poll loop, so a single
  quiet interval raised `TimeoutError` straight out of it - producing exactly the false
  negative its own docstring warns about. One report had it kill a 14-preset build at
  preset 8, after a save that had already succeeded. It now rides out missed pushes
  until its own `timeout`, and you no longer need to wrap it in a retry.
  Its two failures are also now distinguishable: *the condition never became true*
  means listings arrived and your predicate stayed false, while *the device stopped
  pushing listings* means nothing was evaluated - so only the first tells you anything
  about whether your change landed.
- **Corrected `out_portid` 19.** The docs and `set_chain_output`'s docstring lumped
  16-19 together as internal routing. Wrong, and it steered users away from the right
  answer: **16 to 18** are internal row-to-row routing, but **19 (`MULTIPLE`) is a real
  destination** and is what factory presets use to reach the Multi-Out - often exactly
  the value you want when building a chain that has to be audible.

### Documentation

- **A Troubleshooting section**, covering a failure mode whose symptoms actively
  mislead: the unit's USB link can die mid-session, with Cortex Control quit, the cable
  in and the unit booted - so the existing error message sends you the wrong way. It
  now points at the readme. Includes how to tell a flapping port from a plain
  disconnection, that only a full power-down recovers it, and that the link flaps for
  a couple of minutes afterwards in a way that looks identical to the fault. Framed as
  one user's field experience with the cause unknown, not as a diagnosis.
- **MIDI is the simpler route if you only need to switch presets or scenes** - it is
  manufacturer-documented and needs no USB session. This library is for creating and
  editing content, which MIDI cannot do.

## 0.7.0 - 2026-07-27

### Added

- **The device is now told when a client goes away.** Closing a session sends
  `Connection{connected: false}` first, before the transport stops and the handle
  closes, which is what Cortex Control does on quit. Previously this library
  announced the connect and then simply went quiet, so from the unit's point of view
  a client never left - it just stopped sending keepalives.
- **`QuadCortex.disconnect()`** is public, for callers who supply their own transport
  and therefore own teardown. There was no non-private way to send this before.
  It is best effort: a failure never prevents the rest of teardown, which matters
  little in practice since every write on this device is reported as failing anyway
  thanks to the deliberate status-stage STALL.
- `qcctl` gets this for free - it already goes through `connect()`.

### Note

Whether an abandoned session leaves state behind on the device is not established -
there is no device state to read back. This change matches Cortex Control's behaviour;
it is not a fix for a known fault, and no workaround was added for one.

## 0.6.0 - 2026-07-27

Parallel routing is now fully writable, and the grid can be read.

### Added

- **`set_splitter_param(row, param, ...)`**, with `scene=` like the others. It writes
  `chain.combined_splitter`; the `chain.splitter[]` a preset exposes is a read-only view
  of the same state, and writes addressed there are silently ignored. Parameters are
  addressed by the unified model 10004's order (`TYPE`, `STEREO`, `BALANCE`, `LEVEL TO
  A`, `LEVEL TO B`, `FREQUENCY`, `MODE`) whatever type-specific id a preset reports.
- **`splits(preset)`** reports where each row branches into a parallel lane and where
  it rejoins, so grid topology can be read rather than inferred. It reads
  `Chain.split_control_points`, whose `split` and `mix` fields have no presence - so
  anything gating on `HasField` sees nothing and must read them directly. Rows that do
  not branch report `-1` and are omitted.

## 0.5.0 - 2026-07-26

Four gaps that stopped a tester building bass presets from a script. Everything here
was verified on hardware by read-back.

### Added

- **`set_chain_output(row, out_portid)`** - the sibling of `set_chain_input`, and the
  piece that was blocking. Without it a chain built on an empty row could be given
  blocks and an input but never pointed at a jack. **The device does not assign an
  output on its own**, confirmed by adding a block and Input 2 to an empty row and
  reading back `out_portid` still unset - so this is a requirement, not a convenience.
- **`set_mixer_param(row, param, ...)`**, with `scene=`. This is how factory presets
  build scenes: "Darkglass AO900 1" bypasses nothing in any scene and produces all
  eight from per-scene mixer `LEVEL A` / `LEVEL B` across two rows.
- **Per-preset tempo**: `set_tempo_led(on)`, `set_metronome_volume(v)` and the general
  `set_tempo_param(param, ...)`. Reported as having no write path; it turned out
  `tempoProgramData` is applied by a `Grid` UPDATE even though it is not row or column
  keyed. `LED LIGHT` 1.0 -> 0.0 turns the LED off and `VOLUME` -> 0.0 silences the
  metronome, both surviving save and recall.
- **`save_current_preset(default_scene=...)`**, which switches to that scene first
  because the device records whichever is active at save time.
- **`position_to_slot(pos, pad=True)`** for the zero-padded form.

### Fixed

- `slot_to_position` accepted banks past the end of a setlist: `"33A"` returned 256,
  the device ignored the save, and it surfaced 40 seconds later as a read timeout -
  exactly the "the save failed" symptom reported. Both slot helpers now range-check.
- `position_to_slot`'s output could not be compared against the padded form
  `slot_to_position` accepts. Documented, with comparing linear positions recommended.

### Known limits

- **The splitter does not accept host writes.** Four shapes tried - with and without
  the model hash, on a level and on a switch - each saved and read back unchanged,
  while the identical shape against the mixer works. `set_splitter_param()` raises
  instead of silently doing nothing.
- **Splitter and mixer carry no column**, so where a split sits on the grid cannot be
  read, only inferred. The grid topology is only partly recoverable.
- `GlobalTempo` is global and returns only a running clock;
  `MetronomeStatusUpdate` has no mute or level field, which is why muting means
  setting `TempoControl.VOLUME` to zero.

## 0.4.0 - 2026-07-26

**Per-scene parameter values.** A parameter can now hold a different value in each
scene, which was the biggest functional gap: scripts could reproduce a preset's
structure but not its scene behaviour, so anything performable had to be finished
by hand on the unit.

### Added

- **`set_param(..., scene=Scene.D)`** writes one scene and leaves the other seven
  alone, promoting the parameter to scene-following if it is not already.
- **`set_lane_output(..., scene=Scene.E)`** does the same for the per-row Lane
  Output Control, so a **silent scene** - one that mutes the rig without leaving
  the preset - is now scriptable:
  `qc.set_lane_output(row=0, param="VOLUME", value=0.0, scene=Scene.E)`
- **`set_param_scene_mode(row, column, param_index, enabled)`** and
  **`set_lane_output_scene_mode(row, param_index, enabled)`** for explicit control
  over whether a parameter follows scenes at all.

### Documentation

- **Rows and columns are zero-based, and the unit labels rows 1 to 4.** This was
  never stated, and getting it wrong is silent: the edit lands on a real row and
  reads back perfectly, just not the row intended. Also noted that `out_portid` 16
  to 19 are internal grid routes rather than physical outputs, so a lane can be
  muted without silencing anything that leaves the unit.

### How per-scene values work

Three things have to hold, and the library sequences them for you:

- `param_values[0]` applies to whichever scene is **active**; the index is not a scene
  selector, so nothing is ever padded.
- Per-scene values are kept only for a parameter whose `scene_mode` is set. Without it
  a parameter has one global value, which appears in all eight scenes.
- `scene_mode` must travel in a message carrying nothing else; sent alongside a value
  it is dropped.

So a per-scene write is three messages: the flag alone, a scene switch, then the value.
No settle delay is needed between them.

## 0.3.0 - 2026-07-26

Fixes from a review written while building a real preset-generation script against
0.1.0. Two of these were silent and destructive, so upgrade before scripting
anything that edits scenes.

### Fixed

- **`set_param` no longer destroys a parameter across all scenes.** Passing
  `scene=N` above zero padded the message with protobuf defaults below index N;
  the device reads index 0, so the parameter was set to **0.0 in every scene**. It
  now refuses a non-zero scene and explains why: the device applies a parameter
  write to all eight scenes at once and cannot target one.
- **`set_bypass(scene=...)` now works instead of corrupting a different scene.**
  The same padding wrote a default `False` to whichever scene was ACTIVE and did
  nothing to the one asked for. Bypass really is per scene, just not by index: the
  device applies `sceneBypass[0]` to the active scene. Naming a scene now switches
  to it and writes, which leaves the unit on that scene - a visible side effect.
- **`DeviceNotFoundError` is actually raised.** `hid.HIDException` is not an
  `OSError`, so the guidance for the most common first-run failure - unit not
  connected, or Cortex Control still holding the port - was dead code and users got
  a raw traceback.
- **Saving, deleting and moving no longer raise `TimeoutError` on success.** File
  operations are asynchronous and the device often does not reply; a missing reply
  never meant failure.
- Corrected `input_chain_rows`'s worked example, which cited a preset that does not
  have the routing described and contradicted the rule it illustrated.

### Added

- **`field_present(msg, field)`** - `HasField` raises on fields without presence,
  and the schema has many, including `SceneBypass.bypass`. This answers `False`
  instead of crashing, so walking per-scene bypass works.
- **`blocks(preset)`** - the occupied grid cells. Every row reports all 8 column
  slots whether or not they hold anything, so `len(chain.models)` is not a block
  count, and `in_portid == EMPTY` is not an occupancy signal either.
- **`wait_for_listing(setlist, until=...)`** - polls until a listing settles.
  Settling time grows with the number of changes, so a fixed sleep reports failure
  on work that succeeded.
- **`set_lane_output(row, param, value=/real=)`** - the per-row Lane Output
  Control (VOLUME, PAN, MUTE, SOLO), which lives outside `models[]` and so was
  unreachable through the API.
- **`position_to_slot(218) -> "28C"`**, the inverse of `slot_to_position`.
- **`Instrument.NONE`**, so an untagged save is a real enum member rather than a
  bare 0.
- `save_current_preset(confirm=True)` returns the name the device actually stored,
  which can differ from the one requested when it de-duplicates.

### Documentation

- The per-scene write ceiling is now stated plainly, because it decides whether a
  whole class of automation is possible.
- Corrected the claim that nearly every scalar has presence, with the real rule and
  the exceptions.
- Documented slot padding, the lane output block, and that listing lag scales with
  the number of mutations.

### Testing

- Added tests against a **real preset payload** read off a device, rather than only
  against messages this library builds. Both presence and padding findings were
  invisible to construction tests, and two existing tests had asserted the buggy
  construction was correct.

## 0.2.0 - 2026-07-26

Grid blocks and the device's own model catalog. Before this, the library could
edit the blocks a preset already had but could not add or remove one, and had no
idea what any block actually was.

### Added

- **`set_block(row, column, model)`** places a block on the grid, whether the
  cell is empty or already occupied. **`remove_block(row, column)`** clears one.
- **`qc.catalog`** reads the block catalog off the connected unit and caches it:
  every model that unit has, with categories, parameter names, real-world ranges,
  and units. Look models up by id, by name, or by category.
- **`pyquadcortex.models`** holds generated constants for the 412 factory blocks,
  grouped by category, so common blocks can be named in code:
  `models.GuitarOverdrive.CHIEF_DS1`. Purchased plugin content and Neural
  Captures are deliberately excluded, because their ids differ from unit to unit;
  resolve those through `qc.catalog` at runtime.
- **`set_param` accepts a parameter by name** (`param="THRESHOLD", model=...`)
  instead of a positional index, and accepts **`real=`** to pass a value in the
  parameter's own units. Wire values are normalized 0 to 1, and the catalog's
  range is what makes the conversion possible, so `real=-12` on a threshold in dB
  now means what it says.
- `docs/releasing.md`, the release checklist, written after an earlier upload
  shipped a readme that had been built before the last edit to it.

### Changed

- Source distributions now carry the whole `scripts/` directory. The include list
  previously named `compile_protos.sh` on its own, which quietly left
  `generate_models.py` out of the sdist even though the docs tell contributors to
  run it.

### Notes

- There is no 0.1.1 release. The version was bumped for a documentation fix and
  the block and catalog work landed before it was ever published, so it shipped as
  0.2.0 instead.

## 0.1.0 - 2026-07-24

First release, published to TestPyPI only.

Control of a Quad Cortex over USB, speaking the device's own protobuf protocol:

- `connect()` opens the device, runs the connect handshake, and hands back a
  ready client that also works as a context manager.
- Read the firmware version, list a setlist, find a preset by name, recall a
  preset, read a preset back in full, and switch scenes.
- Edit the grid: input routing, parameters, and bypass.
- Scenes: copy or swap, and set labels and colors.
- Manage presets: save, delete, and move.
- Presets are addressed by name, by the slot name shown on the unit (`"28C"`), or
  by index. Ports, instruments, scenes, and setlists are named enums rather than
  bare numbers.
- A `qcctl` command-line tool for the common one-off actions.

# Design direction

> Purpose: where this library is meant to go, and the dead ends already found, so a contributor can tell intent from accident.

Nothing here is a commitment. Several leaks below have been absorbed one at a
time, and those rows are struck through rather than deleted, so the direction
can be judged by what it has produced.

## The next big step: a domain model of the unit

Today's protocol API is a thin wrapper over the unit's protocol. `QuadCortex`
methods correspond closely to protobuf messages: you send a `Grid` update, you
await a `RecallPreset` push, you pass a linear slot position. That made the
protocol legible and testable, and it leaves the caller holding knowledge that
belongs to the library.

**The goal is an object model of the Quad Cortex that represents what the unit
shows and what it can do, and behaves the way the unit behaves.** A caller should
reason about presets, scenes, rows, blocks and inputs the way they do standing in
front of the unit, and never learn a protocol artifact to get a correct answer.
The design is [domain-model.md](domain-model.md); the first surfaces have
shipped.

The test for whether this is working: can a user of the library be surprised by
something that is true of the wire but not true of the unit? Every "yes" is a
leak the library should have absorbed.

### The leaks we know about

Each is documented in [protocol.md](protocol.md) as something the caller must
know. In the target design, each is something the library knows instead.

| Leak | What a caller must know today | What they should be able to do |
|---|---|---|
| `ColBypass.sceneMode` | Partly absorbed: `field_present` stops the crash and writes are correct, but a caller still has to know the flag exists to read state safely | Ask whether a block is on in a given scene and get the answer the unit would give |
| Grid geometry | Partly absorbed by `blocks()` and `splits()` | Address a block by its position on the grid, or iterate the grid |
| The edit path | Edits must be recall, then row/column-keyed update, then save; writing a whole preset back does nothing | Change a preset and save it |
| Scene copy side effects | Copying a scene also moves its label and colour | Copy a scene, and keep the label if that is wanted |
| Setlist paths | The factory path needs a trailing slash for recalls but not for listing keys | Refer to the factory library |
| ~~Slot addressing~~ | **Absorbed.** Slot names are accepted anywhere a position is taken, and `position_to_slot` converts back | |
| ~~Empty slots~~ | **Absorbed.** `list_presets` returns occupied slots by default; `include_empty=True` gives the full map | |
| ~~Saved names~~ | **Absorbed.** `save_current_preset(confirm=True)` returns the name the unit stored | |
| The write STALL | Nothing. The transport swallows it | |

The last row is the model for the others: the benign write stall is a real
protocol wart that no caller sees, because the transport absorbs it.

### Roughly what it might look like

Illustrative only:

```python
with pyquadcortex.connect() as qc:
    preset = qc.setlists.my_presets["28C"]    # slot names, as shown on the unit
    print(preset.name, [s.name for s in preset.scenes])

    scene = preset.scenes["Wombat"]            # scenes by label or by letter
    for block in scene.blocks:
        print(block.position, block.enabled)   # effective state, sceneMode absorbed

    scene.blocks[1, 3].enabled = False         # grid coordinates, as on screen
    preset.rows[1].input = Input.RETURN_1      # rows are 1 to 4, like the unit
    preset.save()                              # the recall/edit/save dance is internal

    preset.scenes["D"].copy_from(scene, keep_label=True)
```

Properties reflect what the unit displays. Mutations are expressed as intent, and
the library produces whatever message sequence the unit requires.

### How to get there without wrecking what works

- **Keep the protocol layer.** It is the foundation the model is built on, and
  it stays valuable for protocol work and for anything the model does not cover
  yet (ADR-0004).
- **The layering already supports this.** The model is a layer above `client.py`
  with no new wire knowledge ([architecture.md](architecture.md#layer-map)).
- **It needs device state.** A model implies caching the unit's pushed state and
  keeping it current. That design is [domain-model.md](domain-model.md) section 9.
- **Absorbing a leak requires knowing the truth.** Verify before hiding, or the
  abstraction lies, which is worse than the leak.
- **It is a breaking change in shape, not in behaviour.** It landed as a new
  namespace and becomes the documented front door as it covers enough (ADR-0006).

## Smaller things worth doing

- **A local MCP server** wrapping the library, so a Quad Cortex can be driven
  conversationally. The public API is kept clean enough to wrap.
- **Scene labels and colours as first-class scene properties** in the model,
  rather than index-addressed setters.

## Wishlist: device features not implemented yet

Things the unit does that this library does not. Each records how far the
investigation got, so picking one up does not start from zero. Nobody is working
on these.

- **Import an Impulse Response from the host.** Everything but the payload is
  mapped: `File{CREATE, type: 1, total_bulk_create_count: 1, folder{key: "2_q"},
  ir_payload}` makes the unit start a real "Importing IRs" operation and report it
  finished, but nothing is imported. Eight encodings were tried (16- and 24-bit
  PCM WAV at 48 and 44.1 kHz, 1024 and 4096 samples, an IEEE-float32 WAV, raw
  int24, raw float32, with and without a `.wav` name and a sha256 key). Outbound
  fragmentation is proven sound to 26 reports, so the transport is not the
  problem. The manual says uploaded WAVs are "automatically resized to 1024
  samples", so the conversion is probably done off-device. Until this lands,
  import IRs with Cortex Control; `list_irs()` and `set_ir()` then use them. The
  USB link died during one run of these attempts; do not run them unattended.

- **Create a Neural Capture.** The handshake is understood: the unit hands the
  flow to a connected host (`NeuralCapture{try_to_show_dialog}`, answered with
  `show_dialog`), and the engine is the internal `NC_Recorder`, `NC_Trainer` and
  `NC_Refiner` models. A connected client suppresses the on-device wizard.
  Everything after the handshake is unexplored, and capturing needs re-patching an
  amp and a load box at the unit, so a host can automate the paperwork only.

- **Drive the Looper transport.** `looper()` reads the full status and
  `LooperState` names five states. No host-side transport control has been found;
  MIDI CC#48 to 61 is the documented route, and the unit's own buttons emit
  `update_type: BUTTONS` pushes.

- **Cloud sign-in, backups and capture sharing.** `CloudLogin`, `CloudProduct`,
  `CloudBackup`, `BackupsForward` and `CloudTransferState` are decoded and none
  has been driven. It touches the owner's account, so it needs explicit
  permission before anyone probes.

## Looked at and set aside

- **Wi-Fi / network control.** Considered, and not feasible without access this
  project does not have, so USB is the only transport (ADR-0003). The specific
  blocker is not written down; if you pick this up, record what you learn.

- **The Tuner's live needle.** `Tuner.enable_meter` and `Tuner.meter` exist, but
  `enable_meter` refuses a host write, so the needle never streams. Unsupported by
  decision: the tuner's useful parts work, and a remote needle for an instrument
  you have to be holding is not worth chasing.

- **Firmware updates.** `Updater` is decoded and has never been sent anything,
  on purpose. A botched firmware write is the one mistake a factory reset does
  not fix. Out of scope permanently.

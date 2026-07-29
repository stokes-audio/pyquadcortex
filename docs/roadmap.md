# Design direction

Ideas for where this library should go. It is written down so the intent survives,
and so a contributor can tell the difference between "this is how the library is
meant to be" and "this is just how it happens to be so far".

Nothing here is a commitment, but it is no longer all hypothetical either: several
leaks below have since been absorbed one at a time, and those rows are struck
through rather than deleted, so the direction can be judged by what it has actually
produced.

## The next big step: a domain model of the device

Today's API is a thin, honest wrapper over the device's protocol. `QuadCortex`
methods correspond closely to protobuf messages: you send a `Grid` update, you
await a `RecallPreset` push, you pass a linear slot position. That was the right
first step - it made the protocol legible and testable - but it leaves the caller
holding a lot of knowledge that belongs to the library.

**The goal is an object model of the Quad Cortex that represents what the unit
shows and what it can do, and behaves the way the unit behaves.** A caller should
be able to reason about presets, scenes, rows, blocks, and inputs the way they do
standing in front of the hardware, and never have to learn a protocol artifact to
get a correct answer.

The test for whether this is working: **can a user of the library be surprised by
something that is true of the wire but not true of the device?** Every "yes" is a
leak the library should have absorbed.

### The leaks we already know about

Each of these is currently documented in [protocol.md](protocol.md) as something
the caller must know. In the target design, each is something the library knows
instead.

| Leak | What a caller must know today | What they should be able to do |
|---|---|---|
| `ColBypass.sceneMode` | Partly absorbed: `field_present` stops the crash and writes are correct, but a caller still has to know the flag exists to read state safely | Ask whether a block is on in a given scene and get the answer the unit would give |
| Grid geometry | Partly absorbed by `blocks()` for models. Splitter and mixer carry no column at all, so their position stays unknowable | Address a block by its position on the grid, or iterate the grid |
| The edit path | Edits must be recall -> row/column-keyed update -> save, and writing a whole preset back silently does nothing | Change a preset and save it |
| Scene copy side effects | Copying a scene also moves its label | Copy a scene, and be able to keep the label if that is what is wanted |
| Setlist paths | The factory path needs a trailing slash for recalls but not for listing keys | Refer to the factory library |
| ~~Slot addressing~~ | **Absorbed.** Slot names are accepted anywhere a position is taken, and `position_to_slot` converts back | |
| ~~Empty slots~~ | **Absorbed.** `list_presets` returns occupied slots by default; `include_empty=True` gives the full map | |
| ~~Saved names~~ | **Absorbed.** `save_current_preset(confirm=True)` returns the name the device actually stored | |
| The write STALL | Writes "fail" and the error must be ignored | Nothing. This one is already absorbed by the transport |

That last row is the model for all the others: the benign write stall is a
significant protocol wart that no caller ever sees, because the transport
swallows it. The rest deserve the same treatment, and the struck-through rows show
it is achievable one leak at a time rather than only by a grand redesign.

### Roughly what it might look like

Illustrative only, to convey the shape:

```python
with pyquadcortex.connect() as qc:
    preset = qc.setlists.user["28C"]          # slot names, as shown on the unit
    print(preset.name, [s.name for s in preset.scenes])

    scene = preset.scenes["Wombat"]            # scenes by label or by letter
    for block in scene.blocks:
        print(block.position, block.enabled)   # effective state, sceneMode absorbed

    scene.blocks[1, 3].enabled = False         # grid coordinates
    preset.rows[0].input = Input.RETURN_1
    preset.save()                              # the recall/edit/save dance is internal

    preset.scenes["D"].copy_from(scene, keep_label=True)
```

Properties reflect what the unit displays. Mutations are expressed as intent, and
the library is responsible for producing them with whatever message sequence the
device actually requires.

### How to get there without wrecking what works

- **Keep the protocol layer.** The current `QuadCortex` message-level API is the
  foundation the model would be built on, and it stays valuable on its own for
  anyone doing protocol work or implementing something the model does not cover
  yet. The domain model belongs on top of it, not instead of it.
- **The layering already supports this.** A domain model is a new layer above
  `client.py` with no new wire knowledge. See
  [architecture.md](architecture.md#layer-map).
- **It will need device state the library does not track yet.** Much of the
  awkwardness today comes from being stateless: every read is a recall, and there
  is no notion of "the preset currently on the grid". A model implies caching the
  device's pushed state and keeping it current, which is a real design problem in
  its own right (invalidation, edits made on the unit, reconnects).
- **Absorbing a leak requires knowing the truth.** Several entries in the table
  above are only partly characterised. `sceneMode` is understood well enough to
  act on; the effect of a nonzero value beyond "follows scenes" is not. Verify
  before hiding, or the abstraction will lie - which is worse than the leak.
- **It is a breaking change in shape, not in behaviour.** It can land additively
  (a new namespace) and become the documented front door once it covers enough.

## Smaller things worth doing

- **A local MCP server** wrapping the library, so a Quad Cortex can be driven
  conversationally. The public API is deliberately kept clean enough to wrap.
- **Model and parameter names.** Blocks are identified on the wire by hash, with
  no human-readable name, so tooling cannot yet say "the Exotic Boost block" or
  name a knob. Recovering that mapping would improve both the library and any
  domain model built on it.
- **Scene labels and colors as first-class scene properties**, rather than
  index-addressed setters.

## Wishlist: device features not implemented yet

Things the unit demonstrably does that this library does not. None is blocked by the
hardware - each is a gap in what has been worked out - and each entry records how far
the investigation got, so picking one up does not start from zero. Nobody is working
on these; they are here to be claimed.

- **Import an Impulse Response from the host.** Everything but the payload is mapped:
  `File{CREATE, type: 1, total_bulk_create_count: 1, folder{key: "2_q"}, ir_payload}`
  makes the device start a real "Importing IRs" operation against "My IRs" and report
  it finished - but nothing is imported. Eight encodings were tried (16- and 24-bit PCM
  WAV at 48 and 44.1 kHz, 1024 and 4096 samples, an IEEE-float32 WAV, raw int24, raw
  float32, with and without a `.wav` name and a sha256 key). Transport is not the
  problem: outbound fragmentation is proven sound to 26 reports. The manual says
  uploaded WAVs are "automatically resized to 1024 samples", so the conversion is
  probably done off-device and the firmware wants a pre-processed form. **Until this
  lands, import IRs with Cortex Control's drag-and-drop** - `list_irs()` and `set_ir()`
  then use them normally. Note the USB link died during a run of these attempts and
  needed a power cycle, so pace them and do not run them unattended.

- **Create a Neural Capture.** The handshake is understood - the unit hands the flow to
  a connected host (`NeuralCapture{try_to_show_dialog}`, answered with `show_dialog`),
  and the engine is the internal `NC_Recorder`/`NC_Trainer`/`NC_Refiner` models. A
  connected client SUPPRESSES the on-device wizard, which is why this looked dead twice
  before the handshake was found. Everything after that handshake is unexplored.
  Realistically this stays low value: capturing requires physically re-patching an amp
  and a load box at the unit, so a host can automate the paperwork but not the part that
  takes the time. Using an existing capture is already supported (`captures()`,
  `set_capture()`).

- **Drive the Looper transport.** `looper()` reads the full status and `LooperState`
  names five states, so the inbound side is done. No host-side transport control has
  been found; MIDI CC#48-61 is the documented route, and the unit's own buttons emit
  `update_type: BUTTONS` pushes, so the shape of what a press looks like is visible even
  though the way to send one is not.

- **Cloud sign-in, backups and capture sharing.** `CloudLogin`, `CloudProduct`,
  `CloudBackup`, `BackupsForward` and `CloudTransferState` are all decoded and none has
  ever been driven. Lower value for a library about local control, and it touches the
  owner's account and cloud storage - so it needs explicit permission before anyone
  starts probing, not just a free afternoon.

## Looked at and set aside

Recorded so nobody spends a weekend rediscovering a dead end.

- **Wi-Fi / network control.** The Quad Cortex has network connectivity, and
  driving it that way was considered rather than overlooked. It was not feasible
  without access this project does not have, so USB is the only transport
  implemented. The specific blocker is not written down here; if you are picking
  this up, treat "someone already found this closed" as the starting point rather
  than an answer, and please record what you learn.

- **The Tuner's live needle.** `Tuner.enable_meter` and `Tuner.meter` exist and are
  decoded, but `enable_meter` refuses a host write - it stays false and `meter` stays
  0.0 - so the needle never streams. **Deliberately unsupported.** The tuner's useful
  parts already work (`show_tuner()`, `set_tuner_input()`, `set_tuner_reference()`,
  `set_tuner_mute()`), and a remote needle for an instrument you have to be holding is
  not worth chasing the write that would enable it.

- **Firmware updates.** `Updater` is decoded and has never been sent anything, on
  purpose. A botched firmware write is the one mistake in this protocol that a factory
  reset does not fix, and there is no version of this library that should risk a
  player's unit to save them a menu tap. Out of scope permanently, not pending.

# Design direction

Ideas for where this library should go. Nothing here is committed, and none of it
is implemented. It is written down so the intent survives, and so a contributor
can tell the difference between "this is how the library is meant to be" and
"this is just how it happens to be so far".

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
| `ColBypass.sceneMode` | Per-scene bypass values are only meaningful when `sceneMode` is set; otherwise they are unmaintained and can contradict the unit | Ask whether a block is on in a given scene and get the answer the unit would give |
| Grid geometry | A recalled preset carries no explicit `row` or `column`; position must be inferred from list ordering | Address a block by its position on the grid, or iterate the grid |
| The edit path | Edits must be recall -> row/column-keyed update -> save, and writing a whole preset back silently does nothing | Change a preset and save it |
| Scene copy side effects | Copying a scene also moves its label | Copy a scene, and be able to keep the label if that is what is wanted |
| Setlist paths | The factory path needs a trailing slash for recalls but not for listing keys | Refer to the factory library |
| Slot addressing | Slots are linear positions; `"28C"` must be converted | Use the slot name shown on the unit |
| Empty slots | A setlist always reports 256 entries, most of them placeholders | Iterate the presets that exist, or ask for free slots |
| Saved names | Saving silently renames on a name collision, so the name you asked for may not be the name you get | Save, and be told what it was called - or be warned before it happens |
| The write STALL | Writes "fail" and the error must be ignored | Nothing. This one is already absorbed by the transport |

That last row is the model for all the others: the benign write stall is a
significant protocol wart that no caller ever sees, because the transport
swallows it. The rest of these deserve the same treatment.

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

## Looked at and set aside

Recorded so nobody spends a weekend rediscovering a dead end.

- **Wi-Fi / network control.** The Quad Cortex has network connectivity, and
  driving it that way was considered rather than overlooked. It was not feasible
  without access this project does not have, so USB is the only transport
  implemented. The specific blocker is not written down here; if you are picking
  this up, treat "someone already found this closed" as the starting point rather
  than an answer, and please record what you learn.

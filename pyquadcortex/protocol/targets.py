"""Where a parameter lives on the wire.

Every parameter the unit has is stored as ``params{index, ...}`` inside some
container, and until now the library had a separate method per container:
``set_param``, ``set_lane_output``, ``set_input_gate``, ``set_mixer_param``,
``set_splitter_param``, ``set_tempo_param``, and a second copy of that fan-out
for ``scene_mode`` and for expression assignment. Six ways to do one thing,
differing only in where the parameter lives.

A TARGET is that difference, made into a value. ``set_param(LaneOutput(0),
"VOLUME", ...)`` and ``set_param(Block(0, 2), "GAIN", ..., model=5011)`` are the
same operation against different addresses, so there is one method again and a
new container costs a class rather than a method per operation.

What a target knows, and why each part is needed:

* **its collection**, the repeated field on ``Chain`` that holds it;
* **how that collection is KEYED**, which is not uniform and is the reason a
  single generic builder could not be written without this type. A block keys by
  ``column``. The lane output, the input gate and the mixer key by ``hash``. The
  combined splitter carries NEITHER - the device's own broadcast sends it bare,
  and that is the shape this library copies. Tempo is not on a chain at all;
* **its catalog model**, so a parameter can be named rather than indexed. This
  is fixed for every target except a block, whose model depends on what the
  player put in that cell - which is exactly why ``set_param`` has always made
  the caller pass ``model=`` and the per-collection methods never did;
* **what it refuses**, which is currently one target and two parameters.

Targets are frozen and carry no device handle, so they are free to build, pass
around, and put in a list. Nothing here talks to a device.
"""
from dataclasses import dataclass

from pyquadcortex.protocol.errors import ControlNotDrivable


#: Catalog model ids for the containers whose model never varies.
CABSIM_LAYOUT = 12000
#: The categories whose models use the `Default Cabsim` layout. Enumerated
#: rather than matched on a "Cabsim" prefix: this repo's precedent is
#: `LANE_OUTPUT_UNASSIGNABLE` - "a MEASURED LIST, not a rule" - and a prefix
#: would silently sweep in a future category whose layout may differ.
#: `tests/hardware/test_generated_constants.py` holds the same four names.
CABSIM_CATEGORIES = ("Cabsim Guitar (M)", "Cabsim Guitar (ST)",
                     "Cabsim Bass (M)", "Cabsim Bass (ST)")
LANE_OUTPUT_CONTROL = 23000
INPUT_GATE_CONTROL = 28000
TEMPO_CONTROL = 25000
MIXER = 11000
#: The unified splitter model. ``SPLITTER_AB`` (10000) is the older two-parameter
#: view; writes go through the unified one.
SPLITTER = 10004
SPLITTER_AB = 10000

#: Lane Output Control parameters a host cannot assign an expression pedal to.
#:
#: A MEASURED LIST, not a rule, and the distinction cost a session. Three
#: plausible rules were tried against hardware and all three are false:
#:
#: * "switch-typed parameters are refused" - no. The Jewel's HIGH CUT, the
#:   Mixer's PHASE and the Splitter's TYPE are all ``switch`` and all accept one.
#: * "bypass-like parameters are refused" - no. The Input Gate Control's BYPASS
#:   is bypass-like, accepts an assignment, and accepts a clear.
#: * "``output_control`` params reject ``expression``" - no. VOLUME and PAN, in
#:   the same block, accept one.
#:
#: Every other container tested - blocks, the input gate, the mixer, the
#: splitter - accepts an assignment on every parameter kind. These two are the
#: only refusal known in the library, so they are named rather than derived.
LANE_OUTPUT_UNASSIGNABLE = ("MUTE", "SOLO")


def _require_even_row(row: int, what: str):
    """Splitters and mixers exist only on rows 0 and 2."""
    if row % 2:
        raise ValueError(
            f"row {row} has no {what}: a branch can only originate on row 0 or "
            f"row 2, whose parallel lane is the row below it. Rows 1 and 3 report "
            f"an empty {what} collection, and a write addressed there does nothing."
        )


class ParamTarget:
    """Base for the addresses below. Not instantiated directly.

    A subclass supplies ``collection`` and ``model_id``, and overrides
    :meth:`container` only where the keying differs from the ``hash`` default.
    """

    #: The repeated field on ``Chain`` holding this container.
    collection = ""
    #: The catalog model id, or ``None`` when the caller must supply one.
    model_id = None
    #: Parameter names this target cannot have an expression pedal assigned to.
    unassignable = ()
    #: Whether this target's parameters can hold per-scene values. Scenes are a
    #: property of the GRID, so the one target that is not on the grid says no.
    supports_scenes = True

    def container(self, msg):
        """Add this target's container to ``msg`` and return it.

        The default is the hash-keyed shape the lane output, the input gate and
        the mixer all use.
        """
        chain = msg.preset.chains.add()
        chain.row = self.row
        element = getattr(chain, self.collection).add()
        element.hash = self.model_id
        return element

    def model(self, get_catalog, model=None):
        """This target's catalog :class:`Model`, or ``None`` if unknown.

        ``get_catalog`` is a zero-argument callable, not a catalog. The catalog
        comes FROM the device, so fetching one costs a round trip - and a write
        addressed by wire index needs no catalog at all. Everything here takes
        the callable and calls it only on the paths that genuinely need it.
        """
        ident = model if model is not None else self.model_id
        if ident is None:
            return None
        return ident if hasattr(ident, "parameter") else get_catalog()[int(ident)]

    def index_of(self, param, get_catalog, model=None):
        """``(index, spec)`` for ``param``, which is a name or a wire index.

        A NAME needs the catalog, so this fetches one. An INDEX does not, and
        this does not - it returns ``(param, None)`` untouched, which is what
        keeps an index-addressed write working on a client that has never
        spoken to a device. Ask for :meth:`spec_at` when the spec is actually
        needed.
        """
        if isinstance(param, str):
            source = self.model(get_catalog, model)
            if source is None:
                raise TypeError(
                    f"naming a parameter on {self.describe()} needs "
                    f"model=<model id or catalog Model>: what is in a grid cell "
                    f"is whatever the player put there, so the address alone "
                    f"cannot say. Pass a wire index instead, or use the Block "
                    f"that blocks() handed you - it carries model_id."
                )
            spec = source.parameter(param)
            return spec.index, spec
        return param, None

    def spec_at(self, index, get_catalog, model=None):
        """The catalog :class:`~pyquadcortex.protocol.catalog.Parameter` at ``index``.

        ``None`` when the catalog does not describe it, which is a real case:
        the wire carries more parameters than the catalog documents on several
        blocks. Such an index is still writable by value; it just cannot be
        converted from real units.
        """
        source = self.model(get_catalog, model)
        if source is None or not 0 <= index < len(source.parameters):
            return None
        return source.parameters[index]

    def normalize(self, index, real, get_catalog, spec=None):
        """Convert ``real``, in the parameter's own units, to the wire's 0..1.

        The catalog answers this, so ``get_catalog`` is taken lazily rather than
        as a spec: an indexed write with ``value=`` never fetches one. ``spec``
        is a hint from :meth:`index_of` when a name was resolved, so naming a
        parameter costs one fetch rather than two.
        """
        # The LAYOUT wins where there is one. A cab's own catalog entry does not
        # describe the wire: 12 of the 174 cab models list something else
        # entirely at index 2 - a Plini Cab calls it POSITION over 0..1 - while
        # the wire carries `Default Cabsim`'s LEVEL, -40..6 dB and tapered.
        # Converting -3.0 dB against POSITION would refuse it as out of range,
        # and a value inside 0..1 would be written on the wrong scale in silence.
        layout = self._layout_spec(index, get_catalog)
        if layout is not None:
            spec = layout
        elif spec is None:
            spec = self.spec_at(index, get_catalog)
        if spec is None and self.model_id is None:
            raise TypeError(
                f"real= on {self.describe()} needs model=<model id or catalog "
                f"Model>, because the conversion depends on WHICH block is in "
                f"the cell. Use the Block that blocks() handed you - it carries "
                f"model_id - or pass value= with the normalized 0..1."
            )
        if spec is None:
            raise ValueError(
                f"real= needs a parameter the catalog describes, and it does not "
                f"describe index {index} on {self.describe()} (that is real - the "
                f"wire carries more parameters than the catalog documents). Pass "
                f"value= with the normalized 0..1 instead."
            )
        return spec.to_normalized(real)

    def _layout_spec(self, index, get_catalog):
        """A parameter belonging to this model's shared LAYOUT, not to itself.

        Only cabs need this, and they need it because the catalog
        under-describes them: most cab models list two mic selectors while the
        wire carries the whole `Default Cabsim` layout, so `Default Cabsim`'s own
        entry for that index is what applies.

        The taper it borrows is confirmed on three blocks in three different
        categories - a Cabsim Bass, a Cabsim Guitar, and Parallax, which is a
        Bass Overdrive carrying its own cab section. So it belongs to the cab
        SECTION wherever that appears, rather than to one cab model.

        Applying it across the category is still an EXTRAPOLATION, and saying
        otherwise would overstate it - all three of those blocks are mono, and
        86 of the 174 models in these categories are stereo. What the catalog
        adds is worth more than a fourth screen reading, though: of the 16 cab
        models that describe a LEVEL of their own, every single one carries
        ``MIN_CABSIM_DB`` and ``skew="4.9594844"``, and that 16 includes the
        stereo variants. So the device says the law is uniform wherever it says
        anything, and the remaining 158 models describe no LEVEL at all - which
        is exactly why they need this borrowing in the first place.
        """
        source = self.model(get_catalog)
        if source is None or source.category not in CABSIM_CATEGORIES:
            return None
        layout = get_catalog().get(CABSIM_LAYOUT)
        if layout is None or index >= len(layout.parameters):
            return None
        return layout.parameters[index]

    def refuse_if_unassignable(self, spec):
        """Raise :class:`ControlNotDrivable` if a pedal cannot be assigned here."""
        if spec.name in self.unassignable:
            raise ControlNotDrivable(
                control=f"{self.describe()}'s {spec.name}",
                evidence=(
                    "the device silently refuses a host expression assignment "
                    "here, in both directions - tested with four message shapes "
                    "including the one VOLUME accepts in the same session, a "
                    "Grid DELETE, and a write to bypass_expression."),
                workaround=(
                    "Assign it on the unit's touchscreen; the unit writes the "
                    "same field and the library reads it back."),
            )

    def describe(self) -> str:
        return type(self).__name__


class ChainTarget(ParamTarget):
    """A target that lives on a chain, and therefore has a ``row``.

    Everything except the tempo block. The split matters because a chain target
    builds its container inside ``preset.chains{row}`` and the tempo block does
    not - and because a signature that needs a row can say so.
    """

    row: int


class LaneControl(ChainTarget):
    """A row-addressed control: one per row, addressed by hash, no column.

    The lane's ends and its branch. Distinguished from :class:`Block` because
    there is exactly ONE of each per row - the device pads them one-per-row the
    way it pads ``models`` to eight column slots - so a column would mean
    nothing.
    """


class BranchControl(LaneControl):
    """A lane control that exists only on rows 0 and 2.

    A branch originates on an even row with its parallel lane on the row below,
    so rows 1 and 3 report these collections empty and a write addressed there
    does nothing. The row check is here rather than repeated in each subclass.
    """

    def __post_init__(self):
        _require_even_row(self.row, type(self).__name__.lower())


class PresetTarget(ParamTarget):
    """A target that hangs off the preset rather than off a chain.

    One member, and it is why :class:`ChainTarget` exists to be distinguished
    from: the tempo block has no row, so it has no scenes either.
    """

    supports_scenes = False


@dataclass(frozen=True)
class Block(ChainTarget):
    """One grid cell: where it is, and what is in it.

    This is BOTH the address of a block parameter and what
    :func:`~pyquadcortex.protocol.client.blocks` hands back when it reads a preset, and
    they are deliberately the same type - what you read is what you write to::

        for block in protocol.blocks(preset):
            qc.set_param(block, "GAIN", real=-6.0)

    ``chain.models[]``, keyed by COLUMN rather than by hash. ``model_id`` is the
    only part of an address that cannot be derived from the address itself -
    what lives in a cell is whatever the player put there - which is why naming
    a parameter on a hand-built ``Block`` needs it supplied, and why every other
    target needs nothing.
    """

    row: int
    column: int
    #: The block's catalog model id. Needed only to NAME a parameter rather than
    #: index one; ``blocks()`` always fills it in.
    model_id: int = None

    collection = "models"

    def container(self, msg):
        chain = msg.preset.chains.add()
        chain.row = self.row
        element = chain.models.add()
        element.column = self.column
        return element

    def describe(self):
        return f"the block at row {self.row} column {self.column}"


@dataclass(frozen=True)
class LaneOutput(LaneControl):
    """A row's Lane Output Control - VOLUME, PAN, MUTE, SOLO.

    ``chain.output_control[]``, hash-keyed, present on all four rows whether or
    not the row has any blocks. MUTE and SOLO refuse an expression assignment;
    see :data:`LANE_OUTPUT_UNASSIGNABLE`.
    """

    row: int

    collection = "output_control"
    model_id = LANE_OUTPUT_CONTROL
    unassignable = LANE_OUTPUT_UNASSIGNABLE

    def describe(self):
        return f"row {self.row}'s Lane Output Control"


@dataclass(frozen=True)
class LaneInput(LaneControl):
    """A row's Input Gate Control - the noise gate at the head of the row.

    ``chain.input_control[]``, hash-keyed, present on all four rows. All three
    of its controls take an expression pedal, BYPASS included.
    """

    row: int

    collection = "input_control"
    model_id = INPUT_GATE_CONTROL

    def describe(self):
        return f"row {self.row}'s Input Gate Control"


@dataclass(frozen=True)
class Mixer(BranchControl):
    """A row's Mixer - ``chain.mixer[]``, hash-keyed. Even rows only."""

    row: int

    collection = "mixer"
    model_id = MIXER

    def describe(self):
        return f"row {self.row}'s Mixer"


@dataclass(frozen=True)
class Splitter(BranchControl):
    """A row's splitter - ``chain.combined_splitter[]``. Even rows only.

    The odd one out: this container is sent with **no hash and no column**,
    because that is the shape the device itself broadcasts. Writes go to
    ``combined_splitter`` and NOT to ``splitter[]``, which is a read-only view.
    """

    row: int

    collection = "combined_splitter"
    model_id = SPLITTER

    def container(self, msg):
        chain = msg.preset.chains.add()
        chain.row = self.row
        return chain.combined_splitter.add()

    def describe(self):
        return f"row {self.row}'s Splitter"


@dataclass(frozen=True)
class Tempo(PresetTarget):
    """The preset's own TempoControl block - tempo, LED and metronome.

    Not on a chain at all: it hangs off the preset as ``tempoProgramData``, a
    repeated field with one entry. There is no row, so this target takes no
    arguments. ``GlobalTempo`` carries the DEVICE's copy of the same block; this
    addresses the preset's by construction.
    """

    collection = "tempoProgramData"
    model_id = TEMPO_CONTROL

    #: Tempo-menu parameter indices, mapped by using each control on the unit in a
    #: named order. The catalog DOES describe these (23 parameters for model 25000),
    #: but two of its names differ from the screen, so this map is what
    #: `Tempo.index_of` resolves a name through first.
    #:
    #: Index 4 is ONE control with THREE names: the unit's Tempo page labels it
    #: **MUTE**, the device catalog calls it ``START`` (a toggleButton), and the
    #: manual calls it PLAYBACK. **1.0 is audible, 0.0 is muted** - traced from
    #: the unit's own MUTE button, which writes 0.0 to mute and 1.0 to unmute, so
    #: the parameter is INVERTED against the label a player sees. There is no
    #: separate mute parameter: the unit's Tempo page has no start/stop control at
    #: all, and the transport is always running.
    #:
    #: This library published the polarity backwards for two releases. The
    #: mistake: propagation into a Looper X parameter named METRONOME MUTE proved
    #: the two are LINKED, and the polarity was then inferred from that name -
    #: which is the unreliable part, and the mirror turns out to be inverted too.
    #: Corrected by three independent measurements: all 17 factory presets hold
    #: 0.0 here at a normal volume and none clicks; writing 1.0 started the click
    #: on 36 field-built presets; and a capture of the unit's MUTE button shows
    #: mute-on writing 0.0.
    #:
    #: Index 7 is the screen's Subdivisions while the catalog calls it NOTELENGTH.
    #: Two earlier releases said indices 8 and 9 were absent from the catalog. They
    #: are not: ``SOUND`` (steps=6) and ``ROUTING`` (steps=5) are described there,
    #: at exactly those indices. Only NAMES ever disagreed.
    #:
    #: Indices 10 to 22 are the per-beat cells - see :attr:`QuadCortex.TEMPO_BEATS`. The
    #: preset carries a 24th parameter (index 23) the catalog does not describe,
    #: untouched by any Tempo-page control traced so far, so still unattributed.
    #: It is NOT a 14th beat: the beats stop at 22, which matches 13/4 being the
    #: largest beat count the unit offers.
    #:
    #: Index 1 - the catalog's TYPE - was NOT written by any control in the Tempo
    #: menu. The menu's MODE (global or per preset) broadcasts nothing at all, so it
    #: may be that, but nothing establishes it.
    NAMES = {
        "TEMPO": 0,
        "LED LIGHT": 2, "LED": 2,
        "VOLUME": 3,
        "START": 4, "PLAYBACK": 4,
        "PAN": 5,
        "TIME SIGNATURE": 6,
        "NOTELENGTH": 7, "SUBDIVISIONS": 7,
        "SOUND": 8,
        "ROUTING": 9,
    }

    def container(self, msg):
        element = msg.preset.tempoProgramData.add()
        element.hash = self.model_id
        return element

    def index_of(self, param, get_catalog, model=None):
        """As the base, plus the screen-name map and one refusal.

        ``MUTE`` is refused by NAME. Tempo parameter 4 is the control the unit
        labels MUTE, and it is INVERTED against that name - 1.0 is audible, 0.0
        is muted, traced from the unit's own MUTE button. So
        ``set_param(Tempo(), "MUTE", 1.0)`` would UNMUTE, and honouring the name
        silently is worse than refusing it.
        """
        if isinstance(param, str):
            key = param.strip().upper()
            if key == "MUTE":
                raise ValueError(
                    "tempo parameter 4 IS the control the unit labels MUTE - but "
                    "it is INVERTED against that name: 1.0 is audible and 0.0 is "
                    "muted (traced from the unit's own MUTE button). So "
                    "set_param(Tempo(), 'MUTE', 1.0) would UNMUTE, which is why "
                    "the name is refused here rather than silently honoured. Use "
                    "set_metronome_muted(True) to mute, set_metronome_running("
                    "True) to make it audible, or the raw name 'START'/'PLAYBACK'."
                )
            if key in self.NAMES:
                return super().index_of(self.NAMES[key], get_catalog, model)
        return super().index_of(param, get_catalog, model)

    def describe(self):
        return "the preset's TempoControl"

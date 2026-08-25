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
from pyquadcortex.protocol.units import bpm_to_tempo, db_to_lane_level

#: Catalog model ids for the containers whose model never varies.
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

        Takes the lazy ``get_catalog`` rather than a spec, because a target that
        knows its own measured span needs no catalog at all - see `Tempo`, whose
        TEMPO is bpm and never looks one up. ``spec`` is a hint from
        :meth:`index_of` when a name was resolved, so a name costs one fetch.
        """
        spec = spec if spec is not None else self.spec_at(index, get_catalog)
        if spec is None:
            raise ValueError(
                f"real= needs a parameter the catalog describes, and it does not "
                f"describe index {index} on {self.describe()} (that is real - the "
                f"wire carries more parameters than the catalog documents). Pass "
                f"value= with the normalized 0..1 instead."
            )
        return spec.to_normalized(real)

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


@dataclass(frozen=True)
class Block(ParamTarget):
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
class LaneOutput(ParamTarget):
    """A row's Lane Output Control - VOLUME, PAN, MUTE, SOLO.

    ``chain.output_control[]``, hash-keyed, present on all four rows whether or
    not the row has any blocks. MUTE and SOLO refuse an expression assignment;
    see :data:`LANE_OUTPUT_UNASSIGNABLE`.
    """

    row: int

    collection = "output_control"
    model_id = LANE_OUTPUT_CONTROL
    unassignable = LANE_OUTPUT_UNASSIGNABLE

    def normalize(self, index, real, get_catalog, spec=None):
        """VOLUME speaks dB, through the measured span rather than the catalog.

        Its catalog range is the placeholder ``0..1 "dB"``, so the catalog cannot
        convert it - but the true span IS measured at both ends, -40..+12 dB, so
        refusing here would be refusing something we know. Every other
        placeholder parameter still refuses, because their spans are not measured.
        """
        spec = spec if spec is not None else self.spec_at(index, get_catalog)
        if spec is not None and spec.name == "VOLUME":
            return db_to_lane_level(real)
        return super().normalize(index, real, get_catalog, spec)

    def describe(self):
        return f"row {self.row}'s Lane Output Control"


@dataclass(frozen=True)
class LaneInput(ParamTarget):
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
class Mixer(ParamTarget):
    """A row's Mixer - ``chain.mixer[]``, hash-keyed. Even rows only."""

    row: int

    collection = "mixer"
    model_id = MIXER

    def __post_init__(self):
        _require_even_row(self.row, "mixer")

    def describe(self):
        return f"row {self.row}'s Mixer"


@dataclass(frozen=True)
class Splitter(ParamTarget):
    """A row's splitter - ``chain.combined_splitter[]``. Even rows only.

    The odd one out: this container is sent with **no hash and no column**,
    because that is the shape the device itself broadcasts. Writes go to
    ``combined_splitter`` and NOT to ``splitter[]``, which is a read-only view.
    """

    row: int

    collection = "combined_splitter"
    model_id = SPLITTER

    def __post_init__(self):
        _require_even_row(self.row, "splitter")

    def container(self, msg):
        chain = msg.preset.chains.add()
        chain.row = self.row
        return chain.combined_splitter.add()

    def describe(self):
        return f"row {self.row}'s Splitter"


@dataclass(frozen=True)
class Tempo(ParamTarget):
    """The preset's own TempoControl block - tempo, LED and metronome.

    Not on a chain at all: it hangs off the preset as ``tempoProgramData``, a
    repeated field with one entry. There is no row, so this target takes no
    arguments. ``GlobalTempo`` carries the DEVICE's copy of the same block; this
    addresses the preset's by construction.
    """

    collection = "tempoProgramData"
    model_id = TEMPO_CONTROL
    #: The tempo block is not on the grid, so it has no per-scene values.
    supports_scenes = False

    #: The unit's screen names for the tempo block's parameters, and the aliases
    #: worth accepting. The catalog names some of these differently, which is why
    #: this map exists rather than relying on `Model.parameter`.
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

    def normalize(self, index, real, get_catalog, spec=None):
        """TEMPO speaks bpm, through the measured 40..240 span.

        Same situation as the lane VOLUME: the catalog publishes ``0..1 "BPM"``,
        a placeholder, and the span was measured against the screen instead.
        """
        if index == 0:
            return bpm_to_tempo(real)
        return super().normalize(index, real, get_catalog, spec)

    def describe(self):
        return "the preset's TempoControl"

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

    def container(self, msg):
        element = msg.preset.tempoProgramData.add()
        element.hash = self.model_id
        return element

    def describe(self):
        return "the preset's TempoControl"

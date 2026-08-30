"""What sits in a grid cell, as the screen shows it.

A `Block` is reached through a :class:`~pyquadcortex.device.grid.BlockGrid`, and
which grid you reached it through decides which SCENE its answers are about.
That is the design doc's grid duality, and it follows from how the unit works:
a block is placed once per preset, but its bypass - and, from story #13, its
scene-following parameters - differ per scene.

So two grids hand back two handles on one cell. Scene-invariant facts (where it
is, which virtual device is in it) come from the same underlying payload and
cannot disagree; scene-varying ones resolve through the grid's own scene. Two
handles on the same cell compare EQUAL, and ``is`` is not the test - see
:meth:`Block.__eq__`.

Nothing here does coordinate arithmetic. Every number came from
:mod:`pyquadcortex.device.translate`, which is the only place a wire index
becomes a screen one.
"""

from dataclasses import dataclass

from pyquadcortex import protocol
from pyquadcortex.device import translate

#: Which physical input feeds a row, in the unit's own vocabulary. The protocol
#: layer's enum, renamed for the screen: this is a PORT rather than a coordinate,
#: so there is nothing to convert.
InputSource = protocol.Input

#: Where a row goes: a jack, a send, USB, another row, or the Multi-Out.
OutputDestination = protocol.Output


@dataclass(frozen=True)
class PedalAssignment:
    """An expression pedal on one of a block's knobs, as the screen shows it.

    Read from the parameter's own row and slot, numbered from 1. ``minimum``
    and ``maximum`` are the two ends of the sweep in the knob's own units where
    the unit describes them, so a volume assignment reads in dB.

    **The pair is not ordered.** ``minimum`` above ``maximum`` REVERSES the
    pedal, which is how the manual describes inverting a parameter, so sorting
    them would throw the setting away. :attr:`reversed` is the question worth
    asking.

    **Without a device attached there is no catalog**, and a knob's scale comes
    from the unit. Then - and for a knob the catalog does not describe, or one
    whose bounds nobody has measured - ``units`` is empty and the two ends stay
    the device's own 0..1. That is said rather than guessed, and
    :attr:`in_real_units` is how to ask which you have.
    """

    row: int
    slot: int | None
    parameter: str | None
    pedal: int
    minimum: float
    maximum: float
    units: str

    @property
    def reversed(self) -> bool:
        """Whether the HEEL is the loud end - ``minimum`` above ``maximum``."""
        return float(self.minimum) > float(self.maximum)

    @property
    def in_real_units(self) -> bool:
        """Whether the sweep reads in the knob's own units.

        False where no catalog was available to ask, which is a real state
        rather than an error: a preset can be read with no unit attached.
        """
        return not isinstance(self.minimum, protocol.Encoded)

    def __repr__(self) -> str:
        where = f"row {self.row}" + (f" slot {self.slot}" if self.slot else "")
        what = self.parameter or "an undescribed parameter"
        unit = f" {self.units}" if self.units else ""
        return (f"<EXP {self.pedal} on {what} ({where}): "
                f"{float(self.minimum):g}{unit} to {float(self.maximum):g}{unit}>")


@dataclass(frozen=True)
class VirtualDevice:
    """An amp, a cab, a pedal or a capture - what the parameter editor calls the
    VIRTUAL DEVICE NAME.

    Named for the screen rather than the wire. The protocol layer calls this a
    *model*, and so does its ``ModelCatalog``, but *model* is also this project's
    word for the domain model, and the unit's own words are VIRTUAL DEVICE LIST
    and VIRTUAL DEVICE NAME (``docs/domain-model.md`` section 5).
    """

    id: int          #: the catalogue id the wire carries
    name: str        #: as shown on the unit, e.g. "Brit 2203"
    category: str    #: AMP, CAB, DELAY and so on


class Block:
    """One cell of the grid.

    Built by the grid, never directly. ``grid`` is what it reads through, and it
    is the grid that decides which scene the scene-varying answers are about.
    """

    def __init__(self, grid, *, row: int, slot=None):
        self._grid = grid
        self._row = row
        self._slot = slot

    @property
    def row(self) -> int:
        """Which row this block is on, 1 to 4, as the screen numbers them."""
        return self._row

    @property
    def slot(self):
        """Which of the row's eight slots this block is in, 1 to 8.

        ``None`` for the input and output blocks, which sit outside the eight -
        they are the ends of the row rather than cells in it.
        """
        return self._slot

    def __eq__(self, other) -> bool:
        """Two handles on the same cell of the same preset are equal.

        **``is`` is deliberately not the test.** ``preset.blocks[1, 3]`` and
        ``scene.blocks[1, 3]`` are two BINDINGS of one cell, and the whole point
        of a binding is that the scene-varying answers may differ - so they
        cannot be one object. What they share is the cell of one preset, which
        is what this compares. Within ONE grid the same handle comes back, so
        ``grid = preset.blocks`` then ``grid[1, 3] is grid[1, 3]`` holds -
        but ``preset.blocks[1, 3] is preset.blocks[1, 3]`` does NOT, because
        ``preset.blocks`` builds a fresh grid every time it is read.
        """
        if type(other) is not type(self):
            return NotImplemented
        return self._key() == other._key()

    def __hash__(self) -> int:
        return hash(self._key())

    def _key(self):
        """What makes two handles the same cell.

        The PRESET, not the payload. An earlier version keyed on the identity of
        the wire payload, which the model replaces on every re-read - so a block
        put in a set was silently lost the moment somebody touched the unit, and
        `hash()` reached through to the cache and could issue a device read with
        a fifteen-second timeout. Neither belongs in an equality check.
        """
        return (type(self).__name__, id(self._grid.preset), self._row, self._slot)

    def __repr__(self) -> str:
        where = f"row {self._row}"
        if self._slot is not None:
            where += f" slot {self._slot}"
        return f"<{type(self).__name__} {where}>"


class DeviceBlock(Block):
    """A virtual device placed in one of a row's eight slots."""

    def __init__(self, grid, *, row: int, slot: int, device_id: int):
        super().__init__(grid, row=row, slot=slot)
        self._device_id = device_id

    @property
    def device(self) -> VirtualDevice:
        """Which virtual device is in this cell.

        The catalogue comes FROM the unit, so it covers purchased plugin devices
        and the player's own Neural Captures. An id it does not have raises the
        catalogue's own ``KeyError``, which already says what is wrong - a
        placeholder name here would be the model guessing.
        """
        found = self._grid.catalog[self._device_id]
        return VirtualDevice(id=found.id, name=found.name,
                             category=found.category)

    @property
    def pedals(self) -> tuple:
        """The expression pedals assigned to this block's knobs.

        Empty for most blocks - a pedal is a deliberate assignment, and a
        preset usually has none or one. See :class:`PedalAssignment`.

        A pedal that BYPASSES this block is a different feature on a different
        field and is not here: it is not a pedal on one of its knobs.
        """
        return tuple(a for a in self._grid.pedals
                     if a.row == self.row and a.slot == self.slot)

    @property
    def bypassed(self) -> bool:
        """Whether this block is bypassed IN THIS GRID'S SCENE.

        Bypass is scene-varying, so the answer depends on which grid you reached
        this block through: ``preset.blocks`` follows whichever scene is active,
        and ``scene.blocks`` is pinned to its own.
        """
        return translate.block_bypassed(self._grid.wire, self._row, self._slot,
                                        self._grid.scene)


class InputBlock(Block):
    """The left-hand end of a row: what feeds it."""

    def __init__(self, grid, *, row: int):
        super().__init__(grid, row=row)

    @property
    def source(self):
        """Which physical input feeds this row, or ``None`` if unstated.

        ``InputSource.EMPTY`` is a real answer meaning "not fed from a physical
        jack", which is the normal state of any row that is not an input row -
        factory "Brit 2203" has six blocks on a row reporting EMPTY. ``None``
        means the preset did not carry the field at all, which is a different
        thing.

        The NOISE REDUCTION / BYPASS / INPUT GAIN controls on this block are
        parameters, and parameters are story #13.
        """
        return translate.row_input(self._grid.wire, self._row)


class LaneOutput:
    """The manual's LANE OUTPUT CONTROL: VOLUME, PAN, MUTE and SOLO for a row.

    Present or absent is all this reports today. Its four controls are
    parameters and arrive with story #13; what matters here is that a row routed
    to another row HAS no lane output, exactly as the screen shows - see
    :attr:`OutputBlock.lane`.
    """

    def __init__(self, grid, *, row: int):
        self._grid = grid
        self._row = row

    @property
    def row(self) -> int:
        return self._row

    def __repr__(self) -> str:
        return f"<LaneOutput row {self._row}>"


class OutputBlock(Block):
    """The right-hand end of a row: where it goes."""

    def __init__(self, grid, *, row: int):
        super().__init__(grid, row=row)

    @property
    def destination(self):
        """Where this row goes, or ``None`` if the preset did not say.

        A jack, a send, USB, the Multi-Out, or another row. WHICH row it feeds
        is not reported: the enum names say 3 and 4 and the unit has four rows,
        so screen numbering is the obvious reading, but obvious is not confirmed
        and a wrong answer there is the silent kind.
        """
        return translate.row_output(self._grid.wire, self._row)

    @property
    def lane(self):
        """This row's LANE OUTPUT CONTROL, or ``None`` when it feeds another row.

        Absent rather than empty, because that is what the screen does: a row
        routed into another row has no lane output to show. Mirroring it keeps
        the caller from reading a volume that does not exist.

        Raises if the preset did not say where the row goes at all. ``None``
        there would mean "no lane output", which is a positive claim about a row
        whose routing the unit never stated.
        """
        destination = self.destination
        if destination is None:
            # Not the same thing as feeding a row, and it must not read as it.
            # The preset carried no out_portid at all, so what this row does is
            # unknown rather than known-to-have-no-lane. `row_output` is careful
            # to keep the two apart and this would have thrown that away.
            raise RuntimeError(
                f"this preset does not say where row {self._row} goes, so "
                f"whether it has a lane output cannot be answered. Read "
                f"output.destination to see that the unit said nothing.")
        if translate.routes_to_a_row(destination):
            return None
        return LaneOutput(self._grid, row=self._row)


class SplitterBlock(Block):
    """Where a row branches into its parallel path.

    Its position is not on the block. The wire carries a splitter with no column
    at all; where the branch starts lives on the chain, which is why
    ``translate.branches`` is what finds it.

    TYPE, STEREO, BALANCE, LEVEL TO A/B, FREQUENCY and MODE are parameters and
    arrive with story #13. So does MUTE, which is worth a note: the manual lists
    a MUTE under SPLITTER PARAMETERS and another under MIXER PARAMETERS, and on
    the unit they are ONE control - muting the splitter shows the mixer's MUTE
    already engaged.
    """


class MixerBlock(Block):
    """Where a parallel path rejoins its row.

    Absent when the branch never rejoins, which is an ordinary shape: the manual
    places the (S) and (M) tokens independently, and factory "Strat Ambience"
    (05B) branches without ever recombining.

    LEVEL A/B, PAN A/B, PHASE and MIXER LEVEL are parameters, story #13, as is
    the MUTE it shares with the splitter.
    """

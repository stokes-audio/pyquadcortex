"""The Grid: four rows of eight slots, and the two ways to look at them.

A :class:`BlockGrid` is a BINDING of the grid to a scene. ``preset.blocks`` is
live-bound - it reads through whichever scene is active, like the touchscreen
itself - and ``scene.blocks`` is fixed to its own. Both look at one underlying
payload, so which device is placed where cannot disagree between them; only the
scene-varying answers differ, which is the point.

Writing through a grid bound to a scene that is NOT active is refused. The unit
has no way to do it - you switch scenes first - and doing that silently would
change what comes out of the outputs and leave it changed
(``docs/domain-model.md`` section 10). Reading through such a grid is fine.

Nothing here does coordinate arithmetic; :mod:`pyquadcortex.device.translate`
owns all of it.
"""

import typing

from pyquadcortex.device import translate
from pyquadcortex.device.blocks import (DeviceBlock, InputBlock, MixerBlock,
                                        OutputBlock, SplitterBlock)
from pyquadcortex.device.errors import InactiveSceneError


class BlockGrid:
    """The grid, bound to a scene. ``preset.blocks`` or ``scene.blocks``.

    Args:
        preset: what this reads through. Anything carrying ``wire`` (the payload
            the unit sent), ``catalog`` and ``active_scene``.
        scene: the scene to pin to, or ``None`` for live-bound - following
            whichever scene is active at the moment of each read.
    """

    def __init__(self, preset, scene=None):
        self._preset = preset
        self._scene = (None if scene is None
                       else translate.SceneLetter(translate.scene_to_wire(scene).name))
        #: Handles already built, keyed by cell. Dropped whenever the payload
        #: underneath changes - see :meth:`_cells`.
        self._handles = {}
        self._built_from = None

    # -- what a block reads through -------------------------------------------

    @property
    def wire(self):
        """The preset payload underneath. Re-read from the cache each time, so a
        grid reflects what the unit is doing now rather than when it was made."""
        return self._preset.wire

    @property
    def catalog(self):
        """The unit's own catalogue of virtual devices."""
        return self._preset.catalog

    @property
    def scene(self):
        """Which scene this grid's answers are about.

        Resolved on every access rather than stored, which is what makes a
        live-bound grid live: storing the letter at construction would pin it
        silently the first time somebody built one.
        """
        if self._scene is None:
            return self._preset.active_scene
        return self._scene

    # -- reading ---------------------------------------------------------------

    def _cells(self) -> dict:
        """Every occupied cell of the current payload, as handles.

        Rebuilt whenever the payload changes. A handle memoized against an older
        payload would go on describing the block that USED to be in that cell,
        which is the quiet kind of wrong this library exists to avoid - and the
        model re-reads the whole preset after every edit, so it happens often.
        """
        wire = self.wire
        if self._built_from is not wire:
            self._handles = {
                (placed.row, placed.slot): DeviceBlock(
                    self, row=placed.row, slot=placed.slot,
                    device_id=placed.device_id)
                for placed in translate.placed_blocks(wire)
            }
            self._built_from = wire
        return self._handles

    def __getitem__(self, where):
        """``blocks[row, slot]``, or ``None`` where the cell is empty."""
        if not isinstance(where, tuple) or len(where) != 2:
            raise TypeError(
                f"a cell is addressed by row and slot, as blocks[1, 3]; "
                f"got {where!r}")
        row, slot = where
        # Validated through the boundary even though the lookup would simply
        # miss: blocks[1, 99] meaning "empty" would read as a fact about the
        # preset rather than as a coordinate no screen shows.
        translate.row_to_wire(row)
        translate.slot_to_wire(slot)
        return self._cells().get((row, slot))

    def __iter__(self) -> typing.Iterator:
        """The OCCUPIED cells only.

        Deliberately different from ``row.slots``, which reports all eight
        including the empty ones. Iterating a grid answers "what is on this
        preset"; iterating a row's slots answers "what is in each of its cells".
        """
        return iter(self._cells().values())

    def __len__(self) -> int:
        """How many cells hold something."""
        return len(self._cells())

    # -- writing ---------------------------------------------------------------

    @property
    def writable(self) -> bool:
        """Whether a write through this grid would reach the unit.

        False only for a grid pinned to a scene that is not active. A live-bound
        grid is always writable, because it follows the active scene and so
        cannot be pointed at the wrong one.
        """
        return self._scene is None or self._scene == self._preset.active_scene

    def check_writable(self) -> None:
        """Raise unless a write through this grid could reach the unit.

        Public because it is the precondition every write through a grid runs,
        and this release ships the guard before the writes it guards - editing
        is M2. A caller can ask before attempting one, and the refusal names the
        step that fixes it.

        Raises:
            InactiveSceneError: if this grid is pinned to a scene that is not
                active.
        """
        if self.writable:
            return
        raise InactiveSceneError(
            f"this grid is bound to scene {self._scene}, which is not the "
            f"active one ({self._preset.active_scene}). The unit cannot write "
            f"to a scene it is not in, so switch to it first with "
            f"scene.activate() - reading through this grid is fine.")

    def __repr__(self) -> str:
        binding = "live" if self._scene is None else f"scene {self._scene}"
        return f"<BlockGrid {binding}, {len(self)} block(s)>"


class Slots:
    """A row's eight cells, as the manual counts them.

    All eight, always, whether or not they hold anything - which is what the
    screen shows, and the opposite of iterating a :class:`BlockGrid`.
    """

    def __init__(self, grid: BlockGrid, row: int):
        self._grid = grid
        self._row = row

    def __getitem__(self, slot: int):
        """``row.slots[3]`` - the block in that cell, or ``None``."""
        return self._grid[self._row, slot]

    def __iter__(self) -> typing.Iterator:
        return (self._grid[self._row, slot] for slot in translate.SLOTS)

    def __len__(self) -> int:
        return len(translate.SLOTS)

    def __repr__(self) -> str:
        held = sum(1 for block in self if block is not None)
        return f"<Slots row {self._row}, {held} of {len(self)} filled>"


class Row:
    """One row of the grid, numbered 1 to 4 as the screen numbers them."""

    def __init__(self, grid: BlockGrid, number: int):
        # Not validated here. Every way to reach a Row goes through the
        # boundary first - `Rows.__getitem__` checks the number and
        # `translate.path_b_of` produces one - so a check here would be a second
        # account of what a row is.
        self._grid = grid
        self._number = number

    @property
    def number(self) -> int:
        return self._number

    @property
    def input(self) -> InputBlock:
        """The left-hand end of this row: what feeds it."""
        return InputBlock(self._grid, row=self._number)

    @property
    def output(self) -> OutputBlock:
        """The right-hand end of this row: where it goes."""
        return OutputBlock(self._grid, row=self._number)

    @property
    def slots(self) -> Slots:
        """This row's eight cells, empty ones included."""
        return Slots(self._grid, self._number)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self._number}>"


class SplittableRow(Row):
    """A row a branch can start on: rows 1 and 3 only.

    A split belongs to a PAIR of rows and only the upper one can start it - the
    manual is explicit, "Route audio from Rows 1 or 3 (Path A) to Rows 2 or 4
    (Path B)". So this row IS Path A and :attr:`path_b` IS Path B; no separate
    pair object is needed and no row is reachable by two names.

    Because rows 2 and 4 are a plain :class:`Row` with no ``splitter`` at all,
    ``rows[2].create_split()`` is something an editor rejects rather than
    something that raises when it runs. That catch needs a LITERAL index: a
    computed one resolves to ``Row | SplittableRow``, so narrow it or accept a
    runtime error. Better than no check, and not absolute.
    """

    def _branch(self):
        for branch in translate.branches(self._grid.wire):
            if branch.row == self._number:
                return branch
        return None

    @property
    def splitter(self):
        """Where this row branches, or ``None`` if it does not."""
        branch = self._branch()
        if branch is None:
            return None
        return SplitterBlock(self._grid, row=self._number, slot=branch.at)

    @property
    def mixer(self):
        """Where the parallel path rejoins, or ``None`` if it never does.

        A branch need not rejoin: the manual allows Path B to reach different
        output blocks instead, and the (S) and (M) tokens are placed
        independently. Factory "Strat Ambience" (05B) branches and never
        recombines.
        """
        branch = self._branch()
        if branch is None or branch.rejoins_at is None:
            return None
        return MixerBlock(self._grid, row=self._number, slot=branch.rejoins_at)

    @property
    def path_b(self) -> Row:
        """The row carrying this row's parallel path: 2 for row 1, 4 for row 3.

        A plain :class:`Row`, because Path B cannot itself branch.
        """
        return Row(self._grid, translate.path_b_of(self._number))


class Rows:
    """The grid's four rows. ``preset.rows[1]`` to ``preset.rows[4]``."""

    def __init__(self, grid: BlockGrid):
        self._grid = grid

    @typing.overload
    def __getitem__(self, row: typing.Literal[1, 3]) -> SplittableRow: ...

    @typing.overload
    def __getitem__(self, row: typing.Literal[2, 4]) -> Row: ...

    def __getitem__(self, row: int) -> Row:
        """One row, 1 to 4.

        Rows 1 and 3 come back as :class:`SplittableRow` and rows 2 and 4 as a
        plain :class:`Row`, so a type checker can reject ``rows[2].splitter``
        before it runs - on a literal index. See :class:`SplittableRow`.
        """
        # Refused through the boundary, so "row 5" is one message wherever it
        # is asked.
        translate.row_to_wire(row)
        if row in translate.SPLITTABLE_ROWS:
            return SplittableRow(self._grid, row)
        return Row(self._grid, row)

    def __iter__(self) -> typing.Iterator[Row]:
        return (self[row] for row in translate.ROWS)

    def __len__(self) -> int:
        return len(translate.ROWS)

    def __repr__(self) -> str:
        return f"<Rows 1 to {len(self)}>"

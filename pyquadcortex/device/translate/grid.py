"""A preset the wire sent, read in the numbers the screen shows.

The protocol layer reports a grid in its own coordinates: rows 0 to 3, columns 0
to 7, scenes 0 to 7, and a branch whose columns live on the chain rather than on
the splitter block. Everything here turns one of those into what the touchscreen
shows, which is why it sits inside the translation boundary rather than beside
the model objects that use it.

``tests/test_translation.py`` refuses every other module in the package the
protocol-layer readers used here - ``blocks``, ``splits`` and ``bypass_state``
are all on its allowlist - so this is the only place a wire coordinate becomes a
screen one.

Nothing here decides anything. It reads what the unit sent and renumbers it; the
model objects in :mod:`pyquadcortex.device.grid` decide what to do with that.
"""

from dataclasses import dataclass

from pyquadcortex import protocol
from pyquadcortex.device.translate.coordinates import (row_from_wire,
                                                       row_to_wire,
                                                       slot_from_wire,
                                                       slot_to_wire)
from pyquadcortex.device.translate.letters import scene_to_wire


@dataclass(frozen=True)
class PlacedBlock:
    """One occupied cell: where it is on screen, and what is in it."""

    row: int          #: 1 to 4, as the screen numbers them
    slot: int         #: 1 to 8, the manual's word for a cell in a row
    device_id: int    #: the catalogue id of the virtual device placed here


@dataclass(frozen=True)
class Branch:
    """Where a row splits into a parallel path, in screen numbers.

    :attr:`rejoins_at` is ``None`` for a branch that never recombines. The
    manual places the (S) and (M) tokens independently, so that is an ordinary
    shape rather than a broken one - factory "Strat Ambience" (05B) branches and
    never rejoins.
    """

    row: int                   #: the row the branch starts on, 1 or 3
    at: int                    #: the slot holding the splitter, 1 to 8
    rejoins_at: int | None     #: the slot holding the mixer, or None
    path_b: int                #: the row carrying path B, 2 or 4


#: The rows a branch can start on, as the screen numbers them. The wire allows a
#: branch only on its rows 0 and 2 - see :func:`pyquadcortex.protocol.splits` -
#: and those two are these two. Derived rather than written as ``(1, 3)`` so the
#: numbering has exactly one account of itself.
SPLITTABLE_ROWS = (row_from_wire(0), row_from_wire(2))


def path_b_of(row: int) -> int:
    """The row carrying ``row``'s parallel path: 2 for row 1, 4 for row 3.

    Refuses rows 2 and 4, because a branch cannot start there and a row that
    cannot branch has no path B. The model expresses the same rule as a type -
    ``rows[2]`` is a plain ``Row`` with no ``path_b`` at all - and this is the
    runtime half, for a row number that was computed rather than written.
    """
    if row not in SPLITTABLE_ROWS:
        raise ValueError(
            f"only rows 1 or 3 start a branch, so only they have a path B; "
            f"got {row!r}")
    return row_from_wire(row_to_wire(row) + 1)


def placed_blocks(binary_preset) -> tuple:
    """Every occupied cell, screen-numbered.

    Which cells are occupied is :func:`pyquadcortex.protocol.blocks`' question,
    and it is not the naive one: every row reports all eight slots whether or
    not they hold anything, so ``len(chain.models)`` is 8 on an empty row. This
    adds the numbering and nothing else.
    """
    return tuple(
        PlacedBlock(row=row_from_wire(block.row),
                    slot=slot_from_wire(block.column),
                    device_id=block.model_id)
        for block in protocol.blocks(binary_preset))


def branches(binary_preset) -> tuple:
    """Every parallel path in this preset, screen-numbered.

    The columns come from the chain's ``split_control_points`` rather than from
    the splitter block, which carries no column at all -
    :func:`pyquadcortex.protocol.splits` is where that is worked out.
    """
    return tuple(
        Branch(row=row_from_wire(split.row),
               at=slot_from_wire(split.split_column),
               rejoins_at=(slot_from_wire(split.mix_column)
                           if split.rejoins else None),
               path_b=row_from_wire(split.lane_row))
        for split in protocol.splits(binary_preset))


def _chain(binary_preset, row: int):
    """The chain for a screen row.

    Matched on the chain's own ``row`` where it carries one and by position
    otherwise, which is :func:`pyquadcortex.protocol.blocks`' rule. Following a
    different rule here would let the two accounts disagree about which row a
    chain is, and the fixture read off a real unit carries no ``row`` at all, so
    the fallback is the normal path rather than the exceptional one.
    """
    wanted = row_to_wire(row)
    for position, chain in enumerate(binary_preset.chains):
        here = chain.row if protocol.field_present(chain, "row") else position
        if here == wanted:
            return chain
    raise LookupError(
        f"this preset carries no chain for row {row}; it has "
        f"{len(binary_preset.chains)}")


def row_input(binary_preset, row: int):
    """The port feeding a screen row, or ``None`` if the preset does not say.

    ``Input.EMPTY`` is a real answer and means "not fed from a physical jack",
    which is the normal state of any row that is not an input row - factory
    "Brit 2203" has six blocks on a row reporting EMPTY. ``None`` means the
    field was absent, which is a different thing and is why the two are not
    collapsed.
    """
    chain = _chain(binary_preset, row)
    if not protocol.field_present(chain, "in_portid"):
        return None
    return protocol.Input(chain.in_portid)


def row_output(binary_preset, row: int):
    """Where a screen row goes, or ``None`` if the preset does not say."""
    chain = _chain(binary_preset, row)
    if not protocol.field_present(chain, "out_portid"):
        return None
    return protocol.Output(chain.out_portid)


#: The three destinations that feed another row instead of a jack. On screen a
#: row routed this way has no LANE OUTPUT CONTROL, which is what the model
#: mirrors by leaving ``lane`` absent.
_ROW_DESTINATIONS = frozenset({
    protocol.Output.NEXT_ROW_3,
    protocol.Output.NEXT_ROW_4,
    protocol.Output.NEXT_ROW_3_4,
})


def routes_to_a_row(destination) -> bool:
    """Whether this destination feeds another row rather than a jack.

    WHICH row it feeds is deliberately not reported. The names say 3 and 4, the
    unit has four rows, and the fixture read off a real unit routes its top row
    to ``NEXT_ROW_3`` - so screen numbering is the obvious reading. Obvious is
    not confirmed, and a wrong answer here is the silent kind that reads back
    perfectly. So the model answers the question it can, which is the one that
    decides whether there is a lane output to show.
    """
    return destination in _ROW_DESTINATIONS


def block_bypassed(binary_preset, row: int, slot: int, scene) -> bool:
    """Whether the block in a cell is bypassed in ``scene``.

    The wire keys the eight bypass slots by scene INDEX; this takes the letter,
    and it is the only place that mapping happens.

    One lookup covers both cases. With scene mode off the block has a single
    bypass state and the unit keeps all eight slots consistent - a global write
    updates every one of them, measured - so reading the asked-for scene is
    right either way and needs no special case. Reading slot zero instead would
    be right for the same reason and wrong the moment scene mode is on, which is
    why it is not written that way.
    """
    state = protocol.bypass_state(
        binary_preset, protocol.Block(row_to_wire(row), slot_to_wire(slot)))
    index = scene_to_wire(scene)
    if index >= len(state.scenes):
        raise LookupError(
            f"the preset stores {len(state.scenes)} bypass slots for row {row} "
            f"slot {slot}, so scene {scene} has none - the unit stores eight")
    return state.scenes[index]


def scene_name(binary_preset, scene) -> str:
    """A scene's label, by letter, as the unit would show it.

    The unit stores a single space for "this scene has no label" rather than an
    empty string (:data:`pyquadcortex.protocol.SCENE_UNLABELLED`), and shows the
    letter on screen instead of a name. So a blank label reads back here as no
    name at all, which is what a caller writing ``if scene.name:`` means and
    what ``label == ""`` would get wrong.
    """
    index = scene_to_wire(scene)
    labels = binary_preset.scene_labels
    if index >= len(labels):
        raise LookupError(
            f"this preset carries {len(labels)} scene labels, so scene {scene} "
            f"has none - the unit stores eight")
    label = labels[index]
    return "" if not label.strip() else label

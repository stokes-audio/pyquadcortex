"""The one place a screen value becomes a wire value, and back.

The model speaks what the touchscreen shows: rows 1 to 4, slots 1 to 8, scenes
and footswitches as letters, levels in dB, the tuner in Hz, the tempo in bpm. The
wire speaks zero-based indexes and raw scales. Every conversion between the two
lives here, and nowhere else in :mod:`pyquadcortex.device` - design principle 5
in ``docs/domain-model.md``.

**Why one place rather than a convention.** The protocol layer's own header says
it plainly: rows are zero-based, "getting this wrong is quiet rather than loud -
an edit lands on a real row, just not the one intended, and it reads back
perfectly". There is no error, no wrong-looking value, and no complaint from the
unit. A ``- 1`` written in the wrong place is therefore invisible until someone
plays the preset. Collecting the arithmetic behind one boundary makes it
reviewable in one place, and ``tests/test_translation.py`` proves the rest of the
model package contains none of it.

**Why a package.** It started as one module and grew past the size where a reader
can hold it. The split is by responsibility - guards, coordinates, letters,
addresses, display units, and reading a whole preset in screen coordinates - and
every public name is re-exported here, so ``translate.row_to_wire(...)`` still
resolves and no caller changed.

The exemption the structural tests grant is now a DIRECTORY, which is a bigger
hole than a file, so ``tests/test_translation.py`` names this package's modules
explicitly. A module added here has to come through that list with a reason
beside it; putting the arithmetic in an unlisted ``translate/whatever.py`` fails
a test rather than passing quietly.

Nothing here talks to a device. These are pure functions and value types, so they
are cheap to test exhaustively, which is the point.

Two words carry two meanings in this package, both of them the unit's own:

* a **slot** is one of the eight cells in a grid row (``row.slots[3]``), and it
  is also a preset's place in a setlist ("28C"). The design doc uses both. The
  grid sense converts with :func:`slot_to_wire`; the setlist sense with
  :func:`slot_to_position` and :class:`PresetAddress`.
* a **position** is the letter part of a preset address ("C") to the model, and
  the linear index of that address (218) to the wire.
"""

from pyquadcortex.device.translate.addresses import (PresetAddress,
                                                     position_to_slot,
                                                     slot_to_position)
from pyquadcortex.device.translate.coordinates import (ROWS, SLOTS,
                                                       row_from_wire,
                                                       row_to_wire,
                                                       slot_from_wire,
                                                       slot_to_wire)
from pyquadcortex.device.translate.letters import (FootswitchLetter, SceneLetter,
                                                   footswitch_from_wire,
                                                   footswitch_to_wire,
                                                   scene_from_wire,
                                                   scene_to_wire)
from pyquadcortex.device.translate.units import (CONCERT_A_HZ, bpm_to_tempo,
                                                 db_to_input_level,
                                                 db_to_lane_level,
                                                 hold_timing_ms,
                                                 hz_to_tuner_reference,
                                                 input_level_db, lane_level_db,
                                                 ms_to_hold_timing, tempo_bpm,
                                                 tuner_reference_hz)

#: Only the three value types are re-exported from :mod:`pyquadcortex`; a caller
#: holds those. The conversions are the seam's own business and are reached as
#: ``translate.row_to_wire(...)`` from inside the model.
__all__ = [
    "ROWS", "SLOTS", "CONCERT_A_HZ",
    "FootswitchLetter", "SceneLetter", "PresetAddress",
    "row_to_wire", "row_from_wire", "slot_to_wire", "slot_from_wire",
    "footswitch_to_wire", "footswitch_from_wire",
    "scene_to_wire", "scene_from_wire",
    "slot_to_position", "position_to_slot",
    "input_level_db", "db_to_input_level",
    "lane_level_db", "db_to_lane_level",
    "tempo_bpm", "bpm_to_tempo",
    "tuner_reference_hz", "hz_to_tuner_reference",
    "hold_timing_ms", "ms_to_hold_timing",
]

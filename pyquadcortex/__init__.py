"""Control a Neural DSP Quad Cortex over USB from Python.

    import pyquadcortex

    with pyquadcortex.connect() as qc:
        print(qc.version())
        qc.switch_scene(1)

See the readme for a tour, and ``docs/`` for the protocol and internals.
"""

import logging

__version__ = "0.38.0"

# A library must not write to the application's console. Without a handler,
# Python's handler of last resort prints WARNING and above to stderr, so a
# routine transport message would appear in a caller's output uninvited.
# Applications that want our logs can configure the "pyquadcortex" logger.
logging.getLogger(__name__).addHandler(logging.NullHandler())

from pyquadcortex.transport import DeviceLostError
from pyquadcortex.client import (SCENE_UNLABELLED, UNITY_LEVEL,
                                 db_to_input_level, input_level_db,
                                 USER_SETLIST_ROOT, Block,
                                 BlockRefused, Folder, MidiOut, QuadCortex, Split,
                                 StompAssignment, blocks, field_present,
                                 free_rows, row_status, RowStatus, params_equal, GAIN_REDUCTION_PARAM,
                                 bypass_state, BypassState, param_state, ParamState, input_chain_rows, midi_out, option_at,
                                 option_value,
                                 param_options, position_to_slot,
                                 beats, tempo_params,
                                 preset_load_midi_out, slot_to_position, splits,
                                 stomp_assignments)  # noqa: E402
from pyquadcortex.enums import (BROKEN_MODE_VALUE, Footswitch, FootswitchMode,
                                HYBRID_MODES, Input, Instrument, MidiOutType,
                                describe_mode, hybrid_mode, PowerOption, RecallReason,
                                ExpressionBypassMode, GlobalEQFilter, LooperState,
                                MetronomeBeat, MetronomeRouting,
                                MetronomeSound, MidiSource,
                                Output, Scene,
                                SceneBypassBehavior, Setlist, TempoSubdivision,
                                TimeSignature)
from pyquadcortex.session import DeviceNotFoundError, connect, open_device
from pyquadcortex import models  # noqa: E402  generated factory-block constants
from pyquadcortex.catalog import Model, ModelCatalog, Parameter  # noqa: E402

__all__ = [
    "__version__",
    "connect",
    "open_device",
    "DeviceNotFoundError",
    "QuadCortex",
    "Block",
    "blocks",
    "Split",
    "splits",
    "free_rows",
    "row_status",
    "params_equal",
    "bypass_state",
    "BypassState",
    "param_state",
    "ParamState",
    "GAIN_REDUCTION_PARAM",
    "RowStatus",
    "Footswitch",
    "FootswitchMode",
    "HYBRID_MODES",
    "hybrid_mode",
    "describe_mode",
    "RecallReason",
    "PowerOption",
    "BROKEN_MODE_VALUE",
    "MidiSource",
    "MidiOutType",
    "SceneBypassBehavior",
    "ExpressionBypassMode",
    "LooperState",
    "GlobalEQFilter",
    "TempoSubdivision",
    "MetronomeSound",
    "MetronomeBeat",
    "MetronomeRouting",
    "TimeSignature",
    "USER_SETLIST_ROOT",
    "option_value",
    "option_at",
    "MidiOut",
    "midi_out",
    "preset_load_midi_out",
    "StompAssignment",
    "Folder",
    "stomp_assignments",
    "param_options",
    "tempo_params",
    "beats",
    "BlockRefused",
    "UNITY_LEVEL",
    "DeviceLostError",
    "input_level_db",
    "db_to_input_level",
    "SCENE_UNLABELLED",
    "field_present",
    "Input",
    "Output",
    "Scene",
    "Instrument",
    "Setlist",
    "slot_to_position",
    "position_to_slot",
    "input_chain_rows",
    "models",
    "Model",
    "ModelCatalog",
    "Parameter",
]

"""The message-level API: one Python call per Quad Cortex protocol message.

This is the library as it shipped through 0.40.0, moved one import deeper and
otherwise unchanged - same classes, same methods, same behavior (ADR-0006)::

    from pyquadcortex import protocol

    with protocol.connect() as qc:
        print(qc.version())
        qc.switch_scene(1)

Use it when you want the wire, or when you want something the model does not
cover yet. For the model of the unit itself, use :func:`pyquadcortex.connect`.

See ``docs/api.md`` for the full surface and ``docs/protocol.md`` for the wire.
"""

from pyquadcortex._version import __version__
from pyquadcortex.protocol.transport import DeviceLostError
from pyquadcortex.protocol.client import (SCENE_UNLABELLED, UNITY_LEVEL,
                                          db_to_input_level, db_to_lane_level,
                                          input_level_db, lane_level_db,
                                          tempo_bpm, bpm_to_tempo,
                                          USER_SETLIST_ROOT, Block,
                                          BlockRefused, ControlNotDrivable, Folder,
                                          MidiOut, QuadCortex, Split,
                                          StompAssignment, blocks, field_present,
                                          free_rows, row_status, RowStatus, params_equal,
                                          GAIN_REDUCTION_PARAM,
                                          bypass_state, BypassState, param_state, ParamState,
                                          input_chain_rows, midi_out, option_at,
                                          option_value,
                                          param_options, position_to_slot,
                                          beats, tempo_params,
                                          preset_load_midi_out, slot_to_position, splits,
                                          stomp_assignments)
from pyquadcortex.protocol.targets import (BranchControl, ChainTarget,
                                           LaneControl, LaneInput, LaneOutput,
                                           Mixer, ParamTarget, PresetTarget,
                                           Splitter, Tempo)
from pyquadcortex.protocol.enums import (BROKEN_MODE_VALUE, Footswitch, FootswitchMode,
                                         HYBRID_MODES, Input, Instrument, MidiOutType,
                                         describe_mode, hybrid_mode, PowerOption, RecallReason,
                                         ExpressionSwitchMode, GlobalEQFilter, LooperState,
                                         MetronomeBeat, MetronomeRouting,
                                         MetronomeSound, MidiSource,
                                         Output, Scene,
                                         SceneBypassBehavior, Setlist, TempoMode,
                                         TempoSubdivision,
                                         TimeSignature)
from pyquadcortex.protocol.session import DeviceNotFoundError, connect, open_device
from pyquadcortex.protocol import models  # generated factory-block constants
from pyquadcortex.protocol import params  # generated parameter constants
from pyquadcortex.protocol import options  # generated option constants
from pyquadcortex.protocol.catalog import Model, ModelCatalog, Parameter

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
    "ExpressionSwitchMode",
    "LooperState",
    "GlobalEQFilter",
    "TempoMode",
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
    "ParamTarget",
    "ChainTarget",
    "LaneControl",
    "BranchControl",
    "PresetTarget",
    "LaneOutput",
    "LaneInput",
    "Mixer",
    "Splitter",
    "Tempo",
    "BlockRefused",
    "ControlNotDrivable",
    "UNITY_LEVEL",
    "DeviceLostError",
    "input_level_db",
    "db_to_input_level",
    "lane_level_db",
    "db_to_lane_level",
    "tempo_bpm",
    "bpm_to_tempo",
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
    "params",
    "options",
    "Model",
    "ModelCatalog",
    "Parameter",
]

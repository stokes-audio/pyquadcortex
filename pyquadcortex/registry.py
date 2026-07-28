"""Registry mapping CortexMessageType enum values to protobuf classes.

Bridges the frame codec (which deals in integer message-type tags) and the
generated protobuf message classes, in both directions:

  * ``type_for(cls)`` -> the ``CortexMessageType.Enum`` integer for a class.
  * ``class_for(message_type)`` -> the generated class for an enum integer.

Extend ``_BY_NAME`` as more message types are exercised.
"""

from pyquadcortex.proto import ProductionAutomation_pb2 as pa

# Map CortexMessageType.Enum name -> generated message class.
# Extend as more message types are exercised.
_BY_NAME = {
    "Version": pa.VersionMessage,
    "RecallPreset": pa.RecallPresetMessage,
    "Scene": pa.SceneMessage,
    "SceneCopy": pa.SceneCopyMessage,
    "SceneLabel": pa.SceneLabelMessage,
    "SceneColor": pa.SceneColorMessage,
    "Grid": pa.GridMessage,
    "GridMove": pa.GridMoveMessage,
    "File": pa.FileMessage,
    "BulkOperation": pa.BulkOperationMessage,
    "SetlistPosition": pa.SetlistPositionMessage,
    "KeepAlive": pa.KeepAliveMessage,
    "GlobalTempo": pa.GlobalTempoMessage,
    "MasterVolume": pa.MasterVolumeMessage,
    # Session hello (confirmed by capture).
    "ResetCommsBuffers": pa.ResetCommsBuffersMessage,
    "Connection": pa.ConnectionMessage,
    "SceneLabel": pa.SceneLabelMessage,
    "SceneColor": pa.SceneColorMessage,
    # Device chatter + connect-subscription state types (see client.hello's
    # _SUBSCRIBE_TYPES). Registered so the RX thread decodes them rather than
    # warning, and so hello() can build a READ for each by class.
    "UndoRedo": pa.UndoRedoMessage,
    "PresetDirty": pa.PresetDirtyMessage,
    "RecentsFavorites": pa.RecentsFavoritesMessage,
    "CPULoad": pa.CPULoadMessage,
    "ModuleStats": pa.ModuleStatsMessage,
    "License": pa.LicenseMessage,
    "IOSettings": pa.IOSettingsMessage,
    "GeneralSettings": pa.GeneralSettingsMessage,
    "ShowGigView": pa.ShowGigViewMessage,
    "Mode": pa.ModeMessage,
    "GlobalEQ": pa.GlobalEQMessage,
    "CompilerInhibitedModules": pa.CompilerInhibitedModulesMessage,
    "NewModels": pa.NewModelsMessage,
    "PinnedModels": pa.PinnedModelsMessage,
    "DefaultParameters": pa.DefaultParametersMessage,
    "Updater": pa.UpdaterMessage,
    "ModelRepo": pa.ModelRepoMessage,
    "GridModelMeter": pa.GridModelMeterMessage,
    "IOMeter": pa.IOMeterMessage,
    "SystemTimeSync": pa.SystemTimeSyncMessage,
    "CloudLogin": pa.CloudLoginMessage,
    "CloudProduct": pa.CloudProductMessage,
    # Per-preset MIDI output. The preset stores these in
    # BinaryPreset.midi_messages/_general/_general_v2, but a Grid update carrying
    # those fields does nothing - MIDISettings is what applies them.
    "MIDISettings": pa.MIDISettingsMessage,
    # Tuner and Looper X. Registered so their state can be read and driven.
    "Tuner": pa.TunerMessage,
    "ShowTuner": pa.ShowTunerMessage,
    "Looper": pa.LooperMessage,
    "GigViewButton": pa.GigViewButtonMessage,
}
_ENUM = pa.CortexMessageType.Enum

# Every remaining type in the enum whose generated class exists, resolved by name.
# The explicit table above records what this project has actually exercised; this
# fallback makes the RX path DECODE the rest rather than dropping them.
#
# That mattered more than it looks. Undecodable inbound messages are discarded before
# dispatch, so any tooling that watches decoded traffic was blind to roughly half the
# schema - and a feature whose message type was unregistered looked exactly like a
# feature that broadcasts nothing at all. Decoding everything removes that trap.
def _auto_registered():
    found = {}
    for value in pa.CortexMessageType.DESCRIPTOR.enum_types_by_name["Enum"].values:
        if value.name in _BY_NAME:
            continue
        cls = getattr(pa, f"{value.name}Message", None)
        if cls is not None:
            found[value.name] = cls
    return found


_BY_NAME.update(_auto_registered())
_BY_TYPE = {_ENUM.Value(name): cls for name, cls in _BY_NAME.items()}
# Reverse map (class -> enum integer) built once; message classes are hashable.
_TYPE_BY_CLASS = {cls: t for t, cls in _BY_TYPE.items()}


def type_for(cls) -> int:
    """Return the CortexMessageType enum integer for a protobuf class."""
    try:
        return _TYPE_BY_CLASS[cls]
    except KeyError:
        raise KeyError(f"no CortexMessageType registered for {cls.__name__}")


def class_for(message_type: int):
    """Return the generated protobuf class for a CortexMessageType enum integer."""
    return _BY_TYPE[message_type]

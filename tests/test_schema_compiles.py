def test_can_import_and_instantiate_core_messages():
    from pyquadcortex.protocol.proto import ProductionAutomation_pb2 as pa
    from pyquadcortex.protocol.proto import Preset_pb2 as preset

    # The routing enum must have the values we rely on.
    assert pa.CortexMessageType.Enum.Value("RecallPreset") == 15
    assert pa.CortexMessageType.Enum.Value("Scene") == 13
    assert pa.CortexMessageType.Enum.Value("Version") == 10

    # Core messages instantiate.
    assert pa.VersionMessage() is not None
    assert pa.RecallPresetMessage() is not None
    assert preset.BinaryPreset() is not None

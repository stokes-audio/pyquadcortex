"""Every operation is named by a hardware test, or says why not (spec section 4).

Source-reading, like tests/test_translation.py: the hardware modules are never
imported here, so this runs with no unit attached.
"""
import ast
import pathlib

from pyquadcortex.protocol import client

HARDWARE = pathlib.Path(__file__).parent / "hardware"

#: Operations no hardware test names, each with the reason. A new operation
#: has to come through this dict or through a marker; there is no third way.
UNMARKED_OPERATIONS = {
    # -- read back by a marked test, but never the subject of one --------------
    # These are how another test observes a write. That makes them exercised,
    # not verified: nothing asserts what the READ itself should return, so a
    # reader that lost a field would leave every test that uses it green.
    "global_eq": "the Global EQ gain test reads it back as its instrument; nothing asserts what global_eq() itself reports",
    "hold_timing_ms": "the HOLD threshold test reads it back as its instrument; nothing asserts the reader against a value set on the unit's own screen",
    "io_settings": "the input gain tests read it back as their instrument; nothing asserts the port listing against what the unit shows",
    "preset_dirty": "test_model_state.py calls it only to decide which branch to expect; nothing asserts the flag across a known-clean and known-dirty preset",
    "settings": "the global-settings echo test reads it only to find the value to flip; nothing asserts a settings() field against the unit's screen",
    "list_presets": "read by the scratch_preset fixture, which no test uses yet; no test asserts on the listing itself",

    # -- the preset library: writes that would touch the owner's own presets ---
    # The scratch_preset fixture is what these would be built on - it saves a
    # copy into a free User slot and deletes it again - but no test uses it yet.
    "save_current_preset": "no hardware test yet; scratch_preset is the fixture for one, and it must save into a free User slot rather than over the owner's work",
    "recall_preset": "no hardware test yet; a recall discards the loaded preset's unsaved edits, so it needs scratch_preset around it",
    "delete_preset": "no hardware test yet; only scratch_preset's teardown deletes anything, and a teardown asserts nothing",
    "copy_preset": "no hardware test yet; it writes into a second setlist, so a failed run leaves a stray preset in the owner's Directory",
    "move_preset": "no hardware test yet; it rearranges the slots a player has laid out for a gig, which a restore cannot make invisible mid-run",
    "rename_preset": "no Python same-commit hardware test yet; the exact Cortex File UPDATE previously failed firmware readback and needs a disposable stored preset",
    "read_preset": "no hardware test yet; it RECALLS the preset it reads, so it is a write in disguise and needs scratch_preset",
    "find_preset": "no hardware test yet; it needs a name that exists on the unit, which the loaded preset supplies - just not written",
    "wait_for_listing": "no hardware test yet; it is the polling wrapper round list_presets and says nothing until a write changes a listing",
    "create_setlist": "no hardware test yet; it adds a folder to the owner's Directory that a failed run would leave behind",
    "delete_setlist": "no hardware test yet; it deletes a folder and everything in it, which is the one preset-library mistake nothing can undo",
    "duplicate_setlist": "no hardware test yet; it copies a whole setlist preset by preset, so a failed run leaves a partial folder behind",

    # -- library listings whose CONTENT belongs to the owner -------------------
    # Each returns whatever this unit happens to hold, so a test can assert the
    # shape and not the answer. Worth writing; none is written.
    "list_folders": "no hardware test yet; the folders are the owner's, so a test can only assert the shape of the listing",
    "list_irs": "no hardware test yet; the IR library is the owner's and may be empty, so a test can only assert the shape",
    "captures": "no hardware test yet; the capture library is the owner's and may be empty, so a test can only assert the shape",
    "favorites": "no hardware test yet; the FAVORITES list is the owner's and may be empty, so a test can only assert the shape",
    "recents": "no hardware test yet; RECENTS changes with every recall, so a test can only assert the shape",
    "pinned_models": "no hardware test yet; which models are pinned is the owner's choice, so a test can only assert the shape",
    "loaded_position": "no hardware test yet; scratch_preset reads it, and asserting it needs a recall to a slot the test chose",
    "read_current_preset_push": "no hardware test yet; it returns the RecallPreset wrapper round the same payload read_current_preset returns",
    "mode": "no hardware test yet; which footswitch mode slots exist is the owner's configuration",
    "mode_cycle": "no hardware test yet; the cycle order is the owner's configuration, so a test can only assert the shape",
    "looper": "no hardware test yet; it answers only when a Looper X block is on the grid, which the loaded preset decides",
    "master_volume": "no hardware test yet; the level is one the owner set by ear, so a test can only assert the shape",
    "tuner": "no hardware test yet; the tuner state is meaningful only while the tuner is open, which is a write this suite does not make",

    # -- grid writes with no test yet ------------------------------------------
    "set_block": "only placed as setup by an expression test, with verify=False; nothing asserts set_block's own echo or its verify path",
    "remove_block": "reached only by an expression test's restore, and a restore asserts nothing about what it undid",
    "move_block": "no hardware test yet; it needs a free cell and a block to move, both of which depend on the loaded preset",
    "write_preset": "no hardware test yet, and none is wanted: it is the wholesale grid path the row/column writes replaced (CLAUDE.md)",
    "set_chain_output": "no hardware test yet; the echo test drives the INPUT side, and an output write is audible on a rig that is plugged in",
    "reroute_grid_input": "no hardware test yet; it rewrites every row on a port at once, so a partial restore leaves the preset re-routed",
    "set_split": "no hardware test yet; branching a row changes what the player hears and needs a preset with a free slot to branch at",
    "clear_split": "no hardware test yet; it can only run on a row that already branches, which the loaded preset decides",
    "set_split_mute": "no hardware test yet; it is audible and only means anything on a row that already branches",
    "set_param_option": "no hardware test yet; it needs a list-valued parameter on a block the loaded preset happens to carry",
    "set_param_scene_mode": "no hardware test yet; making a parameter follow scenes changes what every OTHER scene stores for it",
    "set_capture": "no hardware test yet; it needs a Neural Capture block on the grid and a capture in the owner's library",
    "set_ir": "no hardware test yet; it needs an IR Loader block on the grid and an IR in the owner's library",
    "set_expression_bypass": "no hardware test yet; the expression tests assign to parameters, and the bypass assignment is a different message",
    "copy_scene": "no hardware test yet; it overwrites a whole scene, so the restore is a second whole-scene copy taken first",
    "set_scene_bypass_behavior": "no hardware test yet; it changes how every later bypass edit is stored, so a mid-run failure changes what other tests mean",
    "set_stomp_assignment": "no hardware test yet; it re-labels a footswitch the player uses on stage",
    "clear_stomp_assignment": "no hardware test yet; it can only run on a footswitch that already has an assignment to lose",
    "set_stomp_label": "no hardware test yet; it changes what a footswitch reads on stage and needs the preset's own label saved first",
    "set_stomp_momentary": "no hardware test yet; latching versus momentary is a feel the player set, and the change is silent on screen",

    # -- global settings: the owner's rig, restored only by writing it back ----
    "set_master_volume": "the settings test proves it REFUSES a Db, not that it writes; driving master volume is audible and the level is one the owner set by ear",
    "set_master_volume_assignment": "no hardware test yet; it changes which outputs the knob governs, which is a rig decision rather than a preset one",
    "set_input_level": "no hardware test yet; the input gain tests drive set_input_port with level=, and this narrower method is never called",
    "set_output_level": "no hardware test yet; it is audible on a rig that is plugged in and takes Encoded only, so there is no screen value to assert against",
    "set_output_mute": "no hardware test yet; muting an output on a rig that is plugged in is audible in the worst direction",
    "set_output_pairing": "no hardware test yet; unpairing makes two ports stop sharing settings, so restoring the pairing does not restore the settings",
    "set_output_port": "no hardware test yet; the port settings are the owner's rig, and a partial restore leaves the wrong one in place",
    "set_usb_port": "no hardware test yet; the USB audio settings carry the link this suite runs over",
    "set_global_eq_band": "reached only by the Global EQ test's restore, which asserts nothing; the band write itself has no test",
    "set_global_eq_bypassed": "no hardware test yet; the Global EQ applies to every preset, so a failed restore changes the owner's whole rig",
    "set_global_eq_output": "no hardware test yet; it takes Encoded only, so there is no screen value a test could assert against",
    "set_global_bypass": "no hardware test yet; it bypasses Cab or IR blocks across ALL presets, which a failed restore leaves that way",
    "set_midi_out": "no hardware test yet; it rewrites what a footswitch sends to the rest of the rig",
    "set_midi_thru": "no hardware test yet; MIDI Thru affects the rest of the rig and the change is invisible on the unit",
    "set_preset_load_midi_out": "no hardware test yet; it stores up to twelve messages on the preset, so the restore is the whole list read first",
    "set_gig_view": "no hardware test yet; it opens Gig View on the unit's screen, and nothing restores the screen the player left it on",
    "set_mode": "no hardware test yet; it changes which footswitch mode the player is looking at",
    "set_mode_cycle": "no hardware test yet; it rewrites the mode cycle the player configured for a gig",
    "set_tuner_input": "no hardware test yet; the tuner input is a rig decision and the write is invisible until the tuner is open",
    "set_tuner_mute": "no hardware test yet; it changes whether the unit goes silent when the player tunes on stage",
    "set_tuner_reference": "no hardware test yet; a reference pitch left off 440 is the kind of change nobody notices until a gig",
    "show_tuner": "measured to do NOTHING on firmware d14e (client.py), so there is no effect on the unit to assert",
    "restore_audio": "no hardware test yet; it undoes the silence a host tuner write causes, and nothing here writes the tuner",
    "show_capture_dialog": "answers a request the DEVICE makes; the suite cannot make the unit ask, so there is no state-neutral way to reach it",
    "add_favorite": "no hardware test yet; it edits the FAVORITES list the player uses to find presets on stage",
    "remove_favorite": "no hardware test yet; it can only run on a preset that is already a favourite, which is the owner's choice",
    "pin_model": "no hardware test yet; it reorders the device list the player picks blocks from",
    "unpin_model": "no hardware test yet; it removes EVERY entry for a model, so a restore has to know how many there were",

    # -- the metronome and the tempo block ------------------------------------
    # Audible by definition, and seven of the settings belong to the PRESET in
    # PRESET mode and to the device in GLOBAL mode - so which one a write lands
    # in depends on a switch test_tempo_mode.py moves.
    "set_metronome_running": "no hardware test yet; starting the metronome is audible, and its scope depends on the MODE switch test_tempo_mode.py moves",
    "set_metronome_muted": "no hardware test yet; the mute is only observable while the metronome is running, which is a second audible write",
    "set_metronome_volume": "no hardware test yet; it is audible and its scope depends on the MODE switch test_tempo_mode.py moves",
    "set_metronome_sound": "no hardware test yet; it is audible and its scope depends on the MODE switch test_tempo_mode.py moves",
    "set_metronome_routing": "no hardware test yet; it decides which outputs the click reaches on a rig that is plugged in",
    "set_beat": "no hardware test yet; one beat's sound is audible and belongs to the preset in PRESET mode",
    "set_beats": "no hardware test yet; it writes several beats at once and is audible in the same way set_beat is",
    "set_time_signature": "no hardware test yet; it changes the bar the click counts, which is preset state in PRESET mode",
    "set_tempo_subdivision": "no hardware test yet; it is the metronome's SUBDIVISIONS by name and is audible while the click runs",
    "set_tempo_option": "no hardware test yet; it addresses a list-valued tempo parameter by option number and needs one of those measured first",
    "set_tempo_led": "no hardware test yet; the TEMPO LED is on the unit's face and nothing in a reply reports it",
}



def marked_operations():
    names = set()
    for path in sorted(HARDWARE.rglob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef):
                continue
            for deco in node.decorator_list:
                if (isinstance(deco, ast.Call) and isinstance(deco.func, ast.Attribute)
                        and deco.func.attr == "verifies"):
                    for arg in deco.args:
                        assert isinstance(arg, ast.Constant) and isinstance(arg.value, str), (
                            f"{path.name}:{node.lineno} verifies() takes string literals")
                        names.add(arg.value)
    return names


def test_every_marker_names_a_real_operation():
    unknown = marked_operations() - client.QuadCortex.operations()
    assert not unknown, f"markers name operations QuadCortex does not have: {sorted(unknown)}"


def test_every_operation_is_marked_or_excused_with_a_reason():
    missing = client.QuadCortex.operations() - marked_operations() - set(UNMARKED_OPERATIONS)
    assert not missing, (
        f"operations no hardware test verifies and no reason excuses: {sorted(missing)}. "
        f"Mark the test that drives each one with @pytest.mark.verifies(...), or add it "
        f"to UNMARKED_OPERATIONS with the reason.")
    stale = set(UNMARKED_OPERATIONS) & marked_operations()
    assert not stale, f"excused AND marked; drop the excuse: {sorted(stale)}"
    gone = set(UNMARKED_OPERATIONS) - client.QuadCortex.operations()
    assert not gone, (
        f"excuses naming no operation: {sorted(gone)}. A renamed or deleted "
        f"operation leaves its excuse behind, and an excuse for nothing is a "
        f"reason nobody will ever re-read; drop it.")
    for name, reason in UNMARKED_OPERATIONS.items():
        assert len(reason) > 20, name

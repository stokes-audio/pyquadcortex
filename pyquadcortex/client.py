"""High-level QuadCortex client for pyquadcortex.

This is the ergonomic API a caller (CLI, script) uses to control a Quad Cortex.
It builds protobuf messages and hands them to a ``transport``-like object,
which is dependency-injected via the constructor. The transport exposes:

  * ``send(message)``                  - fire-and-forget
  * ``request(message, timeout=...)``  - send and block for the correlated reply

The client deliberately knows NOTHING about hidapi, HID reports, or the framing
layer: it only speaks protobuf messages. That keeps this layer testable with a
fake transport and keeps all wire concerns in ``framing``/``transport``.

**Rows and columns are ZERO-BASED; the unit displays rows 1 to 4.** So ``row=0``
is the top row on screen, and ``row=2`` is the one labelled 3. Getting this wrong
is quiet rather than loud - an edit lands on a real row, just not the one intended,
and it reads back perfectly. When a change is meant to be audible, check which row
actually reaches a physical output: ``chain.out_portid`` values 16 to 18 are internal
row-to-row routing rather than jacks, though 19 (``MULTIPLE``) is a real destination.

Field semantics were confirmed against real Cortex Control 4.0.1 sessions:
session connect, preset recall (user AND factory setlists), scene switch, grid
bypass/param writes, Save As, delete, and move are all reproduced verbatim from
observed traffic. ``copy_scene`` came from a different source, because Cortex
Control has no scene-copy feature to observe: its shape was read off the device's
own broadcast when a scene was copied on the unit. It too is fully confirmed on
hardware, including its ``from_index`` and ``swap`` behaviour. See
``docs/protocol.md`` for the per-operation coverage table.
"""

import time
import uuid
from typing import NamedTuple

from pyquadcortex import catalog, registry
from pyquadcortex.enums import (Footswitch, Input, Instrument, MidiOutType,  # noqa: F401
                                MidiSource, Output, Setlist)
from pyquadcortex.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.proto import Preset_pb2 as preset

#: The wire value the mixer, splitter and lane-output LEVEL parameters hold when
#: nothing is attenuated - 10/13, which is 0 dB on the -100..+30 dB span those
#: controls cover. The catalog publishes them as 0..1 "dB" (see
#: :attr:`~pyquadcortex.catalog.Parameter.range_is_placeholder`), so this is the
#: reference point for reading and writing them. Measured on every row carrying
#: one across 17 factory presets.
UNITY_LEVEL = 0.76923077

#: How the unit stores "this scene has no label": a single space, not an empty
#: string. So ``label.strip()`` detects a blank scene and ``label == ""`` does not.
#: :meth:`QuadCortex.set_scene_label` sends this when given ``None``.
SCENE_UNLABELLED = " "


class BlockRefused(RuntimeError):
    """The device did not accept a block placement.

    Raised by :meth:`QuadCortex.set_block` when no echo confirms the cell. The
    known cause is the preset having no DSP capacity left for that model.
    """


def _require_even_row(row: int, what: str):
    """Splitters and mixers exist only on rows 0 and 2 - see :func:`splits`."""
    if row % 2:
        raise ValueError(
            f"row {row} has no {what}: a branch can only originate on row 0 or "
            f"row 2, whose parallel lane is the row below it. Rows 1 and 3 report "
            f"an empty {what} collection, and a write addressed there does nothing."
        )


class QuadCortex:
    """Ergonomic control surface over a request/response transport."""

    def __init__(self, transport, _owned_resources=None):
        self._t = transport
        # Set by pyquadcortex.connect() so close() can tear down the transport
        # and HID device it opened on the caller's behalf. When a caller wires
        # their own transport, they own its lifecycle and this stays empty.
        self._owned = _owned_resources or []
        # Populated on first use of .catalog (a ~47 KB fetch from the device).
        self._catalog = None

    # -- catalog -------------------------------------------------------------

    @property
    def catalog(self):
        """This unit's :class:`~pyquadcortex.catalog.ModelCatalog`, fetched once.

        Every block on the grid is stored as an integer model id; the catalog is
        what turns that into a name, a category, and the parameter list in wire
        index order. It comes FROM the device, so it covers whatever this unit
        actually has - purchased plugin models and the player's own Neural
        Captures included - which no hard-coded table could know.

        Fetched lazily (a ~47 KB transfer) and cached for the session.
        """
        if self._catalog is None:
            self._catalog = catalog.parse_model_repo(self._fetch_model_repo())
        return self._catalog

    def _fetch_model_repo(self, timeout: float = 25.0) -> bytes:
        """Ask the device for its ModelRepo payload and return the raw bytes."""
        message = self._t.await_broadcast(
            pa.ModelRepoMessage,
            lambda: self._t.send(pa.ModelRepoMessage(action=pa.MessageAction.READ)),
            timeout=timeout,
            match=lambda m: bool(m.model_repo_payload),
        )
        return message.model_repo_payload

    # -- lifecycle -----------------------------------------------------------

    def disconnect(self):
        """Tell the device this client is going away.

        Sends ``Connection{connected: false}``, which is what Cortex Control does
        on quit. Without it the device is never told the client left - it simply
        stops receiving keepalives - and this library announced the connect but
        never the disconnect.

        Best effort: a failure here never prevents teardown. On this device that
        matters less than it sounds, because EVERY host write is reported as
        failing thanks to the deliberate status-stage STALL, so swallowing the
        error is the normal path rather than a special case.

        :func:`pyquadcortex.connect` calls this for you as the first step of
        :meth:`close`. It is public for callers who supplied their own transport
        and therefore own teardown themselves, who otherwise had no
        non-private way to send it.

        Whether an abandoned session leaks anything on the device is an open
        question - see ``docs/protocol.md`` section 4.3 for what was measured.
        """
        try:
            return self._t.send(pa.ConnectionMessage(connected=False))
        except Exception:  # pragma: no cover - the link may already be gone
            return None

    def close(self):
        """Release the device, if this object opened it.

        Safe to call more than once. A :class:`QuadCortex` built around a
        caller-supplied transport does not own it, so this is then a no-op.
        """
        while self._owned:
            closer = self._owned.pop()
            try:
                closer()
            except Exception:  # pragma: no cover - best-effort teardown
                pass

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False

    # -- session -------------------------------------------------------------

    # State types the device only PUSHES to a client that has subscribed by
    # sending a READ for them during connect. Order/content mirror Cortex
    # Control's connect burst. RecallPreset is the one
    # that matters for read_preset, but the device appears to want the whole
    # set before it treats the client as fully connected.
    _SUBSCRIBE_TYPES = (
        "ModuleStats", "License", "UndoRedo", "IOSettings", "GeneralSettings",
        "ShowGigView", "Mode", "GlobalEQ", "MasterVolume", "File",
        "RecentsFavorites", "CompilerInhibitedModules", "RecallPreset",
        "NewModels", "PinnedModels", "DefaultParameters", "GlobalTempo",
        "SetlistPosition", "PresetDirty", "Scene", "BulkOperation", "Updater",
    )

    # Cortex Control version string the host announces. The device gates state
    # PUSH behaviour on receiving a valid cortex_control_version (see hello);
    # this is the version captured on the wire.
    CC_VERSION = "4.0.1"

    def _hello(self, timeout: float = 5.0, settle: float = 2.0):
        """Perform the full connect handshake Cortex Control performs.

        Internal: :func:`pyquadcortex.connect` calls this for you, so a caller
        never has to. It is only separate so the handshake can be tested and so
        an advanced caller wiring their own transport can still drive it.

        Confirmed by capture and live probe: the device
        will not push state (no RecallPreset preset dumps, no Grid/Scene sync)
        to a client that has only opened the pipe - a minimal
        ResetCommsBuffers+Connection is NOT enough (proven live: recalls
        produced zero device traffic until the full burst below was sent).
        The working sequence is:

          1. ``ResetCommsBuffers`` with a fresh 32-hex ``session_id`` (echoed).
          2. ``Version`` READ, then a ``Version`` UPDATE announcing
             ``cortex_control_version`` (the device gates push behaviour on a
             valid CC version).
          3. ``Connection{connected: true}``.
          4. A READ for each state type in ``_SUBSCRIBE_TYPES`` - this is the
             subscription that makes the device start pushing that state.

        Returns the echoed ResetCommsBuffers reply. After this, ``read_preset``
        and the device's live-sync pushes work.
        """
        reply = self._t.request(
            pa.ResetCommsBuffersMessage(session_id=uuid.uuid4().hex), timeout=timeout
        )
        # Announce our (Cortex Control) version - the device gates push
        # behaviour on a valid cortex_control_version. We do NOT also issue a
        # Version READ here: the device sends its own Version READ to us, and a
        # redundant host READ would race with a caller's later version request
        # (READ replies carry no request_id to disambiguate).
        self._t.send(
            pa.VersionMessage(
                action=pa.MessageAction.UPDATE, cortex_control_version=self.CC_VERSION
            )
        )
        self._t.send(pa.ModelRepoMessage(action=pa.MessageAction.READ))
        self._t.send(pa.ConnectionMessage(connected=True))
        for name in self._SUBSCRIBE_TYPES:
            self._t.send(registry.class_for(pa.CortexMessageType.Enum.Value(name))(
                action=pa.MessageAction.READ
            ))
        # The device needs a moment after the burst before it treats the client
        # as connected and starts pushing; a command sent too soon gets no push
        # (observed as flaky read_preset timeouts). Settle before returning so
        # callers can issue the first command immediately.
        time.sleep(settle)
        return reply

    # -- read ----------------------------------------------------------------

    def version(self, timeout: float = 10.0):
        """Read the device's version info.

        Returns the device's ``VersionMessage``, whose fields include
        ``app_fw_version`` (the firmware version), ``device_type``,
        ``device_serial_number``, and ``comms_version``.

        Works without the connect handshake, so it is a good first call to
        confirm the device is talking.
        """
        return self._t.request(
            pa.VersionMessage(action=pa.MessageAction.READ), timeout=timeout
        )

    def find_preset(self, name: str, setlist: str = Setlist.USER,
                    timeout: float = 25.0):
        """Look a preset up by the name shown on the unit.

        Returns its listing entry, whose ``index`` is the position the recall and
        read methods take::

            cali = qc.find_preset("Cali Basswalk", Setlist.FACTORY)
            preset = qc.read_preset(Setlist.FACTORY, cali.index)

        Matching is exact but case-insensitive. Raises ``KeyError`` if no preset
        of that name exists in the setlist.
        """
        wanted = name.strip().lower()
        entries = self.list_presets(setlist, timeout=timeout)
        for entry in entries:
            if entry.name.strip().lower() == wanted:
                return entry
        raise KeyError(f"no preset named {name!r} in {str(setlist)!r}")

    def read_preset(
        self, setlist_path: str, position, is_factory: bool = None,
        timeout: float = 40.0,
    ) -> preset.BinaryPreset:
        """Recall a preset and return its full ``BinaryPreset``.

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"28C"``); :meth:`find_preset` turns a preset name into one.
        ``is_factory`` is inferred from ``setlist_path``.

        Confirmed by capture and live probe: there is NO
        host-initiated "read preset" request - a ``GridMessage``/``RecallPreset``
        READ gets no reply. Instead the device BROADCASTS a ``RecallPreset``
        push (its ``preset`` field carrying the full BinaryPreset, often
        gzip-compressed - the transport decompresses it) whenever a preset is
        recalled, by host or by the unit. So this recalls the slot and captures
        that push. NOTE: this DOES load the preset onto the grid (it is not a
        side-effect-free read); the device services the push lazily (10-25s
        observed), hence the generous timeout.

        Correlation, confirmed on hardware: the RecallPreset push a host
        recall triggers echoes that recall's ``request_id``, while the
        unsolicited seed push (hello's subscription grid state) carries none.
        Without matching on the id, the waiter returns whatever RecallPreset
        arrives first - which lags by one recall when a prior push is still in
        flight (the seed seeds the lag). So tag the recall with a fresh
        request_id and accept only the push echoing it.
        """
        rid = self._t.next_request_id()

        def trigger():
            self.recall_preset(setlist_path, position, is_factory, request_id=rid)

        push = self._t.await_broadcast(
            pa.RecallPresetMessage,
            trigger,
            timeout=timeout,
            match=lambda m: m.HasField("request_id") and m.request_id == rid,
        )
        return push.preset

    def list_presets(self, setlist: str = Setlist.USER, timeout: float = 25.0,
                     include_empty: bool = False) -> list:
        """List the presets in a setlist, in slot order.

        Each entry is a ``ProductData`` with ``index`` (the linear slot position,
        see :func:`slot_to_position`), ``name``, and ``instrument`` (see
        :class:`~pyquadcortex.enums.Instrument`).

        The device always reports a setlist as its full complement of 256 slots,
        most of which are typically empty. By default only occupied slots are
        returned; pass ``include_empty=True`` for the complete slot map, e.g. to
        find a free slot to save into.

        Unlike :meth:`read_preset`, this does not change what is loaded on the
        grid. There is no host-initiated "list" request: a ``File`` READ makes the
        device push a folder listing per setlist, so this sends that READ and
        waits for the listing whose key matches ``setlist``.

        A listing that arrives is COMPLETE - five READs against an 18-preset setlist
        each produced a full listing, and no short one has been observed. But a READ
        does not reliably produce one promptly: two of those five saw nothing for
        that setlist within 8 s, delivery being lazy. So treat a timeout as "ask
        again", which is what :meth:`wait_for_listing` does, rather than as an
        answer about the setlist's contents.

        Note the trailing-slash asymmetry the match has to absorb: recalls need
        the factory path WITH its trailing slash (Cortex Control sends it that
        way), but the device reports that same folder's listing key WITHOUT one.
        Keys are therefore compared with trailing slashes normalized away.
        """
        wanted = str(setlist).rstrip("/")
        listing = self._t.await_broadcast(
            pa.FileMessage,
            lambda: self._t.send(pa.FileMessage(action=pa.MessageAction.READ)),
            timeout=timeout,
            match=lambda m: (
                m.folder.key.rstrip("/") == wanted and len(m.folder.files) > 0
            ),
        )
        entries = sorted(
            listing.folder.files,
            key=lambda pd: pd.index if pd.HasField("index") else -1,
        )
        if include_empty:
            return entries
        return [pd for pd in entries if pd.HasField("name") and pd.name]

    # -- navigation ----------------------------------------------------------

    def recall_preset(
        self,
        setlist_path: str,
        position,
        is_factory: bool = None,
        request_id: int = None,
    ):
        """Recall a preset within the setlist at ``setlist_path``.

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"28C"``). ``is_factory`` is inferred from ``setlist_path``
        and only needs passing for a setlist this library does not know about.

        Confirmed by capture: recall is a ``SetlistPositionMessage`` UPDATE. The
        setlist is addressed by its device filesystem path in ``folder_key`` and
        the preset by its LINEAR index in ``position``: bank*8 + letter,
        zero-based, so preset "28C" is ``(28-1)*8 + 2 == 218``. Cortex Control
        recalling 28C sent exactly ``{folder_key: "/media/p4/Presets/My
        Presets", position: 218, is_factory: false}``.
        """
        msg = pa.SetlistPositionMessage(action=pa.MessageAction.UPDATE)
        msg.folder_key = setlist_path
        msg.position = _as_position(position)
        msg.is_factory = _is_factory_setlist(setlist_path) if is_factory is None \
            else is_factory
        if request_id is not None:
            msg.request_id = request_id
        return self._t.send(msg)

    def switch_scene(self, scene: int):
        """Switch the active scene.

        Takes a :class:`~pyquadcortex.enums.Scene` (``Scene.B``); scenes are
        numbered from zero, so a bare integer works too.
        """
        msg = pa.SceneMessage(action=pa.MessageAction.UPDATE)
        msg.selected_scene = int(scene)
        return self._t.send(msg)

    def copy_scene(self, from_index: int, to_index: int, swap: bool = False):
        """Copy (or swap, when ``swap=True``) one scene onto another.

        Cortex Control has no scene-copy feature, so this message was not learned
        from its traffic. Instead, copying a scene ON THE UNIT broadcasts
        ``SceneCopy{action: UPDATE, to_index: N}`` (note the action is UPDATE,
        not COPY), and that is the shape sent here.

        Confirmed working host-to-device on hardware, ``from_index`` included:
        ``copy_scene(1, 3)`` on a preset whose scenes A and B differ made scene D
        an exact copy of scene B (not of scene A, which is what a device ignoring
        ``from_index`` would have produced).

        ``swap=True`` is also confirmed: it exchanges the two scenes rather than
        overwriting one, so scene B ends up holding scene D's former state and
        vice versa.

        The scene's LABEL and COLOUR both travel with its bypass and parameter
        state: a copy renames and recolours the destination scene, and a swap
        exchanges both. Confirmed on hardware with nothing else sent - ``copy_scene(
        Scene.E, Scene.B)`` on factory 28A moved 'Clean +VMT' and ``0xff45f862``
        onto scene B - and by performing the same copy on the unit. So reproducing a
        scene map needs no :meth:`set_scene_color` calls for the copied scenes.
        """
        return self._t.send(
            pa.SceneCopyMessage(
                action=pa.MessageAction.UPDATE,
                from_index=from_index,
                to_index=to_index,
                is_swap=swap,
            )
        )

    def set_scene_label(self, scene_index: int, label):
        """Rename a scene, or blank it with ``label=None``.

        Confirmed shape: ``SceneLabel{action: UPDATE, index, label}`` (observed as
        the device's broadcast when a scene was renamed on the unit).

        The unit stores an unlabelled scene as a single SPACE rather than an empty
        string - factory "Cali Basswalk" (27E) reads back ``" "`` for the four
        scenes it does not use - so ``None`` sends :data:`SCENE_UNLABELLED` to match
        what the unit itself writes, and a blank scene is detected with
        ``label.strip()`` rather than ``label == ""``.
        """
        return self._t.send(
            pa.SceneLabelMessage(
                action=pa.MessageAction.UPDATE,
                index=scene_index,
                label=SCENE_UNLABELLED if label is None else label,
            )
        )

    def set_scene_color(self, scene_index: int, color: int):
        """Recolor a scene. Confirmed shape: ``SceneColor{action:
        UPDATE, index, color}`` with ``color`` an ARGB uint32 (recoloring a
        scene pinkish on the unit broadcast 0xFFFF02C2)."""
        return self._t.send(
            pa.SceneColorMessage(
                action=pa.MessageAction.UPDATE, index=scene_index, color=color
            )
        )

    # -- grid write ----------------------------------------------------------

    def write_preset(self, p: preset.BinaryPreset):
        """Send ``p`` as a ``Grid`` UPDATE - the low-level grid-edit primitive.

        The device applies a Grid UPDATE by locating each chain/model by its
        ``row``/``column`` KEY (mirroring the captured param-change updates), so
        ``p`` must carry explicit ``row`` (and ``column`` for model edits) on the
        elements it changes. A sparse, correctly-keyed preset works; the
        convenience wrappers :meth:`set_chain_input`, :meth:`set_param`, and
        :meth:`set_bypass` build exactly such presets and are the usual entry
        points.

        WARNING, confirmed on hardware: a preset freshly read from a
        recall carries NO explicit
        ``row``, so writing it back WHOLESALE does nothing - a full-preset write
        that re-pointed ``in_portid`` read back UNCHANGED. Do not expect a
        recalled-then-mutated preset to persist via this method; use the keyed
        wrappers instead.
        """
        return self._t.send(pa.GridMessage(action=pa.MessageAction.UPDATE, preset=p))

    def set_chain_input(self, row: int, in_portid: int):
        """Re-point one grid ``row``'s input to ``in_portid`` (row-keyed update).

        Confirmed on hardware: a ``Grid`` UPDATE carrying a single chain
        ``{row, in_portid}`` re-points that grid row's input; the device then
        saves it with ``save_current_preset`` (which snapshots the grid). This
        is the ONLY shape that actually moved an input on the wire - a
        full-preset write whose chains lacked ``row`` did nothing. Verified live:
        recall factory D-Cell (row 0 = Input 1) -> ``set_chain_input(0, INPUT_2)``
        -> Save -> recall shows ``in_portid == INPUT_2``.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.in_portid = in_portid
        return self._t.send(msg)

    def set_param(self, row: int, column: int, param_index=None,
                  value: float = 0.0, scene=None, param=None, model=None,
                  real=None, promote: bool = True, text: str = None):
        """Set one block parameter on the grid (row/column-keyed sparse update).

        Confirmed shape: a knob change streams
        ``Grid{UPDATE, preset{chains{row, models{column, params{index,
        param_values[scene]{float_value}}}}}}``. This is the ONLY way an edit
        persists - a full-preset write is dropped (chains carry no row). Save
        the grid afterwards to keep it. ``value`` is the normalized 0..1 float
        the device expects.

        The parameter may be given as a wire ``param_index``, or by NAME via
        ``param`` together with ``model`` (the block's model id, or a
        :class:`~pyquadcortex.catalog.Model`), which resolves the index through
        the device catalog. Naming is the safer route: indices are positional
        and not every one is a visible knob - a cab's parameters are internal
        ``ir selector`` entries, so writing index 0 changes stored data and
        moves nothing on screen.

        ``value`` is the normalized 0..1 the wire carries. Pass ``real=``
        instead to give the value in the parameter's OWN units (dB, ms, Hz...)
        and have it converted using the catalog's range - that needs ``param``
        and ``model`` so the range is known::

            qc.set_param(row=0, column=1, param="THRESHOLD", real=-20, model=comp)

        **Per-scene values.** Name a ``scene`` to change that scene alone::

            qc.set_param(row=2, column=5, param_index=0, value=0.8, scene=Scene.D)

        Three things had to line up for this, all confirmed on hardware:

        1. The device honours ``param_values[0]`` against whichever scene is
           **active** - the index is not a scene selector, so nothing is ever
           padded.
        2. It only keeps per-scene values for a parameter whose ``scene_mode`` is
           set, so naming a scene promotes the parameter first (pass
           ``promote=False`` to skip that if you know it is already set).
        3. The device accepts **either** the flag **or** a value in one message,
           never both: sent together, the flag is silently dropped. So this issues
           the promotion, the scene switch, and the write as three messages.

        Ordering over the pipe is enough; no settle delay is needed. Naming a
        scene leaves the unit sitting on it, which is a visible side effect.

        Without ``scene``, the write lands on the active scene - which for a
        parameter that is not scene-following is its single global value, and so
        appears in all eight scenes.

        **Not every parameter is a number.** A ``ParamValue`` can carry a
        ``string_value`` instead, which is how a cab's microphone selection is
        stored - the unit broadcasts ``string_value: "NG_212 DG Neo_Condenser
        U47"`` when a mic is chosen. Pass ``text=`` for those; confirmed to
        survive a save and read-back. The readable option list for a parameter
        that has one is in ``Param.dynamic_steps`` on the preset, not in the
        catalog (see :meth:`param_options`).
        """
        if text is not None and real is not None:
            raise TypeError("pass text= or real=, not both")
        if real is not None:
            if param is None or model is None:
                raise TypeError(
                    "real= needs param= and model= so the parameter's range is "
                    "known; pass value= for a normalized 0..1 float"
                )
            resolved = model if hasattr(model, "parameter") else self.catalog[int(model)]
            spec = (resolved.parameter(param) if isinstance(param, str)
                    else resolved.parameters[param])
            param_index = spec.index
            value = spec.to_normalized(real)
        elif param is not None:
            param_index = self._resolve_param_index(param, model)
        if param_index is None:
            raise TypeError("set_param needs either param_index or param=<name> with model=")
        if scene is not None:
            if promote:
                self.set_param_scene_mode(row, column, param_index, True)
            self.switch_scene(scene)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        p = model_msg.params.add()
        p.index = param_index
        if text is not None:
            p.param_values.add().string_value = text
        else:
            p.param_values.add().float_value = value
        return self._t.send(msg)

    def set_param_scene_mode(self, row: int, column: int, param_index: int,
                             enabled: bool = True):
        """Make a block parameter follow scenes, or stop it following them.

        A parameter only keeps per-scene values while ``scene_mode`` is set; until
        then it has one global value. On the unit this is the long-press
        assignment.

        The flag must travel ALONE. A ``Grid`` update carrying both
        ``scene_mode`` and a ``param_values`` entry is treated as a plain value
        write and the flag is dropped.
        :meth:`set_param` sequences that for you when you name a scene.

        Save the grid afterwards to keep it.

        Rows and columns are zero-based; the unit displays rows 1 to 4.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        prm = model_msg.params.add()
        prm.index = param_index
        prm.scene_mode = enabled
        return self._t.send(msg)

    def _resolve_param_index(self, param, model) -> int:
        """Turn a parameter name into its wire index using the catalog."""
        if isinstance(param, int):
            return param
        if model is None:
            raise TypeError(
                "naming a parameter needs model=<model id or catalog Model> so "
                "the index can be resolved from the catalog"
            )
        resolved = model if hasattr(model, "parameter") else self.catalog[int(model)]
        return resolved.parameter(param).index

    # -- grid blocks ---------------------------------------------------------

    def set_block(self, row: int, column: int, model, verify: bool = True,
                  timeout: float = 5.0):
        """Put ``model`` in the grid cell at ``row``/``column``.

        Creates a block in an empty cell and replaces whatever is in an occupied
        one - the device makes no distinction. ``model`` is a model id or a
        :class:`~pyquadcortex.catalog.Model`; :mod:`pyquadcortex.models` has
        constants for the factory blocks, and :attr:`catalog` resolves anything
        installed on the unit, including purchased models and Neural Captures.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is added on the unit: ``Grid{UPDATE, preset{chains{row,
        models{column, hash}}}}``. Save the grid afterwards to keep it.

        **A placement can be refused for want of DSP capacity.** The preset as a
        whole has a processing budget, and a block that does not fit is accepted on
        the wire like any other write and simply is not there afterwards. Confirmed
        on hardware: adding a chain ending in a bass cab to factory "OneStar Clean
        Tweed" (02C) placed every block except the cab, deterministically, while the
        cheaper block AFTER it in the same chain landed. Nothing in the reply says
        so - every host write is STALLed, and there is no per-block error message.

        So by default this VERIFIES, which is possible without saving: the device
        echoes a ``Grid`` broadcast naming the cell it accepted (~0.3 s on the
        firmware measured), and a refused block produces no echo at all. When none
        arrives within ``timeout``, this raises :class:`BlockRefused`. Pass
        ``verify=False`` to send and return immediately, in which case a save and
        read-back is the only way to learn whether the block is there.
        """
        model_id = int(getattr(model, "id", model))
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = model_id
        if not verify:
            return self._t.send(msg)

        def echoes_cell(m):
            # Same row/column conventions as blocks(): both fields may arrive
            # without presence, in which case position in the repeated field is
            # the index.
            for i, ch in enumerate(m.preset.chains):
                if (ch.row if field_present(ch, "row") else i) != row:
                    continue
                for j, mdl in enumerate(ch.models):
                    if (mdl.column if field_present(mdl, "column") else j) != column:
                        continue
                    if field_present(mdl, "hash") and mdl.hash == model_id:
                        return True
            return False

        try:
            self._t.await_broadcast(pa.GridMessage, lambda: self._t.send(msg),
                                    timeout=timeout, match=echoes_cell)
        except TimeoutError:
            raise BlockRefused(
                f"the device did not accept {model_id} at row {row} column "
                f"{column}: no Grid echo within {timeout}s. The usual cause is "
                f"that the preset has no DSP capacity left for this block - try a "
                f"cheaper one, or free a block. Pass verify=False to send without "
                f"checking."
            ) from None
        return None

    def remove_block(self, row: int, column: int):
        """Remove the block at ``row``/``column``, leaving the cell empty.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is deleted on the unit: ``Grid{action: DELETE, preset{chains{row,
        models{column, hash: 0}}}}``. The ACTION is what marks the removal -
        an UPDATE carrying ``hash: 0`` is transmitted but ignored by the
        firmware. Save the grid afterwards to keep it.
        """
        msg = pa.GridMessage(action=pa.MessageAction.DELETE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = 0
        return self._t.send(msg)

    def set_bypass(self, row: int, column: int, bypassed: bool, scene=None):
        """Bypass or enable one block on the grid (row/column-keyed sparse update).

        Shape: ``Grid{UPDATE, preset{bypass{row, colBypass{column,
        sceneBypass{bypass}}}}}``. Save the grid afterwards to keep it.

        Unlike parameters, bypass really is per scene - but not by index.
        Confirmed on hardware: the device applies ``sceneBypass[0]`` to whichever
        scene is ACTIVE and ignores any entry beyond it. So:

        * without ``scene``, this changes the block in the currently active scene;
        * with ``scene`` (a :class:`~pyquadcortex.enums.Scene`), the unit is first
          switched to that scene, which is a visible side effect worth knowing
          about - the unit is left sitting there.

        Ordering over the pipe is enough for that pair; no settle delay is needed.

        Blocks only follow scenes when their ``ColBypass.sceneMode`` is set. For a
        block without it, bypass is a single global state and writing it changes
        every scene at once, whatever the active scene is.
        """
        if scene is not None:
            self.switch_scene(scene)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        bp = msg.preset.bypass.add()
        bp.row = row
        cb = bp.colBypass.add()
        cb.column = column
        cb.sceneBypass.add().bypass = bypassed
        return self._t.send(msg)

    def reroute_grid_input(self, p: preset.BinaryPreset, to_port: int,
                           from_port: int = None) -> list:
        """Re-point every grid input row on ``from_port`` to ``to_port``.

        ``p`` is the preset currently on the grid (from :meth:`read_preset`); the
        rows to move are found with :func:`input_chain_rows` and each is sent as
        a row-keyed :meth:`set_chain_input` update. Returns the rows moved.
        ``from_port`` defaults to :attr:`Input.INPUT_1` (factory presets are all
        on Input 1). Raises ``KeyError`` if no row is on ``from_port``. Save the
        grid to persist.
        """
        if from_port is None:
            from_port = Input.INPUT_1
        rows = input_chain_rows(p, from_port)
        if not rows:
            raise KeyError(f"no grid input row on port {from_port}")
        for row in rows:
            self.set_chain_input(row, to_port)
        return rows

    # -- file ops ------------------------------------------------------------

    LANE_OUTPUT_CONTROL = 23000

    TEMPO_CONTROL = 25000

    INPUT_GATE_CONTROL = 28000

    def set_tempo_param(self, param, value: float = None, real=None):
        """Set a per-preset tempo/metronome parameter.

        Each preset carries a ``TempoControl`` block (model ``25000``) in
        ``BinaryPreset.tempoProgramData`` with 24 parameters, among them ``TEMPO``,
        ``LED LIGHT``, ``VOLUME``, ``TYPE``, ``TIME SIGNATURE`` and ``SOUND``.
        These are per PRESET, unlike ``GlobalTempo``, which is global and only ever
        reported a running clock.

        Confirmed on hardware: although ``tempoProgramData`` is NOT row or column
        keyed - it sits outside ``chains[]`` - a ``Grid`` UPDATE carrying it is
        applied anyway, and survives a save and recall. The hash is optional.

        Convenience wrappers: :meth:`set_tempo_led`, :meth:`set_metronome_volume`.
        """
        index = param
        if isinstance(param, str) or real is not None:
            model = self.catalog[self.TEMPO_CONTROL]
            spec = (model.parameter(param) if isinstance(param, str)
                    else model.parameters[param])
            index = spec.index
            if real is not None:
                value = spec.to_normalized(real)
        if value is None:
            raise TypeError("set_tempo_param needs value= (0..1) or real= (own units)")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        tp = msg.preset.tempoProgramData.add()
        tp.hash = self.TEMPO_CONTROL
        prm = tp.params.add()
        prm.index = index
        prm.param_values.add().float_value = value
        return self._t.send(msg)

    def set_tempo_led(self, on: bool):
        """Turn this preset's TEMPO LED on or off."""
        return self.set_tempo_param("LED LIGHT", value=1.0 if on else 0.0)

    def set_metronome_volume(self, value: float):
        """Set this preset's metronome level, 0.0 being silent.

        There is no mute flag anywhere in the schema - ``MetronomeStatusUpdate``
        carries only ``is_enabled`` and ``preroll_enabled`` - so muting means
        setting this to zero.
        """
        return self.set_tempo_param("VOLUME", value=value)

    def set_chain_output(self, row: int, out_portid: int):
        """Point one grid ``row``'s output at ``out_portid`` (row-keyed update).

        The sibling of :meth:`set_chain_input`, and the piece needed to finish a
        chain built on a previously empty row: blocks and an input are not enough,
        because a row whose output is unset does not reach a jack.

        Confirmed on hardware by read-back, not assumed from the symmetry: a
        ``Grid`` UPDATE carrying a single chain ``{row, out_portid}`` re-points that
        row's output, and the value survives a save and recall.

        Pass a :class:`~pyquadcortex.enums.Output`. Note that not every member is a
        physical destination. Values **16 to 18** are internal grid routing
        (``NEXT_ROW_*``): a row set to one of those feeds another row rather than a
        jack.

        **19 (``MULTIPLE``) IS a real destination** - it is what factory presets use
        to reach the Multi-Out, so it is often the right value when building a chain
        that has to be audible alongside an existing one. Factory "Brit 2203" uses 16
        on row 0 to feed the next row and 19 on row 2 for the actual output.

        Note also that the device does NOT validate this field: an id that means
        nothing is stored rather than rejected, so a typo reads back cleanly. See
        ``docs/protocol.md``.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.out_portid = out_portid
        return self._t.send(msg)

    SPLITTER_AB = 10000
    MIXER = 11000

    SPLITTER = 10004

    def set_splitter_param(self, row: int, param, value: float = None, real=None,
                           scene=None, promote: bool = True):
        """Set a Splitter parameter on ``row`` (``LEVEL TO A``, ``BALANCE``...).

        The splitter divides a row into two parallel lanes; :meth:`set_mixer_param`
        controls how they recombine.

        **Address it by the UNIFIED model 10004 ("Splitter"), whatever the preset
        reports.** A preset stores a type-specific legacy id - 10000 "Splitter AB",
        10002 "Splitter Balance", 10003 "LR Crossover" - but the device speaks the
        unified model's parameter order: ``TYPE, STEREO, BALANCE, LEVEL TO A,
        LEVEL TO B, FREQUENCY, MODE``. Which parameters actually apply depends on
        ``TYPE``: ``LEVEL TO A``/``LEVEL TO B`` only for A/B, ``BALANCE`` only for
        Balance, ``FREQUENCY``/``MODE`` only for Crossover.

        **This writes ``chain.combined_splitter``, not ``chain.splitter``.** A preset
        also exposes ``chain.splitter[]``, which is a READ-ONLY view of the same
        state: a write addressed there is silently ignored, accepted on the wire and
        absent on read-back. Always go through this method.

        ``row`` must be 0 or 2: a splitter exists only on an even row, whose lane is
        the row below (see :func:`splits`). An odd row raises ``ValueError`` rather
        than sending a write into a collection the device does not have there.

        Same call shape as :meth:`set_param`, ``scene`` included - though note that
        splitter parameters read back with ``scene_mode`` false in factory content,
        so per-scene splitter values may be unusual.
        """
        _require_even_row(row, "splitter")
        index = param
        if isinstance(param, str) or real is not None:
            model = self.catalog[self.SPLITTER]
            spec = (model.parameter(param) if isinstance(param, str)
                    else model.parameters[param])
            index = spec.index
            if real is not None:
                value = spec.to_normalized(real)
        if value is None:
            raise TypeError("set_splitter_param needs value= (0..1) or real= (own units)")
        if scene is not None:
            if promote:
                self._t.send(self._combined_splitter_message(row, index, scene_mode=True))
            self.switch_scene(scene)
        return self._t.send(self._combined_splitter_message(row, index, value=value))

    def _combined_splitter_message(self, row, index, value=None, scene_mode=None):
        """Build the shape the device itself broadcasts: no hash, no column."""
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        el = chain.combined_splitter.add()
        prm = el.params.add()
        prm.index = index
        if scene_mode is not None:
            prm.scene_mode = scene_mode
        if value is not None:
            prm.param_values.add().float_value = value
        return msg

    def set_mixer_param(self, row: int, param, value: float = None, real=None,
                        scene=None, promote: bool = True):
        """Set a Mixer parameter on ``row`` (``LEVEL A``, ``LEVEL B``, ``PAN A``...).

        The mixer lives in ``chain.mixer[]``. Model ``11000``, "Mixer".

        This is how factory presets build scenes without bypassing anything:
        ``LEVEL A`` and ``LEVEL B`` arrive with ``scene_mode`` already set, and each
        scene raises one path while muting the other. Factory "Darkglass AO900 1"
        does exactly that across two rows to give eight scenes four amp paths, so a
        library that cannot write the mixer cannot reproduce that preset's scene
        behaviour.

        ``row`` must be 0 or 2, for the same reason as :meth:`set_splitter_param`:
        a mixer exists only where a branch can originate. An odd row raises
        ``ValueError``.

        ``LEVEL A``, ``LEVEL B`` and ``MIXER LEVEL`` publish a placeholder catalog
        range, so pass ``value=`` rather than ``real=`` for them;
        :data:`pyquadcortex.UNITY_LEVEL` is unity.

        Same call shape as :meth:`set_param`, including ``scene``.
        """
        _require_even_row(row, "mixer")
        return self._set_sub_param("mixer", self.MIXER, row, param,
                                   value, real, scene, promote)

    def _set_sub_param(self, collection: str, model_hash: int, row: int, param,
                       value, real, scene, promote):
        """Shared body for the splitter/mixer/lane-output style collections."""
        index = param
        if isinstance(param, str) or real is not None:
            model = self.catalog[model_hash]
            spec = (model.parameter(param) if isinstance(param, str)
                    else model.parameters[param])
            index = spec.index
            if real is not None:
                value = spec.to_normalized(real)
        if value is None:
            raise TypeError(
                f"set_{collection}_param needs value= (0..1) or real= (own units)")
        if scene is not None:
            if promote:
                self._sub_param_scene_mode(collection, model_hash, row, index, True)
            self.switch_scene(scene)
        return self._t.send(
            self._sub_param_message(collection, model_hash, row, index, value=value))

    def _sub_param_scene_mode(self, collection, model_hash, row, index, enabled):
        return self._t.send(
            self._sub_param_message(collection, model_hash, row, index,
                                    scene_mode=enabled))

    def _sub_param_message(self, collection, model_hash, row, index,
                           value=None, scene_mode=None):
        """Build a row-keyed Grid update against splitter[]/mixer[].

        The flag and a value must never travel together - the device would treat
        the message as a plain value write and drop the flag (see
        :meth:`set_param_scene_mode`).
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = getattr(chain, collection).add()
        model_msg.hash = model_hash
        prm = model_msg.params.add()
        prm.index = index
        if scene_mode is not None:
            prm.scene_mode = scene_mode
        if value is not None:
            prm.param_values.add().float_value = value
        return msg

    def set_lane_output(self, row: int, param, value: float = None, real=None,
                        scene=None, promote: bool = True):
        """Set a Lane Output Control parameter on ``row``.

        Every grid row carries a Lane Output Control block - the VOLUME, PAN, MUTE
        and SOLO the manual describes - and it lives in ``chain.output_control[]``
        rather than ``chain.models[]``, so :meth:`set_param` cannot reach it. It is
        model ``23000``, present and populated on all four rows whether or not the
        row has any blocks.

        ``param`` is a name (``"VOLUME"``, ``"PAN"``, ``"MUTE"``, ``"SOLO"``) or a
        wire index. Pass ``value`` as the normalized 0..1 the wire carries::

            qc.set_lane_output(row=0, param="PAN", value=0.5)                # centre
            qc.set_lane_output(row=0, param="VOLUME", value=UNITY_LEVEL)     # 0 dB
            qc.set_lane_output(row=0, param="VOLUME", value=0.0)             # silent

        ``VOLUME`` publishes a placeholder catalog range, so ``real=`` raises for it
        rather than converting: :data:`UNITY_LEVEL` is the value it holds when
        nothing is attenuated, and 0.0 is silence.

        A keyed parameter write into ``output_control`` persists the same way as
        one into ``models`` (confirmed on hardware). Note the wire carries FIVE
        parameters here while the catalog documents four; index 4 is unidentified,
        so prefer naming the one you want.

        Name a ``scene`` for a per-scene value, exactly as :meth:`set_param` does.
        A silent scene - one that mutes the rig without leaving the preset - is::

            qc.set_lane_output(row=0, param="VOLUME", value=0.0, scene=Scene.D)
        """
        index = param
        if isinstance(param, str) or real is not None:
            model = self.catalog[self.LANE_OUTPUT_CONTROL]
            spec = (model.parameter(param) if isinstance(param, str)
                    else model.parameters[param])
            index = spec.index
            if real is not None:
                value = spec.to_normalized(real)
        if value is None:
            raise TypeError("set_lane_output needs value= (0..1) or real= (own units)")
        if scene is not None:
            if promote:
                self.set_lane_output_scene_mode(row, index, True)
            self.switch_scene(scene)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        oc = chain.output_control.add()
        oc.hash = self.LANE_OUTPUT_CONTROL
        prm = oc.params.add()
        prm.index = index
        prm.param_values.add().float_value = value
        return self._t.send(msg)

    def set_input_gate(self, row: int, param, value: float = None, real=None,
                       scene=None, promote: bool = True):
        """Set an Input Gate Control parameter on ``row``.

        Every grid row carries an Input Gate Control - the noise gate at the head of
        the row - in ``chain.input_control[]`` rather than ``chain.models[]``, so
        :meth:`set_param` cannot reach it. It is model ``28000``, present on all four
        rows, and it genuinely differs between factory presets, so reproducing a row
        faithfully means reproducing its gate.

        ``param`` is a name or a wire index. Three of the five are controls:

        * ``NOISE REDUCTION`` - the amount, ``real=`` in percent (0..100)
        * ``BYPASS`` - 1.0 bypasses the gate, 0.0 engages it
        * ``INPUT GAIN`` - ``real=`` in dB (-24..+24; 0.5 on the wire is 0 dB)

        ``GAIN REDUCTION`` is not a control but a METER: the catalog types it
        ``grMeter``, and it only ever holds 0.0 or 0.0011 (-39.96 dB on its -40..0
        range, i.e. no reduction). It is sampled when the preset is saved, so two
        saves of the same rig can differ there - worth knowing when comparing
        presets. Index 4 is unidentified and reads 0.0 everywhere.

        All three controls are confirmed writable on hardware in both directions,
        by a row-keyed ``Grid`` UPDATE into ``input_control`` - the same shape
        :meth:`set_lane_output` uses. (A full-preset :meth:`write_preset` reaches
        ``NOISE REDUCTION`` but not ``BYPASS``; that is the general rule that only
        keyed sparse updates persist, not anything specific to the gate.)

        Name a ``scene`` for a per-scene value, exactly as :meth:`set_param` does;
        confirmed on hardware, so a scene can open the gate that others keep shut.
        """
        index = param
        if isinstance(param, str) or real is not None:
            model = self.catalog[self.INPUT_GATE_CONTROL]
            spec = (model.parameter(param) if isinstance(param, str)
                    else model.parameters[param])
            index = spec.index
            if real is not None:
                value = spec.to_normalized(real)
        if value is None:
            raise TypeError("set_input_gate needs value= (0..1) or real= (own units)")
        if scene is not None:
            if promote:
                self._t.send(self._sub_param_message(
                    "input_control", self.INPUT_GATE_CONTROL, row, index,
                    scene_mode=True))
            self.switch_scene(scene)
        return self._t.send(self._sub_param_message(
            "input_control", self.INPUT_GATE_CONTROL, row, index, value=value))

    def set_split_mute(self, row: int, muted: bool = True):
        """Mute or unmute the split/mix path on ``row``.

        The manual lists a MUTE under both SPLITTER PARAMETERS and MIXER
        PARAMETERS. It is **one control**, not two: muting the splitter on the
        unit shows the mixer's MUTE already engaged (confirmed on the unit).
        Neither appears in the catalog's parameter list for either model, which
        is why it is here rather than a ``param`` on
        :meth:`set_splitter_param`.

        The write goes to ``Chain.splitBypass`` and the device reports the result
        in ``Chain.mixBypass`` - the same write-here/read-there split as
        ``combined_splitter`` versus ``splitter[]``. A write to ``mixBypass``
        does nothing.

        Both fields are ``repeated SceneBypass``, one entry per scene, but a
        single write sets **all eight**: it is not per-scene in practice.

        ``row`` must be 0 or 2, as for any splitter or mixer.
        """
        _require_even_row(row, "splitter or mixer")
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        chain.splitBypass.add().bypass = muted
        return self._t.send(msg)

    def set_stomp_assignment(self, row: int, column: int, footswitch):
        """Assign the block at ``row``/``column`` to a STOMP-mode footswitch.

        ``footswitch`` is a :class:`~pyquadcortex.enums.Footswitch` (or 0-7 for
        A-H). One footswitch may drive several blocks - factory content does
        this - so assigning does not displace anything else.

        Reproducing the unit's own two-message sequence, which is what makes it
        stick: a DELETE of any existing assignment for that cell, then the new
        one. Sending only the UPDATE leaves the old assignment in place.

        Read them back with :func:`stomp_assignments`.
        """
        self.clear_stomp_assignment(row, column)
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        a = msg.preset.stomp_mode_assignments.add()
        a.row = row
        a.column = column
        a.stomp_index = int(footswitch)
        return self._t.send(msg)

    def clear_stomp_assignment(self, row: int, column: int):
        """Unassign the block at ``row``/``column`` from its footswitch."""
        msg = pa.GridMessage(action=pa.MessageAction.DELETE)
        a = msg.preset.stomp_mode_assignments.add()
        a.row = row
        a.column = column
        return self._t.send(msg)

    def set_stomp_momentary(self, footswitch, momentary: bool = True):
        """Make a footswitch momentary rather than latching, for this preset.

        ``BinaryPreset.stomp_is_momentary`` is a map keyed by footswitch index.
        Confirmed writable by a ``Grid`` update carrying the map entry alone.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        msg.preset.stomp_is_momentary[int(footswitch)] = momentary
        return self._t.send(msg)

    def set_stomp_label(self, footswitch, label: str, single: bool = False):
        """Label a footswitch for this preset.

        Two maps exist: ``stomp_labels`` and ``single_stomp_labels``, the latter
        used when the footswitch drives exactly one block. The unit clears both
        when an assignment is removed. ``single=True`` writes the second.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        target = (msg.preset.single_stomp_labels if single
                  else msg.preset.stomp_labels)
        target[int(footswitch)] = label
        return self._t.send(msg)

    def set_expression(self, row: int, column: int, param, pedal: int = 1,
                       minimum: float = 0.0, maximum: float = 1.0, model=None):
        """Assign an expression pedal to one block parameter.

        ``pedal`` is 1 or 2, matching EXP 1 and EXP 2 on the back panel.
        ``minimum`` and ``maximum`` are the normalized 0..1 ends of the sweep;
        setting minimum above maximum reverses the pedal, which is how the
        manual describes inverting a parameter.

        Row/column-keyed like :meth:`set_param`, and confirmed on hardware both
        as the device's own broadcast when a pedal is assigned on the unit and
        as a host write surviving a save and read-back.

        Note the manual's warning: a parameter assigned to an expression pedal is
        excluded from Scene data and will not change when switching scenes.
        """
        index = self._resolve_param_index(param, model) if not isinstance(param, int) \
            else param
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        p = model_msg.params.add()
        p.index = index
        p.expression = int(pedal)
        p.expression_min = minimum
        p.expression_max = maximum
        return self._t.send(msg)

    def set_midi_out(self, source, messages):
        """Set the MIDI messages a footswitch or expression pedal sends.

        ``source`` is a :class:`~pyquadcortex.enums.MidiSource` - footswitches A-H
        (0-7) or the two expression pedals (8, 9). ``messages`` is a list of
        :class:`MidiOut` entries, up to 12; the list REPLACES whatever that source
        had.

        These do NOT travel by ``Grid``. The preset stores them in
        ``midi_messages_general_v2``, but a ``Grid`` update carrying that field is
        accepted and ignored - ``MIDISettings`` is what applies them::

            qc.set_midi_out(MidiSource.FOOTSWITCH_A, [MidiOut.cc(channel=3, cc=10, value=64)])

        Confirmed on hardware: the values survive a save and read-back, and the
        120-slot array is 10 sources x 12 messages, so source N occupies slots
        ``N*12`` onward. The device mirrors the first message of each source into
        the 10-slot legacy ``midi_messages_general``.

        A ``MIDISettings`` READ gets no reply on this firmware, so read the saved
        preset to verify rather than asking the device.
        """
        return self._midi_settings("general_midi_messages", source, messages)

    def set_preset_load_midi_out(self, messages):
        """Set the MIDI messages sent when this preset is loaded (up to 12).

        Same mechanism as :meth:`set_midi_out`; these land in
        ``BinaryPreset.midi_messages``.
        """
        return self._midi_settings("preset_load_messages", 0, messages)

    def _midi_settings(self, field: str, source, messages):
        msg = pa.MIDISettingsMessage(action=pa.MessageAction.UPDATE)
        group = getattr(msg, field).messages.add()
        group.source = int(source)
        for m in messages:
            entry = group.msg.add()
            entry.type = int(m.type)
            entry.channel = int(m.channel)
            entry.param1 = int(m.param1)
            entry.param2 = int(m.param2)
            entry.param3 = int(m.param3)
        return self._t.send(msg)

    def set_lane_output_scene_mode(self, row: int, param_index: int,
                                   enabled: bool = True):
        """Make a Lane Output Control parameter follow scenes.

        The :meth:`set_param_scene_mode` of ``output_control``, and subject to the
        same rule: the flag must travel alone. :meth:`set_lane_output` sequences it
        for you when you name a scene.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        oc = chain.output_control.add()
        oc.hash = self.LANE_OUTPUT_CONTROL
        prm = oc.params.add()
        prm.index = param_index
        prm.scene_mode = enabled
        return self._t.send(msg)

    def wait_for_listing(self, setlist: str = Setlist.USER, until=None,
                         timeout: float = 45.0, interval: float = 2.0):
        """Re-list ``setlist`` until ``until(entries)`` holds, and return them.

        File operations are eventually consistent, and the lag scales with how
        many you performed: a single delete settles in a few seconds, but after
        eleven deletes a listing five seconds later still showed all eleven
        presets - they had in fact all gone. A fixed sleep therefore produces
        false negatives, which in a careful script reads as "the clear failed"
        on work that actually succeeded.

        Poll instead::

            # wait for a save to appear
            qc.wait_for_listing(Setlist.USER,
                                until=lambda e: any(p.name == "My Patch" for p in e))

            # wait for a bulk delete to settle
            qc.wait_for_listing(Setlist.USER, until=lambda e: not e)

        With no ``until``, this waits for two consecutive identical listings,
        which is the general "has it stopped changing?" question.

        **A missed push is ridden out, not raised.** The device sometimes goes quiet
        for a polling interval and the underlying listing request times out; that is
        the transient this method exists to absorb, so it keeps polling until its own
        ``timeout``. You do not need to wrap this in your own retry loop.

        Two different ``TimeoutError`` diagnoses come out of it, which are worth
        telling apart: *the condition never became true* means listings arrived and
        your predicate stayed false, whereas *the device stopped pushing listings*
        means nothing was ever evaluated - so the latter says nothing about whether
        your change landed.
        """
        deadline = time.monotonic() + timeout
        previous = None
        listings_seen = 0
        missed_pushes = 0
        while True:
            try:
                entries = self.list_presets(setlist)
            except TimeoutError:
                # A missed File push is exactly the transient this method exists to
                # ride out. Surfacing it would produce the very false negative the
                # docstring warns about - one quiet polling interval aborting a wait
                # for work that already succeeded. Keep polling until OUR deadline.
                missed_pushes += 1
            else:
                listings_seen += 1
                if until is not None:
                    if until(entries):
                        return entries
                else:
                    signature = [(e.index, e.name) for e in entries]
                    if previous is not None and signature == previous:
                        return entries
                    previous = signature
            if time.monotonic() >= deadline:
                if listings_seen == 0:
                    # Different diagnosis: we never saw a listing at all.
                    raise TimeoutError(
                        f"the device stopped pushing listings for {str(setlist)!r}: "
                        f"{missed_pushes} attempt(s) in {timeout}s each timed out "
                        "waiting for a File broadcast. The condition was never "
                        "evaluated, so this says nothing about whether your change "
                        "landed."
                    )
                raise TimeoutError(
                    f"the condition never became true for {str(setlist)!r} within "
                    f"{timeout}s ({listings_seen} listing(s) checked"
                    + (f", {missed_pushes} missed push(es) ridden out"
                       if missed_pushes else "") + ")"
                )
            time.sleep(interval)

    def _file_operation(self, msg, timeout: float = 5.0):
        """Send a File message, tolerating the device not replying.

        File operations are asynchronous and this protocol STALLs every host
        write, so a missing reply says nothing about whether the operation
        worked - the device may simply not answer. Treating that as an error made
        callers wrap every save and delete in ``try/except TimeoutError`` and
        verify by re-reading anyway, which is the same principle the transport
        already applies to the benign write stall.

        Returns the device's reply if one arrives, else ``None``. Either way,
        DEVICE STATE IS THE ARBITER: re-read to confirm (see
        :meth:`wait_for_listing`).
        """
        try:
            return self._t.request(msg, timeout=timeout)
        except TimeoutError:
            return None

    def save_current_preset(
        self,
        setlist_path: str,
        position,
        name: str,
        instrument: int = Instrument.NONE,
        default_scene=None,
        confirm: bool = False,
        confirm_timeout: float = 20.0,
    ):
        """Save the preset currently on the grid into a setlist slot ("Save As").

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"30A"``). Saving OVERWRITES whatever occupies that slot.

        ``default_scene`` sets which scene the preset comes up in. There is no
        field for it in the File message: the device records whichever scene is
        ACTIVE at save time, so this switches to that scene first and the saved
        preset's ``default_scene`` reads back accordingly. Note the side effect -
        the unit is left on that scene.

        **The device may not use the name you asked for.** If the setlist already
        contains a preset of that name, it de-duplicates: the base is truncated as
        needed and a ``_N`` suffix appended, to 20 characters total, so saving a
        second ``"Cali Basswalk [Ret1]"`` yields ``"Cali Basswalk [Ret_1"``. A
        unique name is stored verbatim and is not length-limited (36 characters
        was stored intact). If the resulting name matters, read the slot back and
        use what the device reports.

        Confirmed by capture: Cortex Control's "Save As" is a
        ``FileMessage`` with action CREATE (unset, the default), ``type: 0``,
        and NO preset payload - the device saves the preset it already has on
        the grid. The target slot is addressed inside ``folder``: the setlist's
        device path in ``folder.key`` and one ``files`` entry carrying the
        LINEAR slot index (bank*8 + letter, zero-based: "28E" == 220), the
        preset name, and the instrument tag (captured save sent
        ``instrument: 2``). Saving to slot 28E as "Test save to user sl" sent
        exactly ``{folder{key: "/media/p4/Presets/My Presets",
        is_factory: false, files{index: 220, name: ..., instrument: 2}}}``.

        ``instrument`` is the tag the unit filters on, and is the only preset
        metadata this can set. The descriptive ``tags`` a factory preset carries
        ('Clean', 'Crunch') are NOT reproduced: a preset saved this way reads back
        with an EMPTY tag list whatever its source had, and no route to them was
        found on this firmware - not ``ProductData.tags`` on this message, not a
        File UPDATE carrying them, not a ``Grid`` UPDATE carrying
        ``preset.tags``. All three are accepted and leave the list empty. Nothing
        stale is inherited, so a derived preset is simply untagged.
        """
        if default_scene is not None:
            # No field carries this; the device takes the active scene at save time.
            self.switch_scene(default_scene)
        msg = pa.FileMessage(type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        entry = msg.folder.files.add()
        entry.index = _as_position(position)
        entry.name = name
        entry.instrument = instrument
        self._file_operation(msg)
        if not confirm:
            return name
        # The device renames on a collision, so the only way to know what it
        # actually stored is to ask it.
        try:
            entries = self.wait_for_listing(
                setlist_path,
                until=lambda es: any(e.index == entry.index and e.name for e in es),
                timeout=confirm_timeout,
            )
        except TimeoutError:
            return None
        for e in entries:
            if e.index == entry.index:
                return e.name
        return None

    def delete_preset(self, setlist_path: str, name: str):
        """Delete the preset named ``name`` from the setlist at ``setlist_path``.

        Confirmed by capture: deleting "Test save to user
        sl" from slot 28E sent ``File{action: DELETE, type: 0, folder{key:
        <setlist path>, is_factory: false, files{key: "<setlist
        path>/<name>.pb"}}}`` - the preset is addressed by its device FILE
        PATH (name-based, ``.pb`` extension), NOT by slot index.
        """
        msg = pa.FileMessage(action=pa.MessageAction.DELETE, type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        msg.folder.files.add().key = f"{setlist_path}/{name}.pb"
        return self._file_operation(msg)

    def move_preset(self, setlist_path: str, name: str, to_position):
        """Move the preset named ``name`` to slot ``to_position`` (same setlist).

        ``to_position`` is either the linear slot index or the slot name shown on
        the unit (``"28D"``).

        Confirmed by capture: dragging "Darkglass AO900
        2_1" onto slot 28D sent ``File{action: MOVE, type: 0, folder{key:
        <setlist path>, files{key: "<setlist path>/<name>.pb"}},
        to_folder{key: <setlist path>, files{index: 219}}}`` - source by FILE
        PATH, destination by LINEAR slot index.
        """
        msg = pa.FileMessage(action=pa.MessageAction.MOVE, type=0)
        msg.folder.key = setlist_path
        msg.folder.is_factory = False
        msg.folder.files.add().key = f"{setlist_path}/{name}.pb"
        msg.to_folder.key = setlist_path
        msg.to_folder.files.add().index = _as_position(to_position)
        return self._file_operation(msg)


def field_present(message, field: str) -> bool:
    """Whether ``field`` is set on ``message``, without raising.

    ``HasField`` is the natural way to read this schema, because most fields in a
    preset payload sit in a synthetic ``oneof`` and absent means "not addressed".
    But presence is not universal: ``HasField`` raises ``ValueError`` on a field
    that has none, and the schema has plenty - ``SceneBypass.bypass`` is the one
    that bites, since walking per-scene bypass is a common thing to want::

        # raises ValueError: Field SceneBypass.bypass does not have presence
        entry.HasField("bypass")

        field_present(entry, "bypass")      # False, and no exception

    For a field without presence this returns ``False``, since the wire cannot
    distinguish "absent" from "zero" there anyway. See ``docs/protocol.md`` for
    which fields those are.
    """
    try:
        return message.HasField(field)
    except ValueError:
        return False


class Block(NamedTuple):
    """One occupied grid cell: where it is, and what is in it."""

    row: int
    column: int
    model_id: int


def blocks(p: preset.BinaryPreset) -> list:
    """The OCCUPIED grid cells of ``p``, as :class:`Block` entries.

    Use this rather than walking ``chains[].models[]`` yourself. The device
    reports every row as its full complement of **8 column slots**, with empty
    ones present as ``Model`` entries whose ``hash`` is absent or zero, so
    ``len(chain.models)`` is 8 for every row - including entirely empty rows - and
    is not a block count. ``output_control`` and ``input_control`` are padded the
    same way, one entry on every row. ``splitter``, ``mixer``,
    ``combined_splitter`` and ``split_control_points`` are NOT: they exist only on
    rows 0 and 2, and are empty on rows 1 and 3, because a branch can only
    originate on an even row with its lane on the row below.

    Nor is ``in_portid`` a usable occupancy signal: ``Input.EMPTY`` means "not fed
    from a physical jack", which is the normal state of any row that is not an
    input row, occupied or not. Factory "Brit 2203" has six blocks on row 2 with
    ``in_portid`` EMPTY.
    """
    found = []
    for i, chain in enumerate(p.chains):
        row = chain.row if field_present(chain, "row") else i
        for j, model in enumerate(chain.models):
            if not (field_present(model, "hash") and model.hash):
                continue
            column = model.column if field_present(model, "column") else j
            found.append(Block(row=row, column=column, model_id=model.hash))
    return found


class Split(NamedTuple):
    """Where a row branches into a parallel lane, and where it recombines.

    ``mix_column`` is ``-1`` for a branch that never recombines, so prefer
    :attr:`rejoins` over testing the number. :attr:`lane_row` is the row the
    parallel lane occupies.
    """

    row: int
    split_column: int
    mix_column: int

    @property
    def rejoins(self) -> bool:
        """Whether the parallel lane recombines into this row."""
        return self.mix_column >= 0

    @property
    def lane_row(self) -> int:
        """The row carrying this branch's parallel lane."""
        return self.row + 1


def splits(p: preset.BinaryPreset) -> list:
    """Where each row branches into a parallel lane, as :class:`Split` entries.

    This is the readable half of the grid topology. It does NOT live on the
    splitter block - that carries no ``column`` at all - but in
    ``Chain.split_control_points``, whose ``split`` and ``mix`` fields give the
    columns where the lane leaves and rejoins. Those fields have **no presence**,
    so ``HasField`` (and therefore :func:`field_present`) reports them missing
    even when set; read them directly, as this does.

    A branch is present when ``split >= 0``. ``mix`` is independent: a lane may
    never recombine, and reports ``-1`` when it does not, so test
    :attr:`Split.rejoins` rather than the column. Factory "Strat Ambience" (05B)
    branches at column 2 and never rejoins; "Darkglass AO900 1" (27H) branches and
    rejoins at column 4 on rows 0 and 2. A row with no branch reports ``-1`` for
    both and is omitted.

    The parallel lane occupies :attr:`Split.lane_row`, the row BELOW the branch,
    which is spoken for whether or not it holds blocks - see :func:`free_rows`.
    Only rows 0 and 2 can carry a branch at all.
    """
    found = []
    for i, chain in enumerate(p.chains):
        row = chain.row if field_present(chain, "row") else i
        for scp in chain.split_control_points:
            # -1 means "no branch here" - factory "Brit 2203" reports (-1, -1) on
            # its serial rows. `mix` alone being -1 is a branch that never rejoins.
            if scp.split < 0:
                continue
            found.append(Split(row=row, split_column=scp.split, mix_column=scp.mix))
    return found


class MidiOut(NamedTuple):
    """One per-preset MIDI Out message.

    The wire carries a generic ``{type, channel, param1, param2, param3}``, so
    build these with :meth:`cc` or :meth:`pc` rather than by hand - what the
    three params mean depends on the type. Both mappings are confirmed by
    entering a message on the unit and reading the saved preset.
    """

    type: int
    channel: int
    param1: int = 0
    param2: int = 0
    param3: int = 0

    @classmethod
    def cc(cls, channel: int, cc: int, value: int):
        """A Control Change sending one value, for a footswitch source.

        ``param1`` is the CC number and ``param2`` the value.
        """
        return cls(type=MidiOutType.CC, channel=channel, param1=cc, param2=value)

    @classmethod
    def cc_toggle(cls, channel: int, cc: int, minimum: int, maximum: int):
        """A Control Change that alternates between two values on each press.

        ``param2`` and ``param3`` are the MIN and MAX the manual describes.
        Confirmed: entering CC Toggle on the unit stored ``type: 2`` with
        ``param2: 5, param3: 120`` for a 5/120 range.
        """
        return cls(type=MidiOutType.CC_TOGGLE, channel=channel, param1=cc,
                   param2=minimum, param3=maximum)

    @classmethod
    def expression_cc(cls, channel: int, cc: int, minimum: int, maximum: int):
        """A Control Change swept by an expression pedal.

        An expression source sends a RANGE rather than a single value, so the
        unit asks for min and max even for a plain CC: the stored message is
        ``type: 1`` with ``param2``/``param3`` holding the ends of the sweep.
        Use this for :attr:`~pyquadcortex.enums.MidiSource.EXPRESSION_1` and
        ``EXPRESSION_2``; use :meth:`cc` for a footswitch.
        """
        return cls(type=MidiOutType.CC, channel=channel, param1=cc,
                   param2=minimum, param3=maximum)

    @classmethod
    def pc(cls, channel: int, program: int, bank_msb: int = 0, bank_lsb: int = 0):
        """A Program Change: ``param1``/``param2`` are the bank select bytes
        (CC#0 and CC#32) and ``param3`` the program number."""
        return cls(type=MidiOutType.PC, channel=channel, param1=bank_msb,
                   param2=bank_lsb, param3=program)


class StompAssignment(NamedTuple):
    """A block bound to a STOMP-mode footswitch."""

    row: int
    column: int
    footswitch: int


def stomp_assignments(p: preset.BinaryPreset) -> list:
    """Which blocks are bound to which footswitches, as :class:`StompAssignment`.

    Note that ``row``, ``column`` and ``stomp_index`` all lack presence, so a
    zero is indistinguishable from unset - an entry for row 0, column 0,
    footswitch A reads as a bare, apparently empty entry. Factory content
    populates this: "Darkglass AO900 2" binds eight blocks to A-H.
    """
    return [StompAssignment(row=a.row, column=a.column, footswitch=a.stomp_index)
            for a in p.stomp_mode_assignments]


def midi_out(p: preset.BinaryPreset, source=None) -> dict:
    """The per-preset MIDI Out messages, keyed by :class:`MidiSource`.

    Reads the 120-slot ``midi_messages_general_v2`` as 10 sources x 12 messages.
    Pass ``source`` to get one source's list instead of the whole map. Empty
    slots are dropped, so a source with nothing assigned is absent.
    """
    out = {}
    for i, m in enumerate(p.midi_messages_general_v2):
        if not (m.type or m.channel or m.param1 or m.param2 or m.param3):
            continue
        out.setdefault(i // 12, []).append(
            MidiOut(type=m.type, channel=m.channel, param1=m.param1,
                    param2=m.param2, param3=m.param3))
    if source is not None:
        return out.get(int(source), [])
    return out


def preset_load_midi_out(p: preset.BinaryPreset) -> list:
    """The MIDI messages this preset sends when it is loaded."""
    return [MidiOut(type=m.type, channel=m.channel, param1=m.param1,
                    param2=m.param2, param3=m.param3)
            for m in p.midi_messages
            if m.type or m.channel or m.param1 or m.param2 or m.param3]


def param_options(p: preset.BinaryPreset, row: int, column: int,
                  param_index: int) -> list:
    """The option names of a list-valued parameter, from the preset.

    A comboBox parameter's options are NOT in the device catalog, which gives
    only ``min``, ``max`` and ``steps`` - but the preset carries the rendered
    list in ``Param.dynamic_steps``. Reading factory "US TWN Vibrato" (01C), the
    Doubler's TRIGGER options are ``Off, Follow Input, Input 1, Input 2, Input
    1/2, Return 1, Return 2, Return 1/2, USB input 5..8, ...``.

    Some of those lists include one entry per block in the preset, which is why
    such a parameter's stored value changes when the block count does.
    """
    for i, chain in enumerate(p.chains):
        if (chain.row if field_present(chain, "row") else i) != row:
            continue
        for j, model in enumerate(chain.models):
            if (model.column if field_present(model, "column") else j) != column:
                continue
            if param_index < len(model.params):
                return list(model.params[param_index].dynamic_steps)
    return []


def free_rows(p: preset.BinaryPreset) -> list:
    """The rows of ``p`` available for an independent chain, lowest first.

    A row is free when it holds no blocks AND is not the parallel lane of a branch
    on the row above. The second half is the part that bites: building on the lane
    row of a branch puts blocks inside the existing chain's parallel path rather
    than beside it, and the lane row is frequently empty, so block count alone says
    a row is free when it is not. Factory "Strat Ambience" (05B) branches on row 0
    and holds nothing on row 1; row 1 is not free.
    """
    used = {b.row for b in blocks(p)}
    lanes = {s.lane_row for s in splits(p)}
    return [row for row in range(len(p.chains)) if row not in used | lanes]


def _is_factory_setlist(setlist_path: str) -> bool:
    """Whether ``setlist_path`` is the factory library.

    Compared with trailing slashes normalized: the factory path carries one for
    recalls but the device omits it elsewhere (see :class:`Setlist`).
    """
    return str(setlist_path).rstrip("/") == str(Setlist.FACTORY).rstrip("/")


def _as_position(position) -> int:
    """Accept either a linear slot index or a slot name like ``"28C"``."""
    if isinstance(position, str):
        return slot_to_position(position)
    return int(position)


def slot_to_position(slot: str) -> int:
    """Convert a QC slot name like ``"28C"`` to its linear wire position.

    Confirmed by capture: the wire ``position`` is zero-based
    ``(bank - 1) * 8 + letter`` with A=0..H=7; recalling "28C" sent 218 and
    saving to "28E" sent 220.

    A zero-padded bank is accepted (``"01A"`` and ``"1A"`` are the same slot).
    Note that :func:`position_to_slot` returns the UNPADDED form by default,
    because that is what the unit displays - so comparing its output against a
    padded string never matches. Compare linear positions instead, or ask for
    ``position_to_slot(pos, pad=True)``.
    """
    slot = slot.strip().upper()
    if len(slot) < 2 or not slot[:-1].isdigit() or slot[-1] not in "ABCDEFGH":
        raise ValueError(f"slot must look like '28C' (bank number + letter A-H): {slot!r}")
    bank = int(slot[:-1])
    if not 1 <= bank <= BANKS:
        # A setlist is exactly 256 slots, so bank 33 does not exist. Accepting it
        # silently produced a position of 256, the device ignored the save, and the
        # failure surfaced much later as a listing that never showed the preset.
        raise ValueError(
            f"bank must be 1 to {BANKS} (a setlist holds {BANKS * 8} slots): {slot!r}")
    return (bank - 1) * 8 + (ord(slot[-1]) - ord("A"))


def input_chain_rows(p: preset.BinaryPreset, from_port: int = Input.INPUT_1) -> list:
    """Return the grid rows whose input chain is on ``from_port``.

    A chain's grid row is its explicit ``row`` when set, else its index in
    ``p.chains``. A recalled preset never carries ``row``, so in practice the
    index is the row. Feed each returned row to
    :meth:`QuadCortex.set_chain_input` to re-point it, then save.

    Worked example, factory "Brit 2203": four chains, none with an explicit
    ``row``; ``chains[0]`` is on ``INPUT_1`` with 8 blocks, ``chains[2]`` holds 6
    blocks but reads ``EMPTY`` because it is fed internally by the splitter, and
    ``chains[1]`` and ``chains[3]`` are empty. So this returns ``[0]``.

    Note what that means: a row being ``EMPTY`` says nothing about whether it
    holds blocks. Use :func:`blocks` for occupancy.
    """
    rows = []
    for i, chain in enumerate(p.chains):
        if chain.HasField("in_portid") and chain.in_portid == from_port:
            rows.append(chain.row if chain.HasField("row") else i)
    return rows

BANKS = 32
SLOTS_PER_BANK = 8
SETLIST_SLOTS = BANKS * SLOTS_PER_BANK


def position_to_slot(position: int, pad: bool = False) -> str:
    """Turn a linear slot index into the slot name shown on the unit.

    The inverse of :func:`slot_to_position`: ``218 -> "28C"``. Anything reporting
    results to a person wants this, because the unit talks in slot names while the
    wire talks in indices.

    The default output is UNPADDED (``0 -> "1A"``), matching what the unit
    displays. `slot_to_position` also accepts the padded ``"01A"``, so the two are
    not symmetric: comparing this output against a padded string silently never
    matches, and the usual symptom is a listing wait that times out on a save that
    actually worked. Either pass ``pad=True`` for ``"01A"``, or - better - compare
    linear positions and avoid the question.
    """
    position = int(position)
    if not 0 <= position < SETLIST_SLOTS:
        raise ValueError(
            f"slot position must be 0 to {SETLIST_SLOTS - 1}: {position}")
    bank, letter = divmod(position, SLOTS_PER_BANK)
    return f"{bank + 1:02d}{'ABCDEFGH'[letter]}" if pad else \
           f"{bank + 1}{'ABCDEFGH'[letter]}"


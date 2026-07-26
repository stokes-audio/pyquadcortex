"""High-level QuadCortex client for pyquadcortex.

This is the ergonomic API a caller (CLI, script) uses to control a Quad Cortex.
It builds protobuf messages and hands them to a ``transport``-like object,
which is dependency-injected via the constructor. The transport exposes:

  * ``send(message)``                  - fire-and-forget
  * ``request(message, timeout=...)``  - send and block for the correlated reply

The client deliberately knows NOTHING about hidapi, HID reports, or the framing
layer: it only speaks protobuf messages. That keeps this layer testable with a
fake transport and keeps all wire concerns in ``framing``/``transport``.

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
from pyquadcortex.enums import Input, Instrument, Output, Setlist  # noqa: F401
from pyquadcortex.proto import ProductionAutomation_pb2 as pa
from pyquadcortex.proto import Preset_pb2 as preset


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
        waits for the listing whose key matches ``setlist``. (The device sends
        each setlist's listing more than once; the first is complete, so the
        duplicates are ignored.)

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

        Either way the scene's LABEL travels with its bypass and parameter state:
        a copy renames the destination scene, and a swap exchanges both labels.
        """
        return self._t.send(
            pa.SceneCopyMessage(
                action=pa.MessageAction.UPDATE,
                from_index=from_index,
                to_index=to_index,
                is_swap=swap,
            )
        )

    def set_scene_label(self, scene_index: int, label: str):
        """Rename a scene. Confirmed shape: ``SceneLabel{action:
        UPDATE, index, label}`` (observed as the device's broadcast when a
        scene was renamed on the unit)."""
        return self._t.send(
            pa.SceneLabelMessage(
                action=pa.MessageAction.UPDATE, index=scene_index, label=label
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
                  real=None, promote: bool = True):
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
        """
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
        write and the flag is dropped - which is why this looked read-only.
        :meth:`set_param` sequences that for you when you name a scene.

        Save the grid afterwards to keep it.
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

    def set_block(self, row: int, column: int, model):
        """Put ``model`` in the grid cell at ``row``/``column``.

        Creates a block in an empty cell and replaces whatever is in an occupied
        one - the device makes no distinction. ``model`` is a model id or a
        :class:`~pyquadcortex.catalog.Model`; :mod:`pyquadcortex.models` has
        constants for the factory blocks, and :attr:`catalog` resolves anything
        installed on the unit, including purchased models and Neural Captures.

        Confirmed on hardware, and matching the device's own broadcast when a
        block is added on the unit: ``Grid{UPDATE, preset{chains{row,
        models{column, hash}}}}``. Save the grid afterwards to keep it.
        """
        msg = pa.GridMessage(action=pa.MessageAction.UPDATE)
        chain = msg.preset.chains.add()
        chain.row = row
        model_msg = chain.models.add()
        model_msg.column = column
        model_msg.hash = int(getattr(model, "id", model))
        return self._t.send(msg)

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

    def set_lane_output(self, row: int, param, value: float = None, real=None,
                        scene=None, promote: bool = True):
        """Set a Lane Output Control parameter on ``row``.

        Every grid row carries a Lane Output Control block - the VOLUME, PAN, MUTE
        and SOLO the manual describes - and it lives in ``chain.output_control[]``
        rather than ``chain.models[]``, so :meth:`set_param` cannot reach it. It is
        model ``23000``, present and populated on all four rows whether or not the
        row has any blocks.

        ``param`` is a name (``"VOLUME"``, ``"PAN"``, ``"MUTE"``, ``"SOLO"``) or a
        wire index. Pass ``value`` as the normalized 0..1 the wire carries, or
        ``real`` in the parameter's own units, converted through the catalog::

            qc.set_lane_output(row=0, param="PAN", value=0.5)      # centre
            qc.set_lane_output(row=0, param="VOLUME", real=-3.0)   # dB

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
        which is the general "has it stopped changing?" question. Raises
        ``TimeoutError`` if the condition never holds.
        """
        deadline = time.monotonic() + timeout
        previous = None
        while True:
            entries = self.list_presets(setlist)
            if until is not None:
                if until(entries):
                    return entries
            else:
                signature = [(e.index, e.name) for e in entries]
                if previous is not None and signature == previous:
                    return entries
                previous = signature
            if time.monotonic() >= deadline:
                raise TimeoutError(
                    f"listing for {str(setlist)!r} did not settle within {timeout}s"
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
        confirm: bool = False,
        confirm_timeout: float = 20.0,
    ):
        """Save the preset currently on the grid into a setlist slot ("Save As").

        ``position`` is either the linear slot index or the slot name shown on
        the unit (``"30A"``). Saving OVERWRITES whatever occupies that slot.

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
        """
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
    is not a block count. The same padding applies to ``splitter``, ``mixer``,
    ``output_control`` and ``input_control``.

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
    """
    slot = slot.strip().upper()
    if len(slot) < 2 or not slot[:-1].isdigit() or slot[-1] not in "ABCDEFGH":
        raise ValueError(f"slot must look like '28C' (bank number + letter A-H): {slot!r}")
    bank = int(slot[:-1])
    if bank < 1:
        raise ValueError(f"bank must be >= 1: {slot!r}")
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

def position_to_slot(position: int) -> str:
    """Turn a linear slot index into the slot name shown on the unit.

    The inverse of :func:`slot_to_position`: ``218 -> "28C"``. Anything reporting
    results to a person wants this, because the unit talks in slot names while the
    wire talks in indices.
    """
    position = int(position)
    if position < 0:
        raise ValueError(f"slot position cannot be negative: {position}")
    bank, letter = divmod(position, 8)
    return f"{bank + 1}{'ABCDEFGH'[letter]}"


# Quad Cortex HID framing spec

Working notes on the framing of the QC's USB-HID protobuf transport, written
during protocol development.

> Note: references below to `scripts/*.py` (for example `passive_listen.py`,
> `discover_framing.py`, `verify_*.py`) are development-only tools used while
> working out the protocol; they are not part of this package. Only
> `scripts/compile_protos.sh` and the runnable `examples/` ship here.

## Phase 1 observations (2026-07-17)

Attempted passive listen via `scripts/passive_listen.py` (hidapi through the
PyPI `hid` package, libhidapi 0.15.x from Homebrew) while Cortex Control was
running and connected.

### Result: open blocked - no reports observed

`hid.Device(0x152A, 0x880A)` failed with:

```
hid.HIDException: unable to open device: hid_open_path: failed to open
IOHIDDevice from mach entry: (0xE00002C5) (iokit/common) exclusive access
and device already open
```

Details that pin down the cause:

- hidapi's macOS default is EXCLUSIVE (seize) open: `hid_darwin_get_open_exclusive()`
  returned 1 out of the box. (Contrary to the plan's assumption that the
  default is non-exclusive.)
- We set `hid_darwin_set_open_exclusive(0)` (verified the getter then returned
  0) and retried: the open still failed with the same `kIOReturnExclusiveAccess`
  (0xE00002C5) error.
- A shared-mode open only fails this way when another client already holds the
  device seized. IORegistry confirms who: the QC HID interface
  (`Quad Cortex HID interface@5` -> `AppleUserHIDDevice`) has exactly one
  `IOHIDLibUserClient`, with `"IOUserClientCreator" = "pid 98673, Cortex Control"`.

Conclusion: **Cortex Control opens the QC HID interface with
kIOHIDOptionsTypeSeizeDevice (exclusive). While it is running, no other
userspace process can open the device at all, even read-only/shared.**
Concurrent passive sniffing via a second hidapi handle is not possible on
macOS. Per task safety rules we did not retry aggressively, did not attempt
seize mode ourselves, and did not touch Cortex Control.

### Not yet observed (blocked)

- Input report length(s): unknown (expected 64 per plan; unverified)
- Report rate: unknown
- Report-ID prefix present/absent: unknown
- Sample hex lines: none captured

### Implications for Phase 2

- HID-level capture of a live Cortex Control session (Task 2.1) cannot be done
  by opening the device alongside Cortex Control. Options:
  1. Capture below the HID layer while Cortex Control runs, e.g. Apple's
     `IOUSBHostFamily`/`usb` logging or a hardware/VM USB sniffer.
  2. Run `passive_listen.py` while Cortex Control is NOT running to
     characterize device-initiated traffic (requires operator to quit Cortex
     Control - out of scope for this passive task).
- `passive_listen.py` already forces shared (non-exclusive) open mode, so it
  is ready to coexist with other non-seizing clients once the device is free.

Environment: macOS (Darwin 25.5.0), Cortex Control running and connected for
the whole observation window.

## Phase 2 observations (2026-07-17)

### a) Cortex Control quit to free the device

Cortex Control was quit gracefully at 2026-07-17 20:30:33 CDT via
`osascript -e 'tell application "Cortex Control" to quit'` (it was pid 98673).
Verified exited a few seconds later (`pgrep -fil "Cortex Control"` returned
nothing). The controller is expected to relaunch it at session end.

### b) HID report descriptor (extracted via IORegistry, no device open)

The QC HID interface's report descriptor was read from IORegistry
(`kIOHIDReportDescriptorKey`), so no device open was needed.

Raw descriptor hex:

```
05010900a101150026ff008501750895800900818285027508958009009182c0
```

Decoded:

- Usage Page: Generic Desktop (0x01), Usage 0x00, Application collection.
- Report ID 0x01: INPUT, 128 bytes of 8-bit data (device-to-host).
- Report ID 0x02: OUTPUT, 128 bytes of 8-bit data (host-to-device).
- No feature reports (MaxFeatureReportSize = 1). IORegistry reports
  MaxInputReportSize = 129 and MaxOutputReportSize = 129 (128 payload bytes +
  1 report-ID byte).

Implications:

- HID-level framing is a fixed 128-byte payload per report.
- Host writes must prefix report ID 0x02.
- Reads on hidapi present report ID 0x01 as the first byte (hidapi includes
  the report-ID byte for numbered reports).

Remaining unknown: the inner envelope within the 128-byte payload (message-type
tag, length field, and how protobuf payloads larger than 128 bytes are
chunked across multiple reports). To be determined in Task 2.2 via the plan's
Step 2b fallback (READ a VersionMessage under candidate envelope hypotheses).

### c) Idle-listen observation on the freed device

With Cortex Control quit, `scripts/passive_listen.py --seconds 30 --out
captures/freed-idle-01.txt` was run (start 2026-07-17 20:30:57 CDT). Results:

- The device open now SUCCEEDS (shared mode): the script printed
  `opened Neural DSP Quad Cortex`. This confirms the Phase 1 conclusion that
  the earlier failure was solely Cortex Control's exclusive seize, not a
  permissions/entitlement problem.
- Over the full 30 s window, ZERO input reports arrived
  (`total reports: 0`; `captures/freed-idle-01.txt` is empty).

Interpretation: the QC does not emit unsolicited input reports while idle with
no host session driving it. Device-to-host traffic is very likely
request/response-driven - it responds to host output reports rather than
free-running. This is a valid and useful observation: brute-force framing
discovery (Task 2.2) will need to SEND a candidate request and read the reply,
because passively waiting yields nothing.

### d) `strings` peek at the Cortex Control binary (time-boxed, ~5 min)

Grepped `/Applications/Neural DSP/Cortex Control.app/Contents/MacOS/Cortex
Control` for framing hints. Suggestive (not conclusive) findings:

- Confirms protobuf transport: `message_type` and
  `google::protobuf::TextFormat` symbols present. `message_type` hints the
  inner envelope carries a message-type tag/field.
- Chunking of large payloads is plausible: strings `ChunkID`, `ChunkStart`,
  `DeviceReportStream`, and " binary chunks" appear - consistent with
  splitting >128-byte protobuf messages across multiple 128-byte reports.
- HID plumbing named threads/counters: `HidConnectionThread`, `HidDataHandler`,
  `HidReceiverThread`, `HidSenderThread`, and diagnostic counters
  `usbHIDInCount`, `usbHIDInDroppedCount`, `usbHIDOutCount`,
  `usbHIDOutDroppedCount`. Error format `IOHIDDeviceSetReport failed:
  (0x%08X) %s` confirms it drives writes via IOHIDDeviceSetReport (output
  reports), matching the report-ID-0x02 OUTPUT descriptor above.
- No explicit length-field/offset string was found that pins down the inner
  envelope byte layout; that still has to be resolved empirically in Task 2.2.

## Phase 2 framing discovery attempt (2026-07-17) - BLOCKED on host->device writes

Task 2.2 tried the plan's Step 2b fallback: with Cortex Control quit and the
device free, send the smallest NON-MUTATING request (`VersionMessage` with
`action=READ`, protobuf bytes `0803`) under candidate envelope layouts over HID
output report 0x02, and read input report 0x01 for a reply. Script:
`scripts/discover_framing.py`.

**Every host->device output report is rejected by the OS/USB stack before it
reaches the device**, so the inner envelope could not be probed at all.

### The blocker: SET_REPORT (output) fails with IOKit USB error 0xE0005000

The device opens fine (exclusive/seize, now that Cortex Control is quit) and
control-pipe reads work, but the first and every subsequent output report fails:

```
hid.HIDException: IOHIDDeviceSetReport failed: (0xE0005000) unknown error code
```

Decoding `0xE0005000` as an IOKit return code: system field `0x38` (`sys_iokit`),
subsystem `0x1` (`sub_iokit_usb`), code `0x1000`. A USB-subsystem error on the
OUT transfer - consistent with the device STALLing the SET_REPORT control
request.

### What was ruled out (systematic, one variable at a time)

- **Buffer length**: `[0x02]+128`, `+127`, `+64`, `+63`, `+1` bytes - all fail
  identically. Not an off-by-one.
- **Report-ID byte**: `0x02` and `0x00` - both fail.
- **Open mode**: explicit seize (`hid_darwin_set_open_exclusive(1)`, the default)
  and shared (`0`) - both fail. (Default getter reports `1` = exclusive.)
- **Client library**: bypassed hidapi entirely with a direct IOKit ctypes
  harness (`IOHIDManagerCreate` -> matching -> `IOHIDDeviceOpen(seize)` ->
  scheduled on a run loop with an input-report callback -> `IOHIDDeviceSetReport`
  using Apple's exact convention: report-ID passed separately, buffer WITHOUT the
  id byte). **Still `0xE0005000`.** So it is not a hidapi buffer-convention bug.
- **Retries**: 5 SET_REPORTs in a burst with 250 ms gaps - all fail.
- **Entitlements**: Cortex Control's code signature has NO special USB/HID
  entitlement (`codesign -d --entitlements` shows only
  `com.apple.security.cs.allow-unsigned-executable-memory` and
  `com.apple.security.device.audio-input`). So a missing entitlement is not the
  difference between it and us.

### The decisive asymmetry, and why

Via the direct IOKit harness on the freed device:

- `IOHIDDeviceGetReport(input, reportID=1)` -> **`0x0` success** (returns an
  empty report - nothing to send while idle). Control-pipe HID class requests
  work INBOUND.
- `IOHIDDeviceSetReport(output, reportID=2)` -> **`0xE0005000`**. Control-pipe
  HID class requests are rejected OUTBOUND.

Root cause of *why output must use the control pipe*: the QC HID interface
(**interface 5, bInterfaceClass=3**) has **`bNumEndpoints = 1`** - a single
interrupt IN endpoint, **no interrupt OUT endpoint** (confirmed via IORegistry;
hidapi also enumerates exactly one QC HID interface). Per the USB HID spec,
output reports with no OUT endpoint must be delivered as SET_REPORT class
requests on the control pipe (EP0). This device stalls those.

### The paradox that needs a capture to resolve

Cortex Control clearly DOES send output reports successfully (it fully controls
the device), and its binary uses `IOHIDDeviceSetReport` (`HidSenderThread`,
`usbHIDOutCount`, the `IOHIDDeviceSetReport failed` format string). With the same
API, same open mode, and no entitlement advantage, a faithful replica of its
call still stalls for us. The most likely remaining explanation is **device
state**: the QC accepts SET_REPORT only after some initialization/handshake (or
USB configuration / audio-streaming state) that Cortex Control establishes and
that we cannot reproduce or observe standalone on this Mac - because observing it
requires a USB capture of a live Cortex Control session, which is impossible here
(it seizes the device, and macOS Apple-Silicon USB capture is unreliable).

### This is the planned Phase 2 stop point - operator needed

Resolving the wire format requires observing Cortex Control's actual host->device
traffic. See `doc/plan/2026-07-18-windows-usbpcap-capture.md` for the full capture
plan. Correct capture route (NOTE: an earlier draft of this doc said "Linux +
usbmon" - that is WRONG, because Cortex Control has no Linux build, so there is no
app to drive the device on a Linux host):

1. **Windows + USBPcap (recommended).** Cortex Control runs natively on Windows;
   Wireshark's USBPcap reliably captures USB there. Run Cortex Control against the
   QC on a Windows machine (or a Windows VM with USB passthrough) and capture the
   connect/handshake plus a few known actions.
2. **macOS + Frida (needs SIP disabled).** Hook Cortex Control's
   `IOHIDDeviceSetReport` and log report bytes + order. Stays on this Mac but
   requires temporarily disabling System Integrity Protection.
3. Hardware USB protocol analyzer inline on the cable.

What we need from the capture: (a) the exact bytes of the first host->device
output report(s) after connect - the **unlock/handshake** that makes the device
stop stalling SET_REPORT, (b) how the message-type tag and any length field sit
inside the 128-byte payload, (c) how a >128-byte message (e.g. a full preset) is
chunked. With the connect handshake plus one real "switch scene" output report we
can both unblock writes and pin the envelope.

Until then, the codec (Phase 3+) is built against the CONFIRMED HID-layer facts
(128-byte reports, report ID 1 in / 2 out) with the inner-envelope layout as an
explicit, isolated, clearly-labelled provisional assumption - so confirming it
later is a one-line constant change, not a rewrite.

## Phase 2, follow-up experiments with the operator present (2026-07-18)

The QC owner confirmed the device is brand-new (a factory reset would lose
nothing), which cleared the way to try more aggressive, lower-level write attempts.
All of the following still send ONLY a non-mutating `VersionMessage(READ)`.

### Raw libusb bypass of the macOS HID stack - still stalls (device-side proof)

`scripts/libusb_setreport.py` opens the QC with **libusb** (1.0.29) and issues the
HID SET_REPORT as a raw USB class control transfer (`bmRequestType=0x21,
bRequest=0x09, wValue=0x0202, wIndex=5`), bypassing `IOHIDManager`/AppleUserHIDDevice
entirely. Result:

- `libusb_open` OK; `kernel_driver_active(5)=1`; `detach`/`claim_interface` ->
  `LIBUSB_ERROR_ACCESS` (macOS won't release the HID driver - expected).
- **SET_REPORT (output) -> `LIBUSB_ERROR_PIPE` (-9), a USB STALL.** The control
  transfer reaches the device on EP0 even without claiming the interface, and the
  **device stalls it.**
- **GET_REPORT (input) -> success** (0 bytes), same as via IOKit.

This is the decisive proof: two fully independent USB stacks (Apple IOKit HID and
raw libusb) both get a device-issued STALL on output and success on input. The
stall is the **QC firmware's decision**, not any macOS software layer.

### USB audio streaming state - not the unlock

`scripts/audio_active_setreport.py` streams digital silence to/from the QC audio
interface (via `sounddevice`/PortAudio: output, input, and duplex) and retries the
HID write while audio is actively streaming. SET_REPORT fails with `0xE0005000` in
every case. So "device accepts writes only while USB audio streams" is disproven.

### Ruled out to date (host side)

buffer length, report-ID byte, open mode (seize/shared), client library (hidapi,
direct IOKit ctypes, raw libusb), retry bursts, entitlements, standard HID init
(SET_IDLE/SET_PROTOCOL are done by IOHIDManager and still stall), and USB
audio-streaming state. The unlock is device-side state established by Cortex
Control's connect sequence, which cannot be observed on this Mac (no usbmon; Cortex
Control's hardened runtime blocks Frida/DYLD instrumentation unless SIP is
disabled, which this machine cannot do). Hence the Windows/USBPcap capture plan.

Environment: macOS Darwin 25.5.0, Apple Silicon (AppleT8132USBXHCI), libusb 1.0.29,
sounddevice 0.5.5 / PortAudio, Cortex Control quit for the probes.

## Phase 2 CONFIRMED framing (Windows capture, 2026-07-22)

Source: `captures/windows-session-01-nonaudio.pcapng` - a USBPcap capture of a
complete Cortex Control 4.0.1 (Windows) session against the QC (app_fw `d14e`),
plus a live Windows probe replaying the findings through qcctl's own stack.
Everything in this section is verified on the wire; it supersedes the
PROVISIONAL envelope guesses above.

### THE HEADLINE: there is no unlock. The stall is a lie.

**Every single one of Cortex Control's 273 host->device SET_REPORT transfers
completed with `USBD_STATUS_STALL_PID` (0xC0000004) - and the device processed
every one of them anyway.** The QC firmware accepts the 128-byte data stage,
acts on it, then deliberately STALLs the status stage. Windows Cortex Control
ignores the error; on macOS, IOKit surfaces the same stall as `0xE0005000`,
which qcctl misread as "write rejected". There was never an unlock sequence to
find.

Live proof (Windows, hidapi, Cortex Control quit): `hid_write()` returned -1
for every report sent, and the device still echoed our ResetCommsBuffers
session token, answered a Version READ with its full 290-byte version blob,
recalled a preset, and switched scenes. The transport now treats write errors
as expected (`transport._write_report`); a dead device is detected by
request() timeouts instead.

No non-HID (vendor/standard) control request precedes the first SET_REPORT:
the only control traffic before it is standard enumeration GET_DESCRIPTORs.

### Report envelope (byte-exact)

Each 129-byte hidapi report (report-ID byte + 128-byte body):

```
offset  size  field
0       1     report ID: 0x02 host->device, 0x01 device->host
1       1     len   - count of VALID data bytes in THIS report,
              excluding the report-id/len/flags bytes themselves
2       1     flags - 0x40 FIRST fragment | 0x80 LAST fragment
              (0xC0 = complete single-report message, 0x00 = middle)
3       len   data (max 126); rest of the body is padding (host: zeros,
              device: stale buffer contents - ignore)
```

A logical message's reassembled data is `protobuf ++ trailer(8)`:

```
offset  size  field
0       n     protobuf-serialized message (ProductionAutomation.proto)
n       2     CortexMessageType.Enum value, uint16 LITTLE-ENDIAN
n+2     4     zeros in every observed frame
n+6     2     zeros from the host; device fills varying nonzero values
              (not matched by common CRC-16 variants; safe to send zeros
              and ignore on receive)
```

The message TYPE tag lives in the TRAILER, not a header. There is NO total
message length field anywhere: reassembly is purely flags-driven (concatenate
each report's `len` bytes of data until a report with FLAG_LAST arrives).
Non-final fragments always carry the full 126 bytes (`len=0x7e`).

The trailer's zero region is not entirely inert: device messages whose payload
is NOT protobuf (`RecallPreset` pushes, `License`, `CloudLogin`) carry a
nonzero byte inside trailer[2:6] - apparently a "raw payload" flag. The
`RecallPreset` push payload is a gzipped preset file (starts `1f 8b`); large
protobuf replies (`ModelRepo`, ~47 KB) gzip their payload INSIDE a normal
protobuf bytes field instead.

### Annotated real frames

Frame 5584 (t=109.5s) - Cortex Control's Version READ, single report:

```
02                  report ID 0x02 (host->device output report)
0a                  len = 10 (2 pb + 8 trailer)
c0                  flags = FIRST|LAST (complete message)
08 03               protobuf: VersionMessage{action: READ}
0a 00               trailer: type = 10 (Version), u16 LE
00 00 00 00 00 00   trailer: zeros
<114 zero bytes>    padding to the 128-byte body
```

Frame 5580 (t=109.5s) - the FIRST report of the session (ResetCommsBuffers;
a session hello, not an unlock):

```
02                  report ID 0x02
2c                  len = 44 (36 pb + 8 trailer)
c0                  flags = complete
08 00               protobuf: request_id = 0
12 20 37 39 ... 66  protobuf: session_id = "792f08bad8664fb9ade88b6317c3c54f"
                    (32-hex token; the device echoes it back verbatim)
34 00               trailer: type = 52 (ResetCommsBuffers), u16 LE
00 00 00 00 00 00   trailer: zeros
<82 zero bytes>     padding
```

Frames 5614/5616/5618 (t=110.3s) - the QC's 290-byte Version reply spanning
three input reports (fixture `version_reply_multi.json`):

```
report 1:  01 | 7e | 40 | <126 data bytes>   "Linux buildroot 4.0.0-ADI..."
report 2:  01 | 7e | 00 | <126 data bytes>   (middle: no header of any kind)
report 3:  01 | 2e | 80 | <46 data bytes>    last 8 = trailer:
                                             0a 00 (type=10 Version)
                                             00 00 00 00 (zeros)
                                             60 b3 (device-filled, ignored)
           + 80 bytes stale padding
```

### Session flow (as Cortex Control performs it)

1. host: `ResetCommsBuffers{request_id: 0, session_id: <fresh 32-hex>}`;
   device echoes the same session_id back.
2. host: `Version{action: READ}`; device replies with the full version blob.
3. device: `Version{action: READ}` (the protocol is symmetric!); Cortex
   Control answers `Version{action: UPDATE, cortex_control_version: "4.0.1"}`.
   The device keeps talking even if this is never answered.
4. host: `Connection{connected: true}`, then a burst of READs for device state
   (ModelRepo, IOSettings, Scene, SetlistPosition, ...).
5. host: `KeepAlive{action: UPDATE}` every ~1s thereafter.
6. On quit: `Connection{connected: false}`.

### Request/response correlation

Every host message carries an incrementing `request_id` (field 2). BUT:

- READ replies (e.g. Version) come back WITHOUT a request_id echo.
- A state-changing request triggers a cascade of OTHER-type device messages
  that all echo ITS request_id (recalling a preset emitted UndoRedo, Grid,
  Scene, RecentsFavorites... all with the request's id, plus an echo of the
  SetlistPosition message itself).

So correlation is by MESSAGE TYPE first, request_id (when present on both
sides) as a consistency check - implemented in `transport._dispatch`.

### Confirmed field semantics (the previously UNVERIFIED operations)

- **Preset recall** = `SetlistPosition{action: UPDATE, folder_key:
  "/media/p4/Presets/My Presets", position: 218, is_factory: false}`.
  Setlists are addressed by device FILESYSTEM PATH; presets by LINEAR index
  `(bank-1)*8 + letter` (A=0): "28C" -> 218. (`RecallPreset` is not the
  recall request; the device uses it to PUSH the gzipped preset file.)
- **Scene switch** = `Scene{action: UPDATE, selected_scene: 1}` (zero-based;
  A->B sent 1).
- **Bypass toggle** = `Grid{action: UPDATE, preset{bypass{row: 0,
  colBypass{column: 4, sceneBypass{bypass: true}}}}}`.
- **Param change** = `Grid{action: UPDATE, preset{chains{row: 0, models{
  column: 1, params{index: 1, param_values{float_value: 0.4553}}}}}}`;
  a knob drag streams one Grid UPDATE per step with normalized 0..1 floats.
- **Save As** = `File{type: 0, folder{key: <setlist path>, is_factory: false,
  files{index: 220, name: "Test save to user sl", instrument: 2}}}` - action
  CREATE by default, and NO preset payload: the device saves what is already
  on its grid. "28E" -> index 220. The name field was truncated by the Cortex
  Control UI to 20 chars, not by the protocol.

### Second capture (windows-session-02, 2026-07-23): delete / move / factory / GridMove

A follow-up capture (`captures/windows-session-02-nonaudio.pcapng` +
`-timeline.txt`, decoded with zero errors) pinned the remaining operations:

- **Preset DELETE** = `File{action: DELETE, type: 0, folder{key: <setlist
  path>, is_factory: false, files{key: "<setlist path>/<name>.pb"}}}`.
  Deletes address the preset by its device FILE PATH (name-based, `.pb`
  extension), NOT by slot index. (The earlier index-based extrapolation was
  wrong.) No `delete_from_library` field was sent.
- **Preset MOVE** = `File{action: MOVE, type: 0, folder{key: <setlist path>,
  files{key: "<setlist path>/<name>.pb"}}, to_folder{key: <setlist path>,
  files{index: 219}}}` - source by file path, DESTINATION by linear index.
- **Factory recall** = `SetlistPosition{action: UPDATE, folder_key:
  "/opt/neuraldsp/Factory Library/", position: 7, is_factory: true}`.
  Factory setlists live under `/opt/neuraldsp/` and keep a TRAILING SLASH in
  the folder_key; user setlists (`/media/p4/Presets/...`) have none.
- **Grid block move** (drag a block one column over) = `GridMove{move{
  from_col: 4, to_col: 5, is_drop: true}, grid{rows{modelIds: ...} x4}}` -
  the move plus a full 4x8 grid snapshot of model IDs. No row field was sent
  for a row-0 move (proto3 default).
- `Mode` UPDATEs (`{mode: 0|1|2}`) fire as the UI changes views.

### Third capture (windows-session-03, 2026-07-23): on-UNIT actions + the connect gate

Actions performed on the DEVICE's own touchscreen while Cortex Control watched
(`captures/windows-session-03-*`). This revealed the device->host SYNC
broadcasts and, critically, WHAT MAKES THEM FLOW.

- **SceneCopy** (done on the unit) = `SceneCopy{action: UPDATE, to_index: 3}`
  (from_index left at default 0; action UPDATE, not COPY). The device
  BROADCASTS this when you copy a scene on the unit - it is not reachable from
  Cortex Control's UI. So the host->device shape is the protocol's symmetric
  mirror, not directly captured being sent by CC.
- **SceneLabel** = `SceneLabel{action: UPDATE, index, label}` and **SceneColor**
  = `SceneColor{action: UPDATE, index, color}` where `color` is an ARGB uint32
  (e.g. a pinkish scene = 0xFFFF02C2). On any scene edit the device re-broadcasts
  the FULL set of 8 scene labels+colors (indices 0..7).
- **RecallPreset PUSH**: recalling a preset (host OR unit) makes the device
  broadcast `RecallPreset{action: UPDATE, preset: <BinaryPreset>, reason}`.
  The `preset` bytes are often FRAME-LEVEL gzip-compressed (payload starts
  `1f 8b`); the transport now gunzips before protobuf-parsing. This push - not
  any host READ - is how the full current preset is obtained.

**The connect gate (the big one).** A minimal ResetCommsBuffers+Connection is
NOT enough to make the device push state or sync: proven live, recalls
produced ZERO device traffic until the host replayed Cortex Control's FULL
connect burst. What the device requires before it treats a client as
"connected" and starts pushing:

1. `ResetCommsBuffers{session_id}` (echoed).
2. `Version` READ, then `Version` UPDATE announcing `cortex_control_version`
   ("4.0.1" on the wire) - the device gates push behaviour on a valid CC
   version.
3. `ModelRepo` READ - empirically REQUIRED: with it the device starts pushing;
   without it (Version+Connection+all other subscribes present) it stays
   silent. Likely a readiness gate rather than a real model-repo need.
4. `Connection{connected: true}`.
5. A READ for each state type the client wants pushed (RecallPreset, Grid,
   Scene, SetlistPosition, ... - `client.QuadCortex._SUBSCRIBE_TYPES`).

`client.hello()` performs exactly this. After it, `read_preset` (recall + catch
the RecallPreset push, via `transport.await_broadcast`) returns the full
21 KB BinaryPreset, and all live-sync pushes flow. Verified end-to-end
2026-07-23: read "Cali Basswalk" (4 chains, 8 scenes) and duplicated a scene's
params client-side in the returned protobuf.

There is therefore NO need for an on-device SceneCopy command from the host: a
client-side scene copy = read_preset -> copy per-scene `param_values[from]` to
`[to]` (and per-scene bypass) -> write the preset back via a File CREATE.

### What transport must do (replaces the "unlock" deliverable)

1. Open the HID device; run the RX read loop as before.
2. IGNORE write errors - every SET_REPORT "fails" with a stall and succeeds.
3. Send the session hello (`client.hello()`): ResetCommsBuffers with a fresh
   session_id, then `Connection{connected: true}`.
4. Correlate replies by type (transport._dispatch); keep KeepAlives flowing
   (Cortex Control uses 1s; the device tolerated our 5s default and 20s idle
   gaps in the capture without dropping the session).

Verified end-to-end on 2026-07-22 (Windows, hidapi): hello handshake, Version
READ (serial <redacted>), recall of preset 28C, and scene A->B->A switches all
executed live through qcctl's Transport + QuadCortex client.

## macOS live verification + input port-ID mapping (Phase A/B, 2026-07-23)

Everything above was proven live on macOS (Apple Silicon, hidapi via Homebrew
libhidapi, `DYLD_LIBRARY_PATH=/opt/homebrew/lib`) against the owner's unit
(app_fw `d14e`, serial `<redacted>`, zenos `4.0.1`). The status-stage STALL is
swallowed correctly through IOKit: reads return, scene writes land (confirmed by
eye on the unit), and `read_preset` returns a full BinaryPreset. See
`examples/switch_scenes.py` for a live scene-switch demo.

### RecallPreset pushes echo a host recall's request_id (correlation fix)

CONFIRMED on-device 2026-07-23: a host-initiated recall (`SetlistPosition
UPDATE` carrying a `request_id`) makes the device echo THAT request_id on the
`RecallPreset` push it emits. An unsolicited push (the seed from hello's
RecallPreset subscription, or a unit-initiated recall) carries NO request_id
(bytes go straight from field 1 `action` to field 3 `preset`).

This fixed a lag-by-one bug: `read_preset` used to return whatever RecallPreset
arrived first, so when a prior push was still in flight (the hello seed push,
delivered lazily ~10s later, seeded the lag) each read returned the PREVIOUS
recall's preset. `read_preset` now tags its recall with a fresh `request_id`
(via `transport.next_request_id()`) and, through `await_broadcast(..., match=)`,
accepts only the push echoing it - so a stale/seed push is ignored and each read
returns its own preset. Verified: reading 28A/28C/28E/28F in sequence each
returned its own content, no lag.

### Input port-ID mapping for `BinaryPreset.Chain.in_portid`

`Chain.in_portid` uses the schema's `GainCalInputPortParameter.InputPortId`
enum VERBATIM. CONFIRMED on-device by diffing user presets with known per-row
routings (owner-built) against the read-back `in_portid` values, cross-checked
with the device's `IOSettings` `PortSettings.in_port` list (ports 1, 2, 4
plugged = the owner's rig; 5 unplugged):

| in_portid | port                | evidence                              |
|-----------|---------------------|---------------------------------------|
| 0         | EMPTY (internally fed) | non-input chains (splitter/mixer fed) |
| 1         | Input 1             | factory presets; 28A row3; IOSettings |
| 2         | Input 2             | 28A row1; IOSettings                  |
| 3         | Input 1/2 (stereo)  | 28F row2                              |
| 4         | Return 1            | 28C rows 1&3; IOSettings              |
| 5         | Return 2            | 28E row1; IOSettings                  |
| 6         | Return 1/2 (stereo) | 28F row3                              |
| 8         | USB 5               | 28E row2                              |
| 9         | USB 6               | 28E row3                              |
| 10        | USB 7               | 28E row4                              |
| 11        | USB 8               | 28F row1                              |
| 13        | USB 7/8 (stereo)    | 28F row4                              |

(Untested but implied by the enum: 7 = PREV_ROW, 12 = USB 5/6, 14 =
SIDECHAIN_BUFFER.) Rear combo jacks are the same ports as Input 1 / Input 2
(no distinct id). Constants: `client.INPUT_1/INPUT_2/RETURN_1/RETURN_2`.

### Persisting a re-routed preset: the write path (Phase C, root-caused 2026-07-23)

The naive flow "read_preset -> mutate in_portid -> write_preset(full) -> save"
does NOT work. Root-caused on-device with two discriminating experiments:

1. **A full-preset `Grid` UPDATE is not applied.** The device applies a Grid
   UPDATE by locating each chain/model by its `row`/`column` KEY (mirroring the
   captured param-change update `chains{row:0, models{column:1, params{...}}}`).
   A preset freshly read from a recall carries NO explicit `row` on its chains,
   so `write_preset(full_preset)` has nothing to key on and the `in_portid`
   change is silently dropped.
2. **`File` CREATE ignores `preset_payload`; it snapshots the GRID.** Proven by
   leaving the grid on the factory preset (in_portid=1) while sending a CREATE
   whose `preset_payload` carried in_portid=2: the saved slot read back
   in_portid=1. The `files[].name` IS taken from the File message (so the slot
   gets the right name), but the CONTENT is always the current grid. (The
   captured Cortex Control "Save As" likewise sent no payload.)

**The working flow** (`examples/reroute_and_save.py`, verified end-to-end):
recall the preset (loads it onto the grid) -> for each input row on the source
port send a ROW-KEYED sparse Grid UPDATE re-pointing it
(`client.QuadCortex.set_chain_input(row, in_portid)` = `Grid{UPDATE,
preset{chains{row, in_portid}}}`) -> `save_current_preset` (snapshots the grid).
`client.input_chain_rows(preset, from_port)` finds the rows to move (chain index
== grid row when `row` is unset). Verified: factory "D-Cell H4 Ch3" (row 0 =
Input 1) re-pointed to Input 2 (28G) and Return 1 (28H), both read back with the
new `in_portid`. `client.QuadCortex.reroute_grid_input(preset, to_port)` is the
high-level applied form (find rows via `client.input_chain_rows`, send one
`set_chain_input` per row); the grid is moved via `set_chain_input`, never by
writing a full preset.

## Protocol operation coverage (verified on macOS hardware, 2026-07-23)

Every public `client.QuadCortex` operation exercised live on the owner's unit
(fw `d14e`, serial `<redacted>`). Verification method per row: **read-back** =
re-read device state and assert; **on-unit** = owner confirmed the change on the
device screen (transitions were armed first). Scripts: `scripts/verify_sweep.py`,
`scripts/verify_edit.py`, `scripts/verify_ports.py`.

| Operation | Wire shape (brief) | Verified by | Notes |
|---|---|---|---|
| `hello()` | ResetCommsBuffers + Version UPDATE + ModelRepo READ + Connection + subscribes | read-back | connect gate; state pushes flow after |
| Version READ | `Version{action: READ}` | read-back | serial/fw returned |
| `recall_preset` / `read_preset` | `SetlistPosition{UPDATE, folder_key, position, is_factory, request_id}` -> RecallPreset push | read-back | push echoes the recall's request_id (correlation) |
| enumerate | `File{action: READ}` -> `File{folder{files[]=ProductData}}` | read-back | factory listing gzipped; 256 slots; listings lag a few s after a File mutation |
| `switch_scene` | `Scene{UPDATE, selected_scene}` | on-unit (Phase A) | zero-based |
| `set_chain_input` / `reroute_grid_input` | `Grid{UPDATE, preset{chains{row, in_portid}}}` | read-back + on-unit | row-keyed; the only way input routing persists |
| `set_param` | `Grid{UPDATE, preset{chains{row, models{column, params{index, param_values[scene]{float_value}}}}}}` | read-back | value round-trips (0.0->1.0); param index is positional; not every index is a visible knob |
| `set_bypass` | `Grid{UPDATE, preset{bypass{row, colBypass{column, sceneBypass[scene]{bypass}}}}}` | on-unit | amp block greyed on the unit |
| `set_scene_label` / `set_scene_color` | `SceneLabel/SceneColor{UPDATE, index, label/color}` | read-back | color is ARGB uint32; round-trip exact |
| `copy_scene` | `SceneCopy{UPDATE, from_index, to_index}` | on-unit | host->device confirmed (scene D took on A) |
| `save_current_preset` | `File{CREATE, folder{key, files{index, name, instrument}}}` | read-back | snapshots the GRID; `preset_payload` is IGNORED for CREATE |
| `delete_preset` | `File{DELETE, folder{files{key: "<setlist>/<name>.pb"}}}` | read-back | works; async - a listing within ~2s is stale, ~5s reliable |
| `move_preset` | `File{MOVE, folder{files{key}}, to_folder{files{index}}}` | read-back | source by file path, dest by index; async like delete |
| `write_preset` | `Grid{UPDATE, preset}` | read-back | low-level primitive; applies ONLY row/column-keyed elements. A full recalled preset written back does NOTHING (`verify_edit.py writeprobe`). Use the keyed wrappers. |

### Newly-resolved semantics

- **File ops are eventually-consistent.** delete/move take effect on the device
  but a `File` listing (enumeration) issued within a few seconds can still show
  the pre-mutation state. Verify with a fresh enumeration after a short wait.
- **`save`/`File CREATE` snapshots the grid.** The `preset_payload` field is not
  applied on CREATE; the saved content is whatever is on the grid, named by the
  `files` entry. To save an edited preset: recall -> keyed Grid edits -> save.
- **Pushed preset is structural.** A RecallPreset push carries chains/models by
  `hash` with `column`/param `index` IMPLIED BY POSITION (not stored); param
  `param_values` ARE present and round-trip. `in_portid`/`out_portid`/`row`/
  scene labels+colors are present.

### Confirmed ID enums (no guesses)

- **Input** (`Chain.in_portid`) = `GainCalInputPortParameter.InputPortId`
  verbatim, ids 0-14 all confirmed on-unit (15 rejected): 0 internal, 1 Input 1,
  2 Input 2, 3 Input 1/2, 4 Return 1, 5 Return 2, 6 Return 1/2, 7 "Prev. Row",
  8-11 USB 5-8, 12 USB 5/6, 13 USB 7/8, 14 sidechain buffer (blank in UI).
- **Output** (`Chain.out_portid`) = `GainCalOutputPortParameter.OutputPortId`
  verbatim: anchored by owner's 28F (4 "Output 1", 1 "Output 1/2") and
  spot-confirmed (2 "Output 3/4", 3 "Send 1/2", 10 "USB 5"). 16-19 are internal
  grid-routing states (NEXT_ROW_*, MULTIPLE_OUTS), not direct destinations.
- **Instrument** (`ProductData.instrument`): 1 guitar, 2 bass, 4 vocal
  (owner-confirmed). Powers of two, 3 unused - consistent with bit flags.

Constants for all of the above live in `client.py`
(`INPUT_*`/`RETURN_*`/`USB_IN_*`/`SIDECHAIN_BUFFER`, `OUT_*`, `INSTRUMENT_*`).

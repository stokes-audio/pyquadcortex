# Quad Cortex USB control protocol

A reference for the USB-HID Protobuf protocol the Neural DSP Quad Cortex uses to
talk to Cortex Control, as re-implemented by this library. Everything here was
established by observing real Cortex Control sessions on the wire and then
confirming each finding live against hardware.

> **Applies to:** CorOS / Cortex Control **4.0.1**, device firmware (`app_fw`)
> **d14e**, `zenos` **4.0.1**.
>
> **The protocol is unversioned.** Nothing on the wire identifies a schema or
> protocol version, so none of this is guaranteed across a CorOS update. Treat
> every statement below as "true of 4.0.1 / d14e" and re-verify after a firmware
> change (see
> [architecture.md](architecture.md#adapting-to-a-new-coros-version)).
>
> Unofficial: this project is not affiliated with, endorsed by, or supported by
> Neural DSP.

## Contents

- [1. The USB HID interface](#1-the-usb-hid-interface)
  - [1.1 Exclusive access](#11-exclusive-access)
  - [The benign write STALL](#the-benign-write-stall)
- [2. Report framing](#2-report-framing)
  - [2.1 Report layout](#21-report-layout)
  - [2.2 Fragmentation and reassembly](#22-fragmentation-and-reassembly)
  - [2.3 The message envelope (trailer)](#23-the-message-envelope-trailer)
  - [2.4 Compressed payloads](#24-compressed-payloads)
  - [2.5 Annotated real frames](#25-annotated-real-frames)
- [3. Message types and actions](#3-message-types-and-actions)
- [4. The connect handshake](#4-the-connect-handshake)
  - [4.1 Why it is required](#41-why-it-is-required)
  - [4.2 The sequence](#42-the-sequence)
  - [4.3 Keepalive and disconnect](#43-keepalive-and-disconnect)
- [5. Request/response correlation](#5-requestresponse-correlation)
- [6. Addressing presets](#6-addressing-presets)
  - [6.1 Setlist paths](#61-setlist-paths)
  - [6.2 Slot names and linear positions](#62-slot-names-and-linear-positions)
- [7. Operations](#7-operations)
  - [7.1 Recall a preset](#71-recall-a-preset)
  - [7.2 Read the current preset](#72-read-the-current-preset)
  - [7.3 Enumerate a setlist](#73-enumerate-a-setlist)
  - [7.4 Scenes](#74-scenes)
  - [7.5 Grid edits and the edit path](#75-grid-edits-and-the-edit-path)
  - [Grid block move](#grid-block-move)
  - [7.7 File operations](#77-file-operations)
  - [7.8 Other observed traffic](#78-other-observed-traffic)
- [8. Port, instrument, and preset enums](#8-port-instrument-and-preset-enums)
- [9. The pushed preset structure](#9-the-pushed-preset-structure)
- [Operation coverage](#operation-coverage)
- [Open questions](#open-questions)

## 1. The USB HID interface

The Quad Cortex enumerates as vendor `0x152A` (Neural DSP), product `0x880A`.
Control traffic uses a single HID interface, **interface 5**
(`bInterfaceClass = 3`), with usage page `0x0001` and usage `0x0000`, so it is
*not* a vendor-defined usage page. The unit also exposes USB audio interfaces;
those are unrelated to control.

The HID report descriptor (read from the OS registry, no device open required)
is:

```
05010900a101150026ff008501750895800900818285027508958009009182c0
```

which decodes to:

- Usage Page Generic Desktop (`0x01`), Usage `0x00`, application collection;
- **report ID `0x01`: INPUT**, 128 bytes of 8-bit data (device to host);
- **report ID `0x02`: OUTPUT**, 128 bytes of 8-bit data (host to device);
- no feature reports.

The OS reports `MaxInputReportSize` and `MaxOutputReportSize` as **129**: 128
payload bytes plus the report-ID byte. hidapi includes the report-ID byte in
both directions for numbered reports, so a report is 129 bytes at the hidapi
boundary.

The interface has **`bNumEndpoints = 1`**: a single interrupt IN endpoint and
**no interrupt OUT endpoint**. Per the USB HID specification, output reports on
such an interface must therefore be delivered as `SET_REPORT` class requests on
the control pipe (EP0), which is what host HID stacks do automatically and what
leads directly to the next two sections.

With no host session driving it, the device emits **no unsolicited input
reports** at all: a 30-second idle listen on a freed device produced zero
reports. Device-to-host traffic is entirely a reaction to host traffic or to
user action on the unit.

### 1.1 Exclusive access

On macOS, Cortex Control opens the HID interface with
`kIOHIDOptionsTypeSeizeDevice` (exclusive). While Cortex Control is running, no
other userspace process can open the interface at all, not even read-only:
attempts fail with `kIOReturnExclusiveAccess` (`0xE00002C5`). **Quit Cortex
Control before using this library.** This is also why passive sniffing alongside
a running Cortex Control is not possible on macOS.

### The benign write STALL

**Every HID output report is accepted by the device and then reported as a write
error by the host. The error is meaningless and must be ignored.**

The device consumes the 128-byte data stage of the `SET_REPORT` control
transfer, acts on it, and then deliberately STALLs the transfer's **status
stage**. Host stacks surface that as a failed write:

| Host stack | Symptom |
|---|---|
| Windows (USB layer) | transfer completes with `USBD_STATUS_STALL_PID` (`0xC0000004`) |
| Windows / hidapi | `hid_write()` returns `-1` |
| macOS IOKit | `IOHIDDeviceSetReport failed: (0xE0005000)` (`sys_iokit` / `sub_iokit_usb`, code `0x1000`) |
| libusb (raw control transfer) | `LIBUSB_ERROR_PIPE` (`-9`) |

Three facts pin this down as normal behaviour rather than a client bug:

1. Cortex Control gets the same stall. In a full captured session, **all 273 of
   its host-to-device `SET_REPORT` transfers completed with
   `USBD_STATUS_STALL_PID`**, and the device acted on every one of them. Cortex
   Control simply ignores the error.
2. Two independent USB stacks see it. Both Apple's IOKit HID stack and raw
   libusb (bypassing the HID stack entirely, `bmRequestType=0x21`,
   `bRequest=0x09`, `wValue=0x0202`, `wIndex=5`) get a device-issued stall on
   output and success on input. The stall is the device firmware's decision, not
   an OS software layer's.
3. Writes visibly work despite the "failure". With every `hid_write()`
   returning `-1`, the device still echoed a session token, answered a `Version`
   READ with its full version blob, recalled presets, and switched scenes.

Inbound control requests are *not* stalled: `GET_REPORT` on the input report
succeeds (returning an empty report when the device has nothing to send).

**There is no unlock or magic initialization behind the stall.** No non-HID
(vendor or standard) control request precedes the first `SET_REPORT`; the only
control traffic before it is standard enumeration `GET_DESCRIPTOR`s. Things that
were tested and are *not* the explanation: buffer length, the report-ID byte,
exclusive versus shared open, the client library (hidapi, direct IOKit, raw
libusb), retry bursts, code-signing entitlements, standard HID init requests,
and whether USB audio is streaming.

**Implementation consequence:** a transport must swallow write errors
(`transport._write_report` logs them at debug and continues) and detect a
genuinely dead device through **request timeouts** instead. If writes ever start
failing for real, the symptom will be timeouts, not write errors.

## 2. Report framing

### 2.1 Report layout

Each 129-byte report (report-ID byte plus the 128-byte body) is:

```
offset  size  field
0       1     report ID: 0x02 host->device, 0x01 device->host
1       1     len    - count of VALID data bytes in THIS report, excluding
                       the report-id/len/flags bytes themselves (max 126)
2       1     flags  - 0x40 FIRST fragment | 0x80 LAST fragment
                       0xC0 = complete single-report message, 0x00 = middle
3       len   data
...           padding to the end of the 128-byte body
```

Padding is zero from the host. **From the device, padding is stale buffer
content and must be ignored** (`len` is authoritative).

Constants in `pyquadcortex/framing.py`: `REPORT_SIZE = 128` (body),
`CHUNK_SIZE = 126` (body minus the `len`/`flags` prefix), `OUT_REPORT_ID = 0x02`,
`IN_REPORT_ID = 0x01`, `FLAG_FIRST = 0x40`, `FLAG_LAST = 0x80`.

### 2.2 Fragmentation and reassembly

A logical message is split into 126-byte chunks, one per report:

- the first report has `FLAG_FIRST` set, the last has `FLAG_LAST`, a
  single-report message has both (`flags = 0xC0`), and middle reports have
  `flags = 0x00`;
- middle reports carry **no header of any kind** beyond `len`/`flags`: no
  sequence number, no chunk id, no offset;
- non-final fragments always carry a full 126 bytes (`len = 0x7e`);
- **there is no total-length field anywhere in the protocol.** Reassembly is
  purely flag-driven: concatenate each report's `len` data bytes until a report
  with `FLAG_LAST` arrives.

Because completion is flag-driven and unbounded, a robust receiver needs two
safety behaviours, both implemented in `transport._read_loop`:

- a `FLAG_FIRST` report while a partial message is buffered means the previous
  message was lost mid-stream; drop the partial buffer and start fresh;
- cap the reassembly buffer so a lost `FLAG_LAST` cannot accumulate forever. The
  library's cap is 1 MiB of reassembled body, comfortably above the largest
  observed message (a `ModelRepo` reply, roughly 47 KB of gzipped payload
  spanning 371 reports).

### 2.3 The message envelope (trailer)

A reassembled logical message is `protobuf ++ trailer(8)`:

```
offset  size  field
0       n     protobuf-serialized message (see the recovered schema)
n       2     CortexMessageType.Enum value, uint16 LITTLE-ENDIAN
n+2     4     zero in every observed frame, except as noted below
n+6     2     zero from the host; the device fills varying nonzero values
```

Two things are surprising and worth stating plainly:

- **The message type tag lives in the TRAILER, not in a header.** A receiver
  cannot know a message's type until the final fragment has arrived.
- **There is no length field**, as above.

The device-filled bytes at `n+6` do not match common CRC-16 variants and their
meaning is unknown. It is safe to send zeros there and to ignore them on
receive.

The zero region at `n+2` is not entirely inert: device messages whose payload is
**not** protobuf (`RecallPreset` pushes, `License`, `CloudLogin`) carry a
nonzero byte inside those four bytes, which looks like a "raw payload" flag.
This interpretation is an inference, not a confirmed field; the library does not
rely on it, and instead detects compressed payloads by their gzip magic bytes
and tolerates unparseable ones.

### 2.4 Compressed payloads

Two different, independent kinds of compression appear:

- **Frame-level gzip.** Some device payloads are gzip streams: the reassembled
  payload starts `1f 8b` and the decompressed bytes are the ordinary protobuf
  message for the frame's type. `RecallPreset` pushes (carrying a full preset)
  do this, as does the factory-library folder listing. A receiver should gunzip
  before parsing when it sees the magic bytes.
- **Field-level gzip.** Large protobuf replies such as `ModelRepo` (roughly
  47 KB) instead gzip their content *inside* a normal protobuf `bytes` field, so
  the frame payload itself is plain protobuf.

### 2.5 Annotated real frames

A `Version` READ, single report (this is `tests/fixtures/frames/version_read.json`):

```
02                  report ID 0x02 (host->device output report)
0a                  len = 10 (2 bytes protobuf + 8 trailer)
c0                  flags = FIRST|LAST (complete message)
08 03               protobuf: VersionMessage{action: READ}
0a 00               trailer: type = 10 (Version), uint16 LE
00 00 00 00 00 00   trailer: zeros
<114 zero bytes>    padding to the 128-byte body
```

The first report of a session, `ResetCommsBuffers` (a session hello, not an
unlock):

```
02                  report ID 0x02
2c                  len = 44 (36 protobuf + 8 trailer)
c0                  flags = complete
08 00               protobuf: request_id = 0
12 20 ...           protobuf: session_id = 32 hex characters
34 00               trailer: type = 52 (ResetCommsBuffers), uint16 LE
00 00 00 00 00 00   trailer: zeros
<82 zero bytes>     padding
```

A 290-byte `Version` reply from the device, spanning three input reports (this is
`tests/fixtures/frames/version_reply_multi.json`):

```
report 1:  01 | 7e | 40 | <126 data bytes>   starts "Linux buildroot ..."
report 2:  01 | 7e | 00 | <126 data bytes>   middle: no header of any kind
report 3:  01 | 2e | 80 | <46 data bytes>    last 8 bytes are the trailer:
                                               0a 00        type = 10 (Version)
                                               00 00 00 00  zeros
                                               60 b3        device-filled, ignored
           + 80 bytes of stale padding
```

The `Version` reply carries the device's kernel string, `zenos` version,
`app_fw_version`, bootloader version, MAC address, and serial number, among
other fields (see `VersionMessage` in the schema).

## 3. Message types and actions

Every frame's trailer carries a `CortexMessageType.Enum` value. The schema
declares **71 types** (`Undefined = 0` through `GenerateTestPreset = 70`, with
`NumberOfMessageTypes = 71` as a sentinel). The ones this library uses:

| Value | Type | Role here |
|---|---|---|
| 1 | `Grid` | grid edits (params, bypass, chain input routing) |
| 2 | `SetlistPosition` | preset recall |
| 4 | `File` | enumerate, save, delete, move |
| 10 | `Version` | version read, and the Cortex Control version announce |
| 12 | `GridMove` | move a block between grid positions |
| 13 | `Scene` | select the active scene |
| 15 | `RecallPreset` | the device's push of the full current preset |
| 22 | `SceneCopy` | copy or swap scenes |
| 23 | `SceneLabel` | scene name |
| 32 | `KeepAlive` | session keepalive |
| 48 | `SceneColor` | scene color (ARGB) |
| 49 | `Connection` | connected / disconnected announce |
| 51 | `ModelRepo` | required readiness step in the handshake |
| 52 | `ResetCommsBuffers` | session hello with a session token |

`registry.py` registers roughly three dozen types in total, including state
types the device pushes (`IOSettings`, `GeneralSettings`, `Mode`, `GlobalEQ`,
`UndoRedo`, `PresetDirty`, `RecentsFavorites`, `Updater`, and others) so the RX
path can decode them.

Most messages carry `action` (field 1) drawn from `MessageAction.Enum`:

```
CREATE = 0    UPDATE = 1    DELETE = 2    READ = 3
MOVE = 4      COPY = 5      UPLOAD = 6    DOWNLOAD = 7    SWAP = 8
```

Note `CREATE = 0` is the proto3 default, so an omitted `action` means CREATE.
That is exactly how Cortex Control's "Save As" is sent.

Nearly every scalar field in the schema is wrapped in a synthetic
`oneof _field` (proto3 `optional`), so "set to zero" and "not set" are
distinguishable on the wire and via `HasField()`. The protocol makes use of
that: absent fields mean "unchanged / not addressed", which is what makes sparse
keyed grid edits work.

**The protocol is symmetric.** The same message types flow in both directions.
The device sends the host its own `Version` READ during connect, and it
broadcasts `Scene`, `SceneLabel`, `SceneColor`, `SceneCopy`, `Grid`, and
`RecallPreset` messages when the user operates the unit's own touchscreen.

## 4. The connect handshake

### 4.1 Why it is required

**The device will not push state to a client that has only opened the pipe.** A
minimal `ResetCommsBuffers` + `Connection{connected: true}` is not enough: with
only that, preset recalls produced **zero** device traffic. The device answers
direct requests, but it does not treat the client as connected, so none of the
pushes that carry real state (the `RecallPreset` preset dump, `Grid`/`Scene`
live sync, folder listings) ever arrive.

Two steps of the burst are load-bearing and non-obvious:

- **The Cortex Control version announce.** The device gates push behaviour on
  receiving a valid `cortex_control_version`. The library announces `"4.0.1"`
  (`QuadCortex.CC_VERSION`), the version seen on the wire.
- **A `ModelRepo` READ.** Empirically required: with it, the device starts
  pushing; without it, and with everything else present, it stays silent. This
  looks like a readiness gate rather than a real need for the model repository.

### 4.2 The sequence

As Cortex Control performs it:

1. host: `ResetCommsBuffers{request_id: 0, session_id: <fresh 32 hex chars>}`.
   The device echoes the same `session_id` back.
2. host: `Version{action: READ}`. The device replies with its full version blob.
3. device: `Version{action: READ}` to the host. Cortex Control answers
   `Version{action: UPDATE, cortex_control_version: "4.0.1"}`. The device keeps
   talking even if its own READ is never answered, but the UPDATE is what opens
   the push gate.
4. host: `Connection{connected: true}`, then a burst of READs for device state
   (`ModelRepo`, `IOSettings`, `Scene`, `SetlistPosition`, and the rest).
5. host: `KeepAlive{action: UPDATE}` roughly every second thereafter.

Each READ in step 4 acts as a **subscription**: the device pushes that state
type to clients that asked for it. `QuadCortex._SUBSCRIBE_TYPES` lists the set
the library subscribes to, mirroring Cortex Control's burst. `RecallPreset` is
the one that matters most (it is how a full preset is obtained), but the device
appears to want the whole set before it considers the client fully connected.

`QuadCortex._hello()`, which `pyquadcortex.connect()` runs for you, does the
same thing with one deliberate difference: it does **not** issue a host
`Version` READ. The device sends its own `Version` READ anyway, and a redundant
host READ would race a caller's later version request, since READ replies carry
no `request_id` to disambiguate them.

After the burst the device needs a moment before it treats the client as
connected; a command sent too soon gets no push (observed as flaky preset-read
timeouts). `connect(settle=...)` waits 2 seconds by default.

The `RecallPreset` subscription produces a **seed push** of the currently loaded
preset, delivered lazily (10 to 25 seconds later has been observed). Any code
waiting for a preset push must be able to ignore it; see
[section 5](#5-requestresponse-correlation).

### 4.3 Keepalive and disconnect

Cortex Control sends `KeepAlive{action: UPDATE}` about once per second. The
library defaults to every 5 seconds, and the device tolerated 20-second idle
gaps in the capture without dropping the session, so the exact interval is not
critical. On quit, Cortex Control sends `Connection{connected: false}`.

## 5. Request/response correlation

Every host message carries an incrementing `request_id` (field 2 on most
message types). Correlating replies is less straightforward than that suggests:

- **READ replies carry no `request_id` echo.** A `Version` READ comes back with
  no id at all.
- **A state-changing request triggers a cascade of other-type messages that all
  echo its `request_id`.** Recalling a preset produced `UndoRedo`, `Grid`,
  `Scene`, and `RecentsFavorites` messages all carrying the recall's id, plus an
  echo of the `SetlistPosition` message itself.
- **Some answers are not replies at all**, but broadcasts of a different type
  emitted in reaction to the action (the `RecallPreset` push, the `File` folder
  listings).

So correlation is **by message TYPE first**, with `request_id` used as a
consistency check when both sides carry one (`transport._dispatch`). A reply is
the first inbound message whose type matches the request's, and whose
`request_id`, if present, matches.

One correlation subtlety is worth knowing because it caused a real bug:

**`RecallPreset` pushes echo a host recall's `request_id`.** A host-initiated
recall (a `SetlistPosition` UPDATE carrying a `request_id`) makes the device echo
that id on the `RecallPreset` push it emits. An unsolicited push (the handshake
seed, or a recall performed on the unit) carries **no** `request_id`: the bytes
go straight from field 1 (`action`) to field 3 (`preset`). Without matching on
the id, a reader returns whichever `RecallPreset` arrives first, which lags by
one whenever an earlier push is still in flight; the lazily-delivered seed push
is enough to seed that lag permanently. `read_preset` therefore tags its recall
with a fresh `request_id` and accepts only the push echoing it.

## 6. Addressing presets

### 6.1 Setlist paths

Setlists are addressed by their **device filesystem path**, not by an index or a
name:

| Setlist | Path | `is_factory` |
|---|---|---|
| User ("My Presets") | `/media/p4/Presets/My Presets` | `false` |
| Factory Library | `/opt/neuraldsp/Factory Library/` | `true` |

**Note the factory path's trailing slash.** Cortex Control sends it verbatim,
and user setlist paths have none. `enums.Setlist` carries both strings so
callers do not have to remember this.

Individual presets exist as files inside the setlist directory, named
`<setlist path>/<preset name>.pb`. That path is how `File` DELETE and MOVE
address a preset.

### 6.2 Slot names and linear positions

The unit shows presets as bank-plus-letter names such as "28C". On the wire a
preset is a **zero-based linear position**:

```
position = (bank - 1) * 8 + letter_index      where A = 0 ... H = 7
```

So "28C" is `(28 - 1) * 8 + 2 = 218`, and "28E" is 220. Both were confirmed
against captured traffic. `client.slot_to_position("28C")` implements this. A
setlist holds 256 slots (32 banks of 8).

## 7. Operations

Field paths below use `{}` for nested submessages, matching the captured
traffic. Every shape in this section was seen on the wire unless explicitly
noted.

### 7.1 Recall a preset

```
SetlistPosition{action: UPDATE,
                folder_key: "/media/p4/Presets/My Presets",
                position: 218,
                is_factory: false}
```

For a factory preset, `folder_key: "/opt/neuraldsp/Factory Library/"` (with the
trailing slash) and `is_factory: true`.

`RecallPreset` is **not** the recall request. The device uses that type to push
the preset (next section).

### 7.2 Read the current preset

**There is no host-initiated "read preset" request.** A `Grid` or `RecallPreset`
READ gets no reply. Instead, whenever a preset is recalled, by the host or on the
unit, the device **broadcasts**:

```
RecallPreset{action: UPDATE, preset: <BinaryPreset>, reason: <RecallPresetReason>}
```

The `preset` field carries the full preset (roughly 21 KB for a four-chain,
eight-scene preset), and the frame payload is usually gzip-compressed. This push
is the only way to obtain the full current preset.

Consequences for a client:

- reading a preset **is not side-effect free**: it recalls the slot, which loads
  it onto the grid;
- the device services the push lazily; 10 to 25 seconds has been observed, so
  timeouts must be generous (the library defaults to 40 seconds);
- the push must be correlated by `request_id` to avoid returning a stale or seed
  push (see [section 5](#5-requestresponse-correlation)).

`RecallPresetReason.Enum` is `OTHER = 0`, `UNDO = 1`, `SAVE = 2`.

### 7.3 Enumerate a setlist

There is no host-initiated "list" request either. A `File{action: READ}` makes
the device push one `File` message per setlist:

```
File{folder{key: <setlist path>, is_factory, files: [ProductData, ...]}}
```

Each `ProductData` entry carries `index` (the linear slot position), `name`,
`instrument`, and metadata fields such as `author`, `coros_version`, `date`, and
`cloud_id`. A setlist lists 256 slots. The factory listing arrives
gzip-compressed at the frame level.

A client must match the listing it wants by `folder.key`, and should require
`len(folder.files) > 0`, because the device also pushes empty folder messages.

**Listings are eventually consistent.** After a `File` DELETE or MOVE, a listing
issued within about 2 seconds can still show the pre-mutation state; about
5 seconds was reliable. Re-enumerate after a short wait rather than trusting the
first listing after a mutation.

### 7.4 Scenes

Scene indices are zero-based (scene A is 0). A preset has 8 scenes.

**Select a scene:**

```
Scene{action: UPDATE, selected_scene: 1}
```

**Rename a scene:**

```
SceneLabel{action: UPDATE, index: <0-7>, label: "<name>"}
```

**Recolor a scene:**

```
SceneColor{action: UPDATE, index: <0-7>, color: <ARGB uint32>}
```

`color` is an ARGB `uint32`; a pinkish scene was `0xFFFF02C2`. Round-trips
exactly.

On any scene edit performed on the unit, the device re-broadcasts the **full set
of 8 scene labels and colors** (indices 0 through 7), not only the one that
changed.

**Copy or swap scenes:**

```
SceneCopy{action: UPDATE, from_index: 0, to_index: 3, is_swap: false}
```

Note the action is `UPDATE`, not `COPY`. This shape came from the device's own
broadcast when a scene was copied **on the unit**; Cortex Control's UI does not
appear to expose scene copy, so it was never observed being sent host to device.
The host-to-device direction was confirmed on hardware (copying scene A onto
scene D took effect on the unit). `is_swap` has not been exercised, and
`from_index` was left at its default 0 in the observed broadcast, so a nonzero
`from_index` rests on the symmetry of the protocol rather than on a capture.

A scene copy can also be done entirely client-side without this message: read
the preset, copy the per-scene `param_values[from]` and per-scene bypass entries
to `[to]`, and write the result back with keyed grid edits.

### 7.5 Grid edits and the edit path

The grid is 4 rows by 8 columns. Grid edits are `Grid{action: UPDATE, preset:
<sparse BinaryPreset>}` messages, and this is the single most important
behavioural fact in the protocol:

> **The device applies a `Grid` UPDATE by locating each chain/model by its
> `row` / `column` KEY, and a save snapshots whatever is currently on the grid.**

Two consequences follow, both confirmed by discriminating experiments on
hardware:

1. **A full-preset `Grid` UPDATE is not applied.** A preset freshly obtained
   from a `RecallPreset` push carries **no explicit `row`** on its chains, so a
   wholesale write-back has nothing to key on and the change is silently
   dropped. A re-pointed `in_portid` written this way read back unchanged.
2. **`File` CREATE ignores `preset_payload`; it snapshots the grid.** With the
   grid left on a factory preset (`in_portid = 1`), a CREATE whose
   `preset_payload` carried `in_portid = 2` saved a slot that read back
   `in_portid = 1`. The `files[].name` *is* taken from the `File` message, so
   the slot gets the right name, but the content is always the current grid.
   Cortex Control's own "Save As" likewise sends no payload.

So **the working edit flow is always:**

```
recall the preset            (loads it onto the grid)
  -> one or more row/column-keyed sparse Grid UPDATEs
  -> save                    (File CREATE, which snapshots the grid)
```

`examples/reroute_and_save.py` is the smallest end-to-end demonstration.

**Chain input routing** (the only shape that actually moves an input):

```
Grid{action: UPDATE, preset{chains{row: 0, in_portid: 4}}}
```

**Block parameter:**

```
Grid{action: UPDATE,
     preset{chains{row: 0,
                   models{column: 1,
                          params{index: 1,
                                 param_values[scene]{float_value: 0.4553}}}}}}
```

`float_value` is normalized 0..1. A knob drag in Cortex Control streams one
`Grid` UPDATE per step. `params.index` is **positional** within the model's
parameter list, and not every index corresponds to a knob visible in the UI.

**Block bypass:**

```
Grid{action: UPDATE,
     preset{bypass{row: 0,
                   colBypass{column: 4,
                             sceneBypass[scene]{bypass: true}}}}}
```

Bypass is per scene, indexed by scene within `sceneBypass`.

A useful corollary for locating rows: in a preset read back from a recall, a
chain's grid row equals its **index in `chains`** when the explicit `row` field
is absent. `client.input_chain_rows()` relies on this.

### Grid block move

Dragging a block one column over sends:

```
GridMove{move{from_col: 4, to_col: 5, is_drop: true},
         grid{rows{modelIds: ...} x4}}
```

That is the move plus a **full 4x8 snapshot of grid model IDs**. No row field was
sent for a row-0 move (proto3 default). The library registers this message type
but does not yet wrap it in a client method.

### 7.7 File operations

All four use `FileMessage` with `type: 0` (presets). `FileMessage.type` appears
to select the kind of item being operated on; only `0` has been observed, so the
meaning of other values is unknown.

**Save As** (action CREATE, which is the proto3 default and so is omitted):

```
File{type: 0,
     folder{key: "/media/p4/Presets/My Presets",
            is_factory: false,
            files{index: 220, name: "My Preset", instrument: 2}}}
```

The target slot is the **linear index**, the name comes from the message, and
there is **no preset payload**: the device saves what is on the grid. The
20-character name limit in Cortex Control is a UI limit, not a protocol one.

**Delete:**

```
File{action: DELETE, type: 0,
     folder{key: <setlist path>, is_factory: false,
            files{key: "<setlist path>/<name>.pb"}}}
```

Deletes address the preset by its **device file path** (name-based, `.pb`
extension), not by slot index. No `delete_from_library` field was sent, although
the schema has one.

**Move:**

```
File{action: MOVE, type: 0,
     folder{key: <setlist path>, files{key: "<setlist path>/<name>.pb"}},
     to_folder{key: <setlist path>, files{index: 219}}}
```

Source by **file path**, destination by **linear slot index**. Only same-setlist
moves have been observed.

**Enumerate:** `File{action: READ}`, covered in [section 7.3](#73-enumerate-a-setlist).

Delete and move are **eventually consistent**: the change takes effect on the
device, but a listing issued a couple of seconds later can still show the
pre-mutation state. Re-enumerate after a short wait.

### 7.8 Other observed traffic

- `Mode{action: UPDATE, mode: 0|1|2}` fires as the UI changes views.
- The device pushes `IOSettings`, which includes a `PortSettings` list with
  per-port `plugged` flags. This is useful ground truth for which physical
  inputs and outputs are connected, and was used to cross-check the port id
  mapping below.
- Types the device pushes with non-protobuf payloads (`License`, `CloudLogin`)
  cannot be parsed as their schema message. A receiver must tolerate that rather
  than treating it as an error; the library logs and skips them.

## 8. Port, instrument, and preset enums

### Input ports (`Chain.in_portid`)

`Chain.in_portid` uses the schema's `GainCalInputPortParameter.InputPortId`
enum **verbatim**. Ids 0 through 14 were all confirmed on hardware (15,
`MAX_PORTS`, is rejected). Confirmation method: diffing presets whose per-row
routing was known against the `in_portid` values read back from the device, and
cross-checking against the device's own `IOSettings` port list.

| Id | Port | Note |
|---|---|---|
| 0 | EMPTY | chain is fed internally (splitter/mixer), not from a port |
| 1 | Input 1 | rear combo jack is the same port, no distinct id |
| 2 | Input 2 | rear combo jack is the same port, no distinct id |
| 3 | Input 1/2 | stereo pair |
| 4 | Return 1 | |
| 5 | Return 2 | |
| 6 | Return 1/2 | stereo pair |
| 7 | Prev. Row | feed from the previous grid row |
| 8 | USB 5 | |
| 9 | USB 6 | |
| 10 | USB 7 | |
| 11 | USB 8 | |
| 12 | USB 5/6 | stereo pair |
| 13 | USB 7/8 | stereo pair |
| 14 | Sidechain buffer | internal source, blank in the UI |

### Output ports (`Chain.out_portid`)

`Chain.out_portid` uses `GainCalOutputPortParameter.OutputPortId` verbatim.
Only some values have been checked against hardware: 4 ("Output 1") and 1
("Output 1/2") were anchored by a preset with known routing, and 2 ("Output
3/4"), 3 ("Send 1/2"), and 10 ("USB 5") were spot-confirmed. The rest are taken
from the schema and should be treated as unverified.

```
0  EMPTY          8  SEND_1         16  NEXT_ROW_3
1  XLR_1_2        9  SEND_2         17  NEXT_ROW_4
2  OUTPUT_3_4    10  USB_OUT_5      18  NEXT_ROW_3_4
3  SEND_1_2      11  USB_OUT_6      19  MULTIPLE_OUTS
4  XLR_1         12  USB_OUT_7      20  USB_OUT_3
5  XLR_2         13  USB_OUT_8      21  USB_OUT_4
6  OUTPUT_3      14  USB_OUT_5_6    22  USB_OUT_3_4
7  OUTPUT_4      15  USB_OUT_7_8    23  MAX_PORTS (sentinel)
```

Values 16 through 19 are internal grid-routing states (feed the next row, or
feed several outputs at once), not selectable physical destinations.

### Instrument tag (`ProductData.instrument`)

Confirmed on hardware: `1` guitar, `2` bass, `4` vocal. The values are powers of
two and `3` is unused, which is consistent with bit flags, though no preset with
multiple bits set has been observed.

Named forms of all of the above live in `pyquadcortex/enums.py` (`Input`,
`Output`, `Instrument`, `Setlist`).

## 9. The pushed preset structure

A `BinaryPreset` obtained from a `RecallPreset` push is **structural**, and how
it is encoded matters when you edit it:

- `chains`, and `models` within them, are identified by `hash`, with `column`
  and parameter `index` **implied by position** rather than stored;
- chains read back from a recall carry **no explicit `row`**, so a chain's grid
  row is its index in `chains` (this is why wholesale write-back does not work,
  see [section 7.5](#75-grid-edits-and-the-edit-path));
- `param_values` **are** present and round-trip exactly (0.0 through 1.0
  verified);
- `in_portid`, `out_portid`, scene labels, and scene colors are present;
- per-scene bypass state lives in the separate `bypass` list, keyed by row then
  column then scene.

See `protocol/proto/Preset.proto` for the full structure.

## Operation coverage

Every operation the library exposes has been exercised live on hardware
(firmware `d14e`, CorOS 4.0.1). "Verified by" means: **read-back** = device state
re-read over the protocol and asserted; **on-unit** = the change was confirmed
visually on the device's own screen.

| Operation | Wire shape (brief) | Verified by | Notes |
|---|---|---|---|
| connect handshake | `ResetCommsBuffers` + `Version` UPDATE + `ModelRepo` READ + `Connection` + subscribe READs | read-back | the connect gate; state pushes flow only after it |
| version read | `Version{action: READ}` | read-back | serial and firmware returned |
| `recall_preset` / `read_preset` | `SetlistPosition{UPDATE, folder_key, position, is_factory, request_id}` then a `RecallPreset` push | read-back | the push echoes the recall's `request_id` |
| `list_presets` | `File{action: READ}` then `File{folder{files[] = ProductData}}` | read-back | factory listing gzipped; 256 slots; listings lag a few seconds after a `File` mutation |
| `switch_scene` | `Scene{UPDATE, selected_scene}` | on-unit | zero-based |
| `set_chain_input` / `reroute_grid_input` | `Grid{UPDATE, preset{chains{row, in_portid}}}` | read-back + on-unit | row-keyed; the only shape that persists input routing |
| `set_param` | `Grid{UPDATE, preset{chains{row, models{column, params{index, param_values[scene]{float_value}}}}}}` | read-back | value round-trips 0.0 to 1.0; param index is positional; not every index is a visible knob |
| `set_bypass` | `Grid{UPDATE, preset{bypass{row, colBypass{column, sceneBypass[scene]{bypass}}}}}` | on-unit | block greyed out on the unit |
| `set_scene_label` / `set_scene_color` | `SceneLabel` / `SceneColor{UPDATE, index, label/color}` | read-back | color is ARGB uint32; exact round-trip |
| `copy_scene` | `SceneCopy{UPDATE, from_index, to_index}` | on-unit | shape came from the device's broadcast, never observed being sent by Cortex Control; host-to-device confirmed (scene D took on scene A). `is_swap` untested |
| `save_current_preset` | `File{CREATE, folder{key, files{index, name, instrument}}}` | read-back | snapshots the GRID; `preset_payload` is IGNORED for CREATE |
| `delete_preset` | `File{DELETE, folder{files{key: "<setlist>/<name>.pb"}}}` | read-back | works, but asynchronous: a listing within about 2 s is stale, about 5 s is reliable |
| `move_preset` | `File{MOVE, folder{files{key}}, to_folder{files{index}}}` | read-back | source by file path, destination by index; asynchronous like delete |
| `write_preset` | `Grid{UPDATE, preset}` | read-back | low-level primitive; applies ONLY row/column-keyed elements. A full recalled preset written back does NOTHING. Use the keyed wrappers |
| `GridMove` | `GridMove{move{from_col, to_col, is_drop}, grid{rows{modelIds} x4}}` | captured only | observed in a Cortex Control session; no client method, not driven host-to-device by this library |

## Open questions

Stated explicitly so nobody builds on a guess:

- **The two device-filled trailer bytes** (offset `n+6`) have no known meaning.
  They do not match common CRC-16 variants. Sending zeros works; ignoring them
  on receive works.
- **The "raw payload" trailer flag** at offset `n+2` is an inference from the
  observation that non-protobuf device payloads carry a nonzero byte there. The
  exact field layout and semantics are unconfirmed.
- **`FileMessage.type`** was always `0` in every observation. What other values
  select is unknown.
- **`SceneCopy.is_swap`** and a nonzero `SceneCopy.from_index` have not been
  exercised on hardware.
- **`delete_from_library`** (on `FileMessage`) exists in the schema but was never
  sent. Its effect is unknown.
- **Most output port ids** are schema-derived rather than hardware-confirmed
  (see [section 8](#output-ports-chainout_portid)).
- **Cross-setlist moves**, downloads/plugin folders
  (`SetlistPosition.is_downloads`, `is_plugin`), IR payloads
  (`FileMessage.ir_payload`), and bulk operations
  (`total_bulk_create_count`, `BulkOperation`) are all present in the schema and
  entirely unobserved.
- **Roughly half the schema's 71 message types** have never been seen on the
  wire by this project, including tuner, looper, MIDI settings, Neural Capture,
  backups, and diagnostics. Their field layouts are known from the schema; their
  behaviour is not.

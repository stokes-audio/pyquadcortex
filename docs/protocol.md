# Quad Cortex USB control protocol

A reference for the USB-HID Protobuf protocol the Neural DSP Quad Cortex uses to
talk to Cortex Control, as re-implemented by this library. Everything here was
established by observing real Cortex Control sessions on the wire and then
confirming each finding live against hardware.

> **Applies to a device profile, and says which.** A profile is what the unit
> reports in its `Version` reply: `device_type` and `zenos_git_hash`, the CorOS
> version (ADR-0020). Every statement below was measured on **Quad Cortex, CorOS
> 4.0.1** (`zenos` 4.0.1, `app_fw` d14e), the maintainer's unit, unless it says
> otherwise. An observation from another profile is written beside the 4.0.1
> record, dated and named, never in its place. `app_fw` alone does not identify a
> profile: a contributor reports d14e on CorOS 4.1.0 as well.
>
> **The protocol is unversioned.** Nothing on the wire identifies a schema or
> protocol version, so none of this is guaranteed across a CorOS update. The
> profile is read from the unit's own `Version` reply at connect, which is the
> only version information the wire offers. Re-verify after a firmware change (see
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
  - [The two flag bytes, confirmed](#the-two-flag-bytes-confirmed)
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
  - [Per-preset tempo, LED and metronome](#per-preset-tempo-led-and-metronome)
    - [MODE is the DEVICE tempo block's parameter 1](#mode-is-the-device-tempo-blocks-parameter-1)
  - [Grid block move](#grid-block-move)
  - [7.6 Per-preset MIDI Out](#76-per-preset-midi-out)
  - [7.6b Moving blocks, and creating a branch](#76b-moving-blocks-and-creating-a-branch)
  - [7.6c Preset fields that are NOT writable](#76c-preset-fields-that-are-not-writable)
  - [7.7c The folder tree, and what else is enumerable](#77c-the-folder-tree-and-what-else-is-enumerable)
  - [7.7b Global device settings](#77b-global-device-settings)
  - [7.7 File operations](#77-file-operations)
  - [7.8 Other observed traffic](#78-other-observed-traffic)
- [8. Port, instrument, and preset enums](#8-port-instrument-and-preset-enums)
- [9. The pushed preset structure](#9-the-pushed-preset-structure)
- [Operation coverage](#operation-coverage)
- [Grid blocks](#grid-blocks)
  - [A placement can be refused, for two known reasons](#a-placement-can-be-refused-for-two-known-reasons)
- [The model catalog (ModelRepo)](#the-model-catalog-modelrepo)
  - [A parameter's scale is in the catalog, and we were not reading it](#a-parameters-scale-is-in-the-catalog-and-we-were-not-reading-it)
  - [`param_values` can contain NaN](#param_values-can-contain-nan)
  - [Adding a block rewrites comboBox values on rows you never wrote to](#adding-a-block-rewrites-combobox-values-on-rows-you-never-wrote-to)
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

Constants in `pyquadcortex/protocol/framing.py`: `REPORT_SIZE = 128` (body),
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
n       4     CortexMessageType.Enum value, uint32 LITTLE-ENDIAN
n+4     1     ENCRYPTED: 1 = the payload is not protobuf and cannot be read
n+5     1     COMPRESSED: 1 = the payload is a gzip stream
n+6     2     zero from the host; the device fills varying nonzero values
```

Two things are surprising and worth stating plainly:

- **The message type tag lives in the TRAILER, not in a header.** A receiver
  cannot know a message's type until the final fragment has arrived.
- **There is no length field**, as above.

The device-filled bytes at `n+6` do not match common CRC-16 variants and their
meaning is unknown. It is safe to send zeros there and to ignore them on
receive. They stay an open question.

#### The two flag bytes, confirmed

The bytes at `n+4` and `n+5` were read as zeros for a long time, and the "raw
payload flag" this document used to describe as an inference was the same two
bytes seen dimly. Both are now confirmed.

**Method.** Two runs, offline then live.

*Offline*, and the one that carries the weight: every logical message in the
three USBPcap captures of Cortex Control 4.0.1 was reassembled and its whole
trailer recorded, both directions.
`research/captures/windows-session-0{1,2,3}-nonaudio.pcapng` in the lab repo,
15,675 messages, device on CorOS 4.0.1 (`zenos_git_hash: 4.0.1`, app firmware
`d14e`). The check is `research/scripts/trailer_flags.py` in that repo. It is
the only run containing a `CloudLogin` exchange, because this library never
logs in.

*Live*, 2026-09-03, on the unit through this library's own client on macOS
rather than Cortex Control: 1,000 messages over a connect burst, a full folder
listing and one recall, with the same result on every row below. Same firmware,
so it is evidence about a different client and a different host, **not** about a
newer CorOS. `research/scripts/trailer_flags_live.py`.

It also settles a practical point: seeing the ENCRYPTED flag needs no cloud or
account operation. `License` is already in the handshake's subscribe burst, so
an ordinary `connect()` provokes the encrypted reply.

| what | result |
| --- | --- |
| `n+2` and `n+3` | zero in all 15,675 messages, both directions |
| `n+4` set | 13 messages, all of them `License` or `CloudLogin` |
| `n+4` clear | 15,662 messages, none of which failed to parse for that reason |
| `n+5` set | 39 messages, every one starting with the gzip magic `1f 8b` |
| `n+5` vs the gzip magic | agree 15,675 out of 15,675 |
| `n+6..n+7` | always zero from the host; nonzero on 2,700 of 14,539 device messages |

The live run reproduced every row: `n+2`/`n+3` zero on all 1,000, the COMPRESSED
flag and the gzip magic bytes agreeing 1000 out of 1000, ENCRYPTED on the
`License` reply and nothing else, `File` arriving 778 plain and 20 gzipped in the
one session, and `n+6..n+7` nonzero on 205 of 1,000.

**Both flags describe the FRAME, not the message type.** That is the part worth
keeping, because a per-type table would look right and be wrong:

| type | direction | flag | payload |
| --- | --- | --- | --- |
| `License` | host to device | encrypted 0 | plain protobuf, `action: READ` |
| `License` | device to host | encrypted 1 | 17 bytes, not protobuf |
| `File` | device to host | compressed 0 | plain protobuf, x1,173 |
| `File` | device to host | compressed 1 | gzip, x30 (the factory library listing) |
| `RecallPreset` | device to host | compressed 0 | plain protobuf, x1 (a 9,326-byte push) |
| `RecallPreset` | device to host | compressed 1 | gzip, x9 |

**What "encrypted" means here, and what it does not.** What was measured is that
the flag marks a payload a receiver must not parse as protobuf, and that only
`License` and `CloudLogin` carry it. The name comes from the .NET library
CortexUSB, which decrypts on the same flag. This library does neither: it reads
the flag, labels the frame, and hands the bytes back untouched. See
[ADR.md](ADR.md) (ADR-0019) and the note below.

**Not reproduced: the compressed flag on types 32 and 33.** CortexUSB skips
decompression for `KeepAlive` (32) and `GlobalTempo` (33) by default, which
reads like an observation that those two set the compressed byte without
carrying compressed data. In these captures they do not: `GlobalTempo` x3,785
and `KeepAlive` x944, every one with both flags clear. Recorded as not
reproduced on CorOS 4.0.1 rather than as disproved, since their exception list
may come from firmware or a client we did not measure.

**The type field's width is not observable.** Bytes `n+2` and `n+3` are always
zero, and every message type in the schema is under 256, so a uint32 type and a
uint16 type followed by two reserved zero bytes produce identical bytes on the
wire. This document and the codec take the uint32 reading, which matches
CortexUSB, and nothing depends on the choice. Do not record it as confirmed.

### 2.4 Compressed payloads

Two different, independent kinds of compression appear:

- **Frame-level gzip.** Some device payloads are gzip streams: the reassembled
  payload starts `1f 8b` and the decompressed bytes are the ordinary protobuf
  message for the frame's type. `RecallPreset` pushes (carrying a full preset)
  do this, as does the factory-library folder listing. A receiver should gunzip
  before parsing when it sees the magic bytes.

  The trailer's COMPRESSED byte says the same thing (see 2.3), and agreed with
  the magic bytes on all 15,675 messages measured. **The library still tests the
  magic bytes, not the flag.** The argument is CortexUSB's own exception list:
  they read the flag and then had to add a skip-list for two message types.
  Magic bytes need no exceptions, so the flag stays informational. `framing`
  reports it, and logs a line if the two ever disagree, but the magic bytes
  are what decide whether a payload is gunzipped. The agreement was measured on
  CorOS 4.0.1 and nowhere else, so a firmware that broke it should say so rather
  than fail quietly.
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
0a 00 00 00         trailer: type = 10 (Version), uint32 LE
00                  trailer: ENCRYPTED = 0
00                  trailer: COMPRESSED = 0
00 00               trailer: device bytes, zero from the host
<116 zero bytes>    padding to the 128-byte body
```

The first report of a session, `ResetCommsBuffers` (a session hello, not an
unlock):

```
02                  report ID 0x02
2c                  len = 44 (36 protobuf + 8 trailer)
c0                  flags = complete
08 00               protobuf: request_id = 0
12 20 ...           protobuf: session_id = 32 hex characters
34 00 00 00         trailer: type = 52 (ResetCommsBuffers), uint32 LE
00 00               trailer: ENCRYPTED = 0, COMPRESSED = 0
00 00               trailer: device bytes, zero from the host
<82 zero bytes>     padding
```

A 290-byte `Version` reply from the device, spanning three input reports (this is
`tests/fixtures/frames/version_reply_multi.json`):

```
report 1:  01 | 7e | 40 | <126 data bytes>   starts "Linux buildroot ..."
report 2:  01 | 7e | 00 | <126 data bytes>   middle: no header of any kind
report 3:  01 | 2e | 80 | <46 data bytes>    last 8 bytes are the trailer:
                                               0a 00 00 00  type = 10 (Version)
                                               00           ENCRYPTED = 0
                                               00           COMPRESSED = 0
                                               60 b3        device-filled, ignored
           + 80 bytes of stale padding
```

The `Version` reply carries the device's kernel string, `zenos` version,
`app_fw_version`, bootloader version, MAC address, and serial number, among
other fields (see `VersionMessage` in the schema).

A `License` reply from the device, the smallest frame with a flag set (this is
`tests/fixtures/frames/license_reply_encrypted.json`):

```
01                  report ID 0x01 (device->host input report)
19                  len = 25 (17 payload + 8 trailer)
c0                  flags = FIRST|LAST
51 f4 99 f9 ...     payload: 17 bytes, NOT protobuf
3a 00 00 00         trailer: type = 58 (License), uint32 LE
01                  trailer: ENCRYPTED = 1
00                  trailer: COMPRESSED = 0
a2 b6               trailer: device-filled, ignored
```

The host's `License` READ that provoked it carries `08 03` and ENCRYPTED = 0
(`tests/fixtures/frames/license_read.json`). Same message type, opposite flag.

## 3. Message types and actions

Every frame's trailer carries a `CortexMessageType.Enum` value. The schema
declares **73 types** (`Undefined = 0` through `RemoteControl = 72`, with
`NumberOfMessageTypes = 73` as a sentinel). `ModelPreset = 71` and
`RemoteControl = 72` are additions recovered from Cortex Control 4.1.0. The
ones this library uses:

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
| 71 | `ModelPreset` | Cortex Control model-preset state; decoded, no client wrapper |
| 72 | `RemoteControl` | physical display capture and touchscreen input |

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

Most scalar fields in a preset payload are wrapped in a synthetic
`oneof _field` (proto3 `optional`), so "set to zero" and "not set" are
distinguishable on the wire and via `HasField()`. The protocol makes use of
that: absent fields mean "unchanged / not addressed", which is what makes sparse
keyed grid edits work.

### 3.1 Remote touchscreen control

`RemoteControl{UPDATE, mouse{x, y, type}}` addresses the physical display in
raw 800 x 480 pixels. On QC CorOS 4.1.0 a reliable tap is two messages at the
same coordinate, 20 ms apart: enum `RELEASE=1` followed by enum `PRESS=0`.
Those runtime semantics are inverted against the recovered enum labels; using
the labelled order leaves the touch held and eventually enters Grid drag mode.
The nominal `TAP` type did not honour its supplied coordinate, and `MOVE` was
not a reliable positioning primitive.

One screenshot READ and a 300 ms UI settle must precede the first mouse event
on a connection. Without that priming sequence CorOS intermittently ignores the
mouse pair. `tap_screen()` performs it lazily; later taps on the same connection
do not repeat the screenshot read.

The device acknowledges neither mouse message. `tap_screen(x, y)` therefore
sends the confirmed pair and returns after transmission; it cannot claim that a
particular screen opened. `(184, 147)` opened the second block on the top Grid
row during the hardware verification.

The readback half is asynchronous: `RemoteControl{READ, screenshot:{}}`
produces an uncorrelated `RemoteControl{UPDATE,
screenshot:{payload:<PNG>}}`. On CorOS 4.1.0 the payload is a complete 800 x
480 PNG in one reassembled protocol message. It carries no `request_id`, so a
normal request waiter misses it; `capture_screen()` installs a type waiter first
and validates the PNG signature before returning the bytes.

**But presence is NOT universal, and `HasField` raises on a field that lacks it**
(`ValueError: Field X does not have presence`). The exceptions are not obscure:
`Preset.proto` has 22 singular fields without presence and
`ProductionAutomation.proto` has 243, including every `action` field and all of
`SceneCopyMessage`, `SceneLabelMessage` and `SceneColorMessage`. The one that
bites hardest is **`SceneBypass.bypass`**, because walking per-scene bypass is a
natural thing to want and the obvious loop crashes on the first preset read.

Use `pyquadcortex.protocol.field_present(msg, "name")`, which answers `False` instead of
raising. Two more presence details worth knowing, both observed in real payloads:
`Chain.row` is absent on a recalled preset (see [7.5](#75-grid-edits-and-the-edit-path)),
and `Param.index` is absent too - a parameter's index is its POSITION in the
`params` list.

**The protocol is symmetric.** The same message types flow in both directions.
The device answers a host `Version` READ and then asks one of its own, and it
broadcasts `Scene`, `SceneLabel`, `SceneColor`, `SceneCopy`, `Grid`, and
`RecallPreset` messages when the user operates the unit's own touchscreen.

That symmetry is not free for a listener. A message of a type the host tracks is
not necessarily news about the device: the device's own `Version` READ carries
`action` and nothing else. So anything counting inbound messages per type needs a
rule for the ones that say nothing - the model's cache counts a message only when
it carried a field the cache keeps or a field it does not
(`pyquadcortex/device/state.py`, `_apply_one`).

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
3. device: `Version{action: READ}` to the host. Read this as the TAIL OF STEP
   2's ANSWER rather than as a separate handshake step. The capture cannot say
   so on its own, but our own reads can: measured 2026-08-27 on `d14e`, ten host
   `Version` READs out of ten were answered by a `Version{UPDATE}` carrying
   fifteen fields and then, 0.5-0.8 ms later, by the device's own
   `Version{READ}` carrying `action` alone. Cortex Control answers
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

`QuadCortex._hello()`, which `pyquadcortex.protocol.connect()` runs for you, does the
same thing with one deliberate difference: it does **not** issue a host
`Version` READ, because a redundant host READ would race a caller's later
version request - READ replies carry no `request_id` to disambiguate them, so
the transport hands a `Version` with no id to whichever waiter is first in line.

Skipping it also means the device asks nothing back. Measured 2026-08-27 on
`d14e`: a connect through `_hello()` and its whole burst produce exactly one
inbound `Version`, an `UPDATE` carrying `cortex_control_version_valid` in answer
to the announce, and eight seconds of idling after it produce none. So step 3
above is a consequence of step 2 rather than something the device does on
connecting.

### 4.3 Keepalive and disconnect

Cortex Control sends `KeepAlive{action: UPDATE}` about once per second. The
library defaults to every 5 seconds, and the device tolerated 20-second idle
gaps in the capture without dropping the session, so the exact interval is not
critical. On quit, Cortex Control sends `Connection{connected: false}`. This library now does the
same, as the first step of teardown - the send needs a live transport, so it has to
precede stopping it and closing the handle.

**Does abandoning a session leak anything device-side?** Measured rather than
assumed, and the answer is *nothing observable*, with the honest caveat that the
measurement is coarse.

The experiment: baseline a session's health, then open and abandon **12** sessions
with no goodbye (each with its own fresh `session_id` and full 22-type subscription
set, exactly as before this change), then re-measure. Health meant handshake
duration, whether the `RecallPreset` seed push still arrived, and how long a
`read_preset` round trip took.

Afterwards: the seed push still arrived, subscriptions still fired, and
`read_preset` took the same time (9.04s -> 8.77s, i.e. unchanged). One handshake
came in at 4.79s against a 2.03s baseline, which looked like degradation until
handshake variance was characterised: six clean sessions spanned **2.03s to 3.80s**,
a spread of 1.77s. So a single 4.79s sample is barely outside normal noise, and
handshake timing is in any case dominated by this library's own two-second settle,
which makes it a poor instrument for small effects.

**What was NOT established:** whether the device supersedes an old session when a
fresh `ResetCommsBuffers` arrives, whether it reaps sessions whose keepalives stop,
or whether there is any ceiling on accumulated sessions. There is no evidence of a
leak, and no proof there is not one. Nothing here justifies a workaround; sending
the goodbye is worth doing because it matches the real client, not because a fault
was demonstrated.

### 4.4 A `Version` READ is answered twice, and `request()` cannot tell the two apart

Measured 2026-09-03 on CorOS 4.0.1 / d14e with a listener on every inbound
`Version`. A host `Version{READ}` is answered by the full `UPDATE` with fifteen
fields and, 1 ms later, by the device's own `Version{READ}` carrying `action`
alone - the same question the device asks Cortex Control (section 4). Neither
carries a `request_id`, and `Transport.request` correlates by type when no id is
present, so the second message is handed to whichever `Version` waiter is
registered when it lands. Five `version()` calls in a row returned full, empty,
full, empty, full. With a second between calls the device's READ arrives while
nobody is waiting and is dropped, which is why the pattern is easy to miss.

The consequence is a read that answers `""` for `custom_name` and `""` for the
serial every second call, with no error. `version()` therefore accepts only a
`Version` carrying an identity field, `device_serial_number` or `app_fw_version`.
The device's own READ carries neither, nor does the handshake's
`cortex_control_version_valid` answer, so both are left for the RX path to log; a
partial reply carrying one of the two is still returned, because the cache keeps
what the unit sent and re-reads for the rest. A sparse host `Version{UPDATE, custom_name}`
(a rename) is echoed once with the same two fields, does not re-run the
handshake gate, and leaves reads answering - measured three times the same day.

After the burst the device needs a moment before it treats the client as
connected; a command sent too soon gets no push (observed as flaky preset-read
timeouts). `connect(settle=...)` waits 2 seconds by default.

The `RecallPreset` subscription produces a **seed push** of the currently loaded
preset, delivered lazily (10 to 25 seconds later has been observed). Any code
waiting for a preset push must be able to ignore it; see
[section 5](#5-requestresponse-correlation).

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

**Note the factory path's trailing slash.** Cortex Control sends it verbatim on a
recall, and user setlist paths have none. `enums.Setlist` carries both strings so
callers do not have to remember this.

**The slash is not consistent across operations.** A factory *recall* needs the
trailing slash, but the device reports that same folder's *listing* key as
`/opt/neuraldsp/Factory Library`, with no slash. Any code matching a pushed
`folder.key` against a setlist constant must normalize trailing slashes on both
sides; comparing them raw silently matches nothing (which surfaces as a listing
that never arrives, not as an error).

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
`cloud_id`. The factory listing arrives gzip-compressed at the frame level.

Three things to expect from that push:

- **A single `File` READ pushes every folder the device knows about**, not only
  the setlist of interest: both setlists, each installed plugin's artist preset
  folders, the impulse-response library, and several internal keys. Match the one
  you want by `folder.key` (normalizing trailing slashes, see
  [6.1](#61-setlist-paths)) and ignore the rest.
- **A setlist always lists its full 256 slots**, occupied or not. Empty slots
  appear as entries with an `index` and no `name`, so a caller that wants real
  presets has to filter them out. A user setlist holding a dozen presets still
  reports 256 entries.
- **A listing that arrives is complete**, so there is no need to wait for a
  second one to be sure of it. Five `File` READs against an 18-preset setlist each
  produced a full 18, and no short listing has been observed. Duplicate pushes of
  the same listing carry identical contents and can be ignored.
- **A READ does not reliably produce a listing promptly.** In those same five
  rounds, two saw nothing for the setlist of interest within 8 seconds; delivery is
  lazy. So a timeout means "ask again", not "the setlist is empty", and a caller
  that deletes everything a listing reports should re-enumerate rather than assume
  one pass saw everything. `wait_for_listing()` does this.

A client should also require `len(folder.files) > 0`, because the device pushes
empty folder messages for keys with no contents.

**Listings are eventually consistent, and the lag scales with the number of
mutations.** After a single `File` DELETE or MOVE, a listing within about 2
seconds can show the pre-mutation state and about 5 seconds was reliable. That does
NOT generalise: after deleting eleven presets, a listing 5 seconds later still
returned all eleven - the deletes had in fact all succeeded, and a fresh
connection moments later showed an empty setlist.

So a fixed sleep produces false negatives, which in a careful script reads as "the
operation failed, abort" on work that actually worked. **Poll until the listing
settles** rather than sleeping a fixed interval: `QuadCortex.wait_for_listing()`
does this, either against a predicate or by waiting for two consecutive identical
listings.

**The device renames a saved preset if the name collides.** Saving is not
name-preserving. Within a setlist:

- A **unique** name is stored verbatim, with no length limit observed (a
  36-character name came back intact).
- A name that **already exists** in that setlist is de-duplicated: the base is
  truncated as needed and a `_N` suffix appended, to 20 characters in total.
  Saving a second `Cali Basswalk [Ret1]` produced `Cali Basswalk [Ret_1`, and a
  second 36-character `ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789` produced
  `ABCDEFGHIJKLMNOPQR_1`.

A client that cares what the preset ended up called must read the slot back
rather than assume the requested name. (Names like `Cali Basswalk_1` appearing in
a user setlist are this mechanism, not the user's doing.)

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

Note the action is `UPDATE`, not `COPY`. Cortex Control provides no way to copy a
scene, so this message could not be learned from its traffic. Instead the shape
was read off the device's own broadcast when a scene was copied **on the unit**,
and sending that shape host to device is confirmed working on hardware.

**`from_index` is honored.** This was checked with a discriminating test, because
`from_index: 0` is also the protobuf default, so copying scene A would not
distinguish "the field works" from "the field is ignored". Sending
`SceneCopy{UPDATE, from_index: 1, to_index: 3}` against a preset whose scenes A
and B differ made scene D an exact copy of **scene B**, not of scene A. Verified
by saving the resulting grid and reading it back: across every block that follows
scenes, scene D matched scene B and differed from scene A.

**`is_swap` exchanges the two scenes.** Also confirmed by read-back: with
`is_swap: true`, scene B ended up holding scene D's former state and scene D held
scene B's, rather than one overwriting the other.

**The scene LABEL and COLOUR both travel with the state**, in both modes. A copy
renames and recolours the destination scene to match the source; a swap exchanges
both. Confirmed by read-back with nothing else sent - `copy_scene(E, B)` on factory
28A moved both the label `'Clean +VMT'` and the colour `0xff45f862` onto scene B,
which had been `'Bright Punch'` / `0xff0a74e0` - and independently by performing the
same copy on the unit's own screen.

So `SceneCopy` is not limited to the audible state: a caller who only wants the
sound copied should expect the label and colour to move as well, and a caller
reproducing a scene map gets the colour for free and needs no `set_scene_color`
calls for copied scenes.

**An unlabelled scene is a single SPACE, not an empty string.** Factory "Cali
Basswalk" (27E) has four scenes it does not use and all four labels read `" "`; 34 of
the 136 scene labels across 17 factory presets are that. So `if not label` is wrong,
`label.strip()` is right, and writing a blank scene means sending `" "` to match what
the unit does. `pyquadcortex.protocol.SCENE_UNLABELLED` holds it, and
`set_scene_label(index, None)` sends it - confirmed to round-trip a save as `" "`.

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

**`ColBypass.sceneMode` decides whether the per-scene values mean anything.** A
block whose `sceneMode` is true follows the scenes: its `sceneBypass` array is
live, and switching scenes changes whether that block is engaged. A block whose
`sceneMode` is false has a single global bypass state that is the same in every
scene, and its stored `sceneBypass` entries are **not maintained** - they can hold
stale or contradictory values that do not match what the unit displays.

This is easy to trip over when reading a preset: a block can appear from its
`sceneBypass` array to differ between scenes while the unit shows it identical in
all of them. In one observed preset, only 5 of the 32 grid positions had
`sceneMode` set. Filter on `sceneMode` before comparing scenes, or drawing
conclusions from a diff.

**Per-scene parameter values ARE writable**, but the shape is nothing like it
looks. `params{index, param_values[scene]{float_value}}` reads as though the index
selects a scene. It does not, and three separate facts have to line up. All of
this was established by controlled experiment, each round saved and read back:

- **`param_values[0]` is applied to whichever scene is ACTIVE.** The index is not
  a selector, so nothing should ever be padded. Entries past index 0 are ignored.
- **Only a parameter whose `Param.scene_mode` is set keeps per-scene values.**
  Without it the parameter has ONE global value, so writing it appears in all
  eight scenes - which is easily mistaken for "the write hit every scene".
- **`scene_mode` IS host-writable, but only when sent ALONE.** A `Grid` update
  carrying both `scene_mode` and a `param_values` entry is treated as a plain
  value write and the flag is silently dropped. Sent by itself, it sticks. This is
  the long-press assignment the unit's own UI performs.

So the sequence for a per-scene parameter value is three messages:

```
Grid{UPDATE, chains{row, models{column, params{index, scene_mode: true}}}}   # flag alone
Scene{UPDATE, selected_scene: N}                                            # sit on the scene
Grid{UPDATE, chains{row, models{column, params{index, param_values{value}}}}}
```

Ordering over the pipe is enough; no settle delay is needed. It works identically
in `chains[].output_control[]`, so the Lane Output Control can hold a per-scene
volume - which is how a "silent scene" that mutes without leaving the preset is
built. `set_param(scene=...)` issues exactly this, for every target that has scenes.

Padding `param_values` up to a scene index remains actively destructive: the
entries below carry protobuf defaults, the device reads index 0, and the parameter
ends up **0.0 in every scene**. Nothing in this library pads.

**A bypass update must not carry a `chains` element.** Sending the same
`bypass{row, colBypass{...}}` group with an otherwise-empty `chains{row}` beside it
made the device ignore the whole message: nothing changed in any scene. Removing
the chains element and sending only the bypass group applied it immediately. So a
sparse update should carry exactly the one thing it means to change - an empty
sibling element is not neutral. (Observed as a controlled A/B, twice.)

**Bypass is different, and per-scene bypass DOES work** - but not by index. The
device applies `sceneBypass[0]` to whichever scene is **active** and ignores any
entry beyond it. Re-verified in a controlled run with the active scene set
explicitly rather than inferred: an index-3 `true` did not reach scene D, while
index 0's default did reach the active scene. So to bypass a block in one scene, switch to that scene and write
index 0; ordering over the pipe is enough, with no settle delay needed.
`set_bypass(scene=...)` does exactly that. Only blocks whose `ColBypass.sceneMode`
is set follow scenes at all (see [7.4](#74-scenes)).

**Rows and columns are zero-based on the wire, and the unit's screen labels rows 1
to 4.** `chains[0]` is the top row. Also worth checking before assuming an edit is
audible: `out_portid` **16 to 18** are internal row-to-row routing rather than
physical outputs, so a lane set to one of those can be muted while the signal still
leaves the unit through another row. **19 (`MULTIPLE`) is a real destination.** In factory "Brit 2203", row 0 has `out_portid: 16` (into the next row)
and row 2 has `19` (MULTIPLE, the actual Multi-Out).

For "does this row make sound", the explicit split of `out_portid` values: **real
destinations** are 1-15 (XLR 1/2, Out 3/4, sends, USB 5-8 and their pairs, per the `Output`
enum) and **19** (MULTIPLE, the Multi-Out); **internal routing** is 16-18 (`NEXT_ROW_*`,
audio leaves through another row); **0** is unrouted. The device does not validate the
field - nonsense ids store fine - so nothing outside those ranges means anything.

**Every row reports all 8 column slots.** Empty ones arrive as `Model` entries
whose `hash` is absent or zero, so `len(chain.models)` is 8 for every row -
including entirely empty rows - and is not a block count. `output_control` and
`input_control` are padded the same way: one entry on every row, whether or not the
row holds anything.

**`splitter`, `mixer`, `combined_splitter` and `split_control_points` are NOT.**
They exist only on rows 0 and 2 and are empty on rows 1 and 3, because a branch can
only originate on an even row, with its parallel lane on the row below. Counted
across all 68 rows of 17 factory presets:

| collection | row 0 | row 1 | row 2 | row 3 |
|---|---|---|---|---|
| `models` | 136 | 136 | 136 | 136 |
| `output_control` | 17 | 17 | 17 | 17 |
| `input_control` | 17 | 17 | 17 | 17 |
| `splitter` | 17 | 0 | 17 | 0 |
| `mixer` | 17 | 0 | 17 | 0 |
| `combined_splitter` | 17 | 0 | 17 | 0 |
| `split_control_points` | 17 | 0 | 17 | 0 |

So a splitter or mixer write addressed to row 1 or row 3 goes into a collection the
device does not have there and does nothing. `Splitter(row)` and `Mixer(row)`
raise `ValueError` on an odd row rather than letting a write be built.

Nor is `in_portid == EMPTY` an occupancy signal: it means "not fed from a physical jack", the normal
state of any non-input row. Factory "Brit 2203" has six blocks on row 2 with
`in_portid` EMPTY. Use `pyquadcortex.protocol.blocks(preset)` to iterate the cells that
actually hold something.

**Row output routing.** `Grid{UPDATE, preset{chains{row, out_portid}}}` re-points a
row's output, the mirror of the confirmed `in_portid` shape, and it survives a save
and recall.

**The device does NOT assign an output automatically.** A row given blocks and a
physical input keeps `out_portid` at 0 - confirmed by adding a block plus Input 2 to
an empty row and reading it back. So setting the output is a REQUIREMENT when
building a chain on a fresh row, not a convenience: without it the row never reaches
a jack.

All of `out_portid` 5, 6, 7, 8, 9, 11, 20 and 22 were accepted and stored verbatim,
so the device does not validate or normalise the value - passing a nonsense id will
be kept, not rejected. Values **16 to 18** are internal row-to-row routing rather
than jacks, while **19 (`MULTIPLE`) IS a physical destination** - it is what factory
presets use to reach the Multi-Out, and a tester reported setting it on 8 presets with
the audio arriving each time (factory "Brit 2203" uses 16 on row 0 to feed the next row, and 19,
MULTIPLE, on row 2 for the actual Multi-Out).

**Splitter and mixer.** Both are sub-collections of a chain, alongside
`output_control`: `chain.splitter[]` holds "Splitter AB" (model `10000`: LEVEL TO A,
LEVEL TO B, STEREO) and `chain.mixer[]` holds "Mixer" (model `11000`: LEVEL A, PAN A,
LEVEL B, PAN B, PHASE, MIXER LEVEL, SPLIT MODE).

The MIXER is writable, with the same row-keyed shape used for `models[]`, and it
supports per-scene values. This matters because factory presets build their scenes
out of it rather than out of bypass: in "Darkglass AO900 1" nothing is bypassed in
any scene, and all eight scenes come from per-scene `LEVEL A` / `LEVEL B` across two
rows, giving four amp paths. A client that cannot write the mixer cannot reproduce
that.

**The splitter is written through `chain.combined_splitter`.**

```
Grid{UPDATE, preset{chains{row: 0, combined_splitter{params{index: 3,
                                                     param_values{float_value}}}}}}
```

A separate repeated field on the same chain, carrying no hash and no column. Writing
there works and propagates to both representations: setting `LEVEL TO A` to 0.25 read
back as 0.25 in `combined_splitter[3]` and in `splitter[0]` alike.

**`chain.splitter[]` is a read-only view of the same state.** A write addressed to it
is silently ignored - accepted on the wire, absent on read-back - so it must not be
used as a write target.

Parameter indices follow the **unified** model 10004 (`TYPE, STEREO, BALANCE, LEVEL TO
A, LEVEL TO B, FREQUENCY, MODE`), whatever type-specific legacy id the preset reports
(10000 Splitter AB, 10002 Splitter Balance, 10003 LR Crossover). Which parameters
apply depends on `TYPE`: levels for A/B, `BALANCE` for Balance, `FREQUENCY`/`MODE` for
Crossover.

Splitter parameters read back with `scene_mode` false in the factory content examined,
so per-scene splitter values appear to be unusual, though the promote/switch/write
sequence is offered for them anyway.

**Where a row splits IS readable - just not from the splitter.** Neither the splitter
nor the mixer carries `column`, so the position looked unknowable. It lives in
`Chain.split_control_points` instead, whose `split` and `mix` fields give the columns
where the lane leaves and rejoins.

**`split` and `mix` have no presence**, so `HasField` reports them absent even when
set, and code that gates on presence - the correct habit everywhere else in this
schema - sees nothing here. Read them directly, as `pyquadcortex.protocol.splits()` does.

Confirmed: factory "Darkglass AO900 1" (27H) and "Darkglass AO900 2" (28A) both report
`(split=4, mix=4)` on rows 0 and 2, and the parallel lane's single block does sit at
column 4. Rows that do not branch report **`-1`** for both, as factory "Brit 2203"
does on its serial rows.

**A branch is marked by `split` alone, and `mix` is independent of it.** A lane may
branch and never recombine, in which case `split >= 0` while `mix == -1`. Three
factory presets do exactly that: "Strat Ambience" (05B) reports `(2, -1)` on row 0,
"Classic Pedalboard" (07C) `(7, -1)`, "Stereo Lead" (11B) `(5, -1)`. `split` and
`mix` need not agree either - 07C reports `(3, 4)` on row 2 and 11B `(1, 7)`. So test
whether a branch exists on `split`, and read `mix` separately;
`Split.rejoins` does that.

**The row below a branch is spoken for, even when it is empty.** The parallel lane
lives there, so a row can hold no blocks and still not be available: writing to it
puts content inside the existing chain's parallel path rather than beside it. Block
count alone therefore answers "is this row free?" wrongly. `pyquadcortex.protocol.free_rows()`
excludes both occupied rows and lane rows.

### Per-preset tempo, LED and metronome

Reported as a dead end, and it is not. Each preset carries a `TempoControl` block
(model `25000`) in `BinaryPreset.tempoProgramData` - a REPEATED field, despite
holding a single entry - with 24 parameters including `TEMPO`, `TYPE`, `LED LIGHT`,
`VOLUME`, `PAN`, `START`, `TIME SIGNATURE`, `NOTELENGTH`, `SOUND`, `ROUTING` and a
run of `STEPSTATE*`.

**It is writable, even though it is not row or column keyed.** A `Grid` UPDATE
carrying `tempoProgramData{params{index, param_values}}` is applied and survives a
save and recall, with the hash optional. That contradicts the reasonable assumption
that a Grid update only ever applies row/column-keyed elements, so it is worth
stating plainly. Confirmed: `LED LIGHT` 1.0 -> 0.0 turns the tempo LED off, and
`VOLUME` 0.6131 -> 0.0 silences the metronome.

**The Tempo menu's parameter indices**, mapped by using each control on the unit in a
named order:

| index | control on screen | catalog name |
|---|---|---|
| 0 | TEMPO | TEMPO |
| 1 | - (in the preset) | TYPE. NOT written by any control in the menu, in the PRESET copy - it stayed 0.0 through every measured flip. In the DEVICE copy carried by `GlobalTempo.params` the same index is **MODE**: `0.0` PRESET, `1.0` GLOBAL. See below |
| 2 | Tempo LED | LED LIGHT |
| 3 | Volume | VOLUME |
| 4 | **The unit's MUTE** (`1.0` = AUDIBLE, `0.0` = muted) | START in the catalog, PLAYBACK in the manual, MUTE on the unit's own Tempo page - one control, three names. TRACED: pressing the unit's MUTE button writes `0.0`, pressing it again writes `1.0`. Note it is INVERTED against the label a player sees. This table said the opposite for two releases, having inferred polarity from the Looper X mirror's NAME (METRONOME MUTE) - and that mirror is inverted too |
| 5 | Pan | PAN |
| 6 | Time Signature | TIME SIGNATURE |
| 7 | **Subdivisions** | NOTELENGTH - the names disagree |
| 8 | Sound | SOUND |
| 9 | Routing | ROUTING |
| 10-22 | the per-beat cells, beat 1 first | `STEPSTATE0` to `STEPSTATE12` |
| 23 | - | *absent from the catalog, and unattributed* |

The catalog DOES describe these - 23 parameters for model `25000` - while the stored
preset carries 24. What it gets wrong is two of the NAMES, hence
`targets.Tempo.NAMES`.

**Correcting this table:** two releases listed indices 8 and 9 as absent from the
catalog. They are not. `SOUND` (steps=6) and `ROUTING` (steps=5) are described at
exactly those indices, and every other index is described too. Only NAMES ever
disagreed, never the coverage. Index 23 is the single genuinely undescribed one.

**For the list-valued ones the catalog's `steps` IS the option count**, and the wire value
of option N is `N / (count - 1)`. TIME SIGNATURE has 21 options, SUBDIVISIONS 4, SOUND 6,
ROUTING 5 - and the observed values fit exactly: the second subdivision stored 0.3333
(1/3), the second time signature 0.05 (1/20), the fourth routing 0.75 (3/4).

That does NOT hold for a parameter whose options enumerate the preset's blocks - a
Doubler's TRIGGER publishes `steps=45` while the real list is 19 to 25 entries. For those
the preset's `dynamic_steps` is authoritative. Tempo parameters carry no `dynamic_steps`
at all, so their option NAMES are not available from the device, and the manual does not
enumerate them either. **All four lists are now named**, read off the unit's own dropdowns top to bottom, with
the ordering confirmed by selecting the LAST entry of each and seeing the wire store
exactly 1.0:

| control | options, in order |
|---|---|
| SUBDIVISIONS (4) | `1/4`, `1/8`, `1/8T`, `1/16` |
| ROUTING (5) | `MULTI`, `HP`, `OUT 1/2`, `OUT 3/4`, `SEND 1/2` |
| SOUND (6) | `BLIP`, `BLOCK`, `COWBELL`, `DIGITAL`, `DRUM KIT`, `SOFT KIT` |
| TIME SIGNATURE (21) | `2/4` to `13/4`, then `3/8`, `6/8`, `9/8`, `12/8`, then `5/8 (3+2)`, `5/8 (2+3)`, `7/8 (3+2+2)`, `7/8 (2+3+2)`, `7/8 (2+2+3)` |

Every earlier one-off pairing agrees: 1/8 notes stored 0.3333 (option 1 of 4), 3/4 stored
0.05 (option 1 of 21), the factory default 0.1 is 4/4 (option 2), Block stored 0.2 (option
1 of 6), OUT 3/4 stored 0.75 (option 3 of 5). They are the enums
`TempoSubdivision`, `MetronomeRouting`, `MetronomeSound` and `TimeSignature`.

One correction: an earlier note had ROUTING option 0 as the headphones. It is `MULTI` -
the wrong reading came from assuming an operator's starting point matched the factory
default.

**The per-beat states: indices 10 to 22 are beats 1 to 13.** Each beat of the bar can be
set independently - the cells on the Tempo page - and each is a four-option list at
`option / 3`:

| wire | option | state |
|---|---|---|
| 0.0 | 0 | normal, the ordinary click |
| 0.333 | 1 | off, the beat is skipped |
| 0.667 | 2 | accented, louder |
| 1.0 | 3 | de-emphasized, softer but audible |

Traced by touching cells on a 4/4 preset in a known order, from a baseline of
accent-normal-normal-normal. One touch on beat 3 wrote index 12 = 0.333. Three touches
on beat 4 walked index 13 through 0.333, 0.667, 1.0. Four touches on beat 1 walked
index 10 from 0.667 through 1.0, 0.0, 0.333 and **back to 0.667**.

That wraparound is the whole experiment. A cell cycles UP by 1/3 and wraps, so
returning to its starting value in four touches proves the count is exactly four
rather than assuming it from `steps`, and it fixes the cycle order at the same time.
Worth copying as a method: to establish a list's size from the outside, walk it until
it repeats.

The option ORDER is not a loudness order, so do not infer meaning from it. Which state
is louder came from the operator's ear, corroborated by the 4/4 default carrying 0.667
on beat 1 and nothing else.

**Changing the time signature rewrites these**, because the device re-lays the accent
pattern out for the new bar. Selecting 7/8 (2+2+3) wrote indices 6, 12 and 14 together -
beats 3 and 5, which are exactly the group starts of 2+2+3 once beat 1 is discounted as
already accented. That capture was taken before any of this was understood and read only
as "some STEPSTATEs get rewritten"; it is now an independent confirmation of the mapping
from a direction nobody was looking. **Set the signature before the beats.**

All 13 exist whatever the signature is, which matches 13/4 being the largest beat count
the unit offers. Beats past the current signature are stored and not sounded. How many a
COMPOUND signature sounds - whether 6/8 draws six cells or two - has not been measured.

The catalog types these `empty` while still publishing `steps=4`, which is why they were
long treated as placeholders. Only 16 parameters in the whole catalog are typed `empty`:
these 13, and three `DUMMY` entries carrying no steps at all.

**In the stored preset these params are POSITIONAL.** All 24 arrive with `index` absent,
so position is the index - the same convention as `models[]`. A host WRITE does set
`index`; it is only the device's stored form that omits it. `pyquadcortex.protocol.tempo_params()`
reads them positionally.

**The menu's MODE control emits NO CHANGE EVENT, and is READABLE AND WRITABLE
ANYWAY.** The silence is real, established three times, the last two with instruments
worth trusting - and it is a fact about change notification, not about readability.
The current value rides the ambient tempo stream. Keep this pair together: it is the
clearest example this project has of a trustworthy negative being over-read.

1. **The first attempt.** Toggling to GLOBAL, pressing OK, toggling back to PRESET,
   pressing OK again and then saving the preset produced no `Grid`, `GlobalTempo` or
   `GeneralSettings` traffic. It happened to reach the right answer, but it silently
   dropped 27 of the device's 72 message types and could not tell silence from a dead
   link, so nothing about how it was reached justified believing it.
2. **The re-test.** The same toggle, confirm, toggle back, confirm again sequence,
   watched properly: 70 of the 72 message types decoded, and a liveness heartbeat proving
   the link was up for the whole **420-second** window. No traffic of any kind.
3. **The device-wide sweep of 2026-08-06.** One long listener run over the whole unit,
   banner-marked per section so every message is attributable to the action that caused
   it; the Tempo menu section falls between roughly 765 s and 1395 s of it. Changing MODE
   and changing it back drew silence again. Its action script says only "change MODE to
   its other setting" and "change MODE back", with no OK step written down before the
   `BANK UP` that ends the section, so it repeats the toggle rather than the commit. The
   commit case rests on the two runs above.

All three are sound as far as they go, and all three answer a narrower question than
the one that was asked of them. For eight releases - 0.33.0 through 0.40.0 - this
section said MODE "is NOT on the wire", and the coverage audit and the model design
said "not on the wire at all". Every one of those tests LISTENED, and none of them
ASKED. **The switch was on the wire the whole time.**

Worse than that, and worth stating because it is the sharper lesson: **the value was
in the ambient stream those tests were recording.** The params-shaped `GlobalTempo`
push carries `params[1]`, and it arrives about twice per 14 seconds regardless of
whether anyone touches the switch. A 420-second run should have caught it flipping.
The likeliest reason none did is the noise list - `capture.md`'s own listener recipe
names `GlobalTempoMessage` first among the types to filter out, because it is the
heaviest chatter on the link. So the answer was very probably discarded by the
instrument before it reached the log, three times.

### MODE is the DEVICE tempo block's parameter 1

Found 2026-08-12. `GlobalTempo.params[1]` is the Tempo menu's MODE switch:

| wire | the menu shows |
|---|---|
| `0.0` | PRESET |
| `1.0` | GLOBAL |

Readable by `GlobalTempo{READ}`, and **writable** by a `GlobalTempo{UPDATE,
params{index: 1, param_values}}` - the same `index` / `param_values` shape as every other
tempo write. `QuadCortex.tempo_mode()` and `set_tempo_mode()` are the operations;
`TempoMode` is the enum.

**The unit keeps two tempo blocks and MODE picks which one plays.** The preset's is
`BinaryPreset.tempoProgramData` with 24 parameters; the device's rides in
`GlobalTempo.params` with 25. Neither is touched by the switch. On the unit measured they
disagreed at indices 2 (`LED LIGHT`), 3 (`VOLUME`) and 9 (`ROUTING`) as well as the
tempo itself. The tempo difference was heard and seen - 111 bpm against 120 - so
which block is in effect is not a subtle distinction; the other three were read off
the wire rather than listened to.

How it was established, because a negative that stood for eight releases deserves a
method rather than an assertion: **every field of every message the device answers was
captured in each switch position and diffed**, rather than any field being looked for.
Twelve state types were READ - `GlobalTempo`, `GeneralSettings`, `Mode`, `IOSettings`,
`MasterVolume`, `GlobalEQ`, `ShowGigView`, `Tuner`, `Looper`, `SetlistPosition`, `Scene`,
`PresetDirty` - the RX path was tapped for a 14-second window so everything else the
device pushed was captured too, and both the named fields and any field number the
recovered schema does not know were recorded. Across a PRESET -> GLOBAL -> PRESET cycle
exactly one field moved, and it moved back; the return capture differed from the baseline
in nothing at all.

**What did NOT differ, since a negative is a result too.** `GeneralSettings` was
identical in both positions, and no message anywhere carried a field number the schema
does not know - so the mode is not hiding in the settings bag, which was the leading
hypothesis given that message's 39 gapless field numbers. `BinaryPreset.tempo` (field 10)
was ABSENT in both positions, and the preset's whole `tempoProgramData` block was
identical across the flip, so presence in the preset is not the discriminator either. Of
the device tempo block's 25 parameters, index 24 - which exists there, does not exist in
the preset's 24, and is described nowhere - held `0.0` in both positions and is still
unattributed.

This method is now required rather than optional: ADR-0010 makes a differential
state capture the thing that happens before a control is recorded as having no wire
path. The harness is `tests/hardware/state_snapshot.py`, and
`tests/test_state_snapshot.py` proves offline that it can SEE an unknown field number, a
presence-tracked field set to zero, and a value appearing in only one of two message
shapes - the three ways this particular question could have come back falsely negative
again.

Three independent confirmations, since one flip in one direction is what produced this
project's last false result:

1. **The wire.** `0.0` -> `1.0` -> `0.0`, nothing else moving either way.
2. **The unit's own screen.** A host write moved the menu's switch, and the restore
   moved it back - watched by the owner at the unit.
3. **The tempo in effect.** The unit displayed 111 bpm in PRESET and 120 in GLOBAL, with
   the preset block holding `0.355` and the device block `0.400`. Both are exact on a
   40-240 bpm range, from a direction nobody was looking.

A host write to parameter 1 left the preset's own copy of parameter 1 at `0.0`, measured
before and after, so the scope of the write is known rather than assumed. That matters
more than it looks: ADR-0007 rejected letting a tempo write land in whichever scope the
unit happened to be in, precisely because the device accepts a write it does not
understand and says nothing, which makes a guess and a success indistinguishable.

**It is the catalog's `TYPE`,** which the table above records as written by no control in
the menu. That is true of the PRESET copy, which sat at `0.0` throughout. The DEVICE copy
is the switch.

**Asking `GlobalTempo` was the lead that paid off, and it had been written off.** A single
READ of it once returned only a running clock (`current_beat`, `current_bar`,
`current_tick`) with no parameters, and that was recorded here as a dead end. It was not
one: `GlobalTempo` **alternates two shapes** - one push carrying `metronome_status`, one
carrying the 25 params - and that READ happened to land on the clock. A reader of this
message must match on CONTENT, waiting for a reply that actually carries parameters;
one that takes the first `GlobalTempo` to arrive will usually get the clock and read
nothing, which is exactly how the dead end was manufactured. The params shape was
measured arriving about once every seven seconds, so the wait needs to be generous.

One genuine related dead end, for the record: `MetronomeStatusUpdate`
carries only `is_enabled` and `preroll_enabled`, with no mute or level field at all -
which is why muting means setting `TempoControl.VOLUME` to zero.

**Default scene.** No field on the File message carries it. The device records
whichever scene is ACTIVE at save time, so switching to a scene immediately before
saving makes it the preset's default and `BinaryPreset.default_scene` reads back
accordingly. `save_current_preset(default_scene=...)` does that.

**Lane Output Control** is model `23000`, sitting in `chains[].output_control[]`
rather than `models[]`, present and populated on all four rows. Its parameters are
`0 VOLUME` (dB), `1 PAN` (0.5 is centre), `2 MUTE`, `3 SOLO` - and the wire
carries a fifth, index 4, that the catalog does not document. A keyed write into
`output_control` persists exactly like one into `models` (confirmed: PAN 0.5 ->
0.0 survived a save and read-back).

**Splitter and mixer MUTE is ONE control**, and it is not a catalog parameter of
either model. Muting the splitter on the unit shows the mixer's MUTE already engaged
(confirmed on the unit). The write goes to `Chain.splitBypass`:

```
Grid{UPDATE, preset{chains{row: 0, splitBypass{bypass: true}}}}
```

and the device reports the result in `Chain.mixBypass` - the same write-here,
read-there arrangement as `combined_splitter` versus `splitter[]`. A write addressed to
`mixBypass` does nothing. Established by a four-trial matrix, one write per fresh
recall, across both fields and rows 0 and 2.

Both fields are `repeated SceneBypass`, one entry per scene, but a single write sets
**all eight** - so despite the type, this is not per-scene.

**STOMP footswitch assignments** live in `BinaryPreset.stomp_mode_assignments`, entries
of `{row, column, stomp_index}` where `stomp_index` 0-7 is footswitch A-H. None of the
three fields has presence, so an entry for row 0 / column 0 / footswitch A arrives
looking empty. Factory content populates this: "Darkglass AO900 2" binds its row 0
blocks to A-D and its row 2 blocks to E-H. One footswitch may drive several blocks.

Assigning takes TWO messages, which is what the unit itself sends:

```
Grid{DELETE, preset{stomp_mode_assignments{row, column}}}
Grid{UPDATE, preset{stomp_mode_assignments{row, column, stomp_index}}}
```

The UPDATE alone leaves the previous assignment in place. Three map fields travel with
it and are writable the same way: `stomp_labels`, `single_stomp_labels` (both
`map<uint32, string>`) and `stomp_is_momentary` (`map<uint32, bool>`), all keyed by
footswitch index. The unit clears all three when an assignment is removed, sending one
`Grid{UPDATE}` per map.

**Momentary is real, and the manual does not mention it.** Manual 4.0.0 describes
"momentary" only for the expression toe switch and Looper X, never for a stomp - but the
touchscreen's **Assign footswitch** modal carries a Latching/Momentary toggle, and using
it broadcasts `Grid{UPDATE, preset{stomp_is_momentary{key, value}}}`. The key is the
footswitch index rather than the column, confirmed on a case where the two differ: a
block at column 3 assigned to footswitch E produced `key: 4`. Factory content leaves the
map empty, so a missing entry means latching.

**A momentary write only lands on a footswitch driving exactly ONE block.** The device
enforces this on the wire, and silently - a write aimed at a multi-block switch is
accepted, echoes nothing, and reads back unchanged. The unit greys out its own toggle in
the same case, so this is a device rule rather than a transport wart. Measured within one
preset: two single-block switches took the write and read back `true`, one of them
verified on the unit's screen having never been touched by hand, while a two-block switch
stayed `false` across repeated attempts. There is no error to catch, so check the
assignment count before writing.

**Expression pedal assignment** is a row/column-keyed parameter write like any other,
using three fields on `Param`:

```
Grid{UPDATE, preset{chains{row, models{column, params{index, expression,
                                        expression_min, expression_max}}}}}
```

`expression` is the pedal (1 or 2) and the two floats are the normalized ends of the
sweep; min above max reverses it. Confirmed both as the device's broadcast when a pedal
is assigned on the unit and as a host write surviving save and read-back. Per the
manual, a parameter assigned to a pedal is excluded from scene data.

**A `ParamValue` can carry a `string_value`.** Cab microphone selection uses it - the
unit broadcasts `string_value: "NG_212 DG Neo_Condenser U47"`. A host write of the same
shape persists, so a parameter is not necessarily a number.

**Input Gate Control** is model `28000`, in `chains[].input_control[]`, one per row.
Parameters: `0 NOISE REDUCTION` (%, 0..100), `1 BYPASS` (1.0 bypasses), `2 GAIN
REDUCTION`, `3 INPUT GAIN` (dB, -24..+24, where 0.5 on the wire is 0 dB), and an
undocumented index 4 that reads 0.0 everywhere. It is written with the same row-keyed
shape as `output_control`, and all three controls are confirmed in both directions on
hardware: `NOISE REDUCTION` 0.3 -> 0.6, `BYPASS` 1.0 -> 0.0 on a preset that ships
bypassed and 0.0 -> 1.0 on one that does not, `INPUT GAIN` 0.5 -> 0.7 and -> 0.25.
Per-scene values work too (promote, switch, write): scene C held 0.9 while the other
seven held 0.3.

The gate genuinely differs between factory presets, so reproducing a row faithfully
means reproducing it. Six distinct settings appear across 68 rows, the commonest being
`NOISE REDUCTION` 0.372 with the gate engaged (39 rows) and 0.3 with it bypassed (12).

**`GAIN REDUCTION` is a meter, not a control.** The catalog types it `grMeter`, and
across all 68 rows it only ever holds 0.0 or 0.0011 - the latter being -39.96 dB on
its -40..0 range, i.e. no reduction happening. It is sampled when the preset is
saved, so two saves of the same rig can legitimately differ there, which matters when
diffing presets.

A useful corollary for locating rows: in a preset read back from a recall, a
chain's grid row equals its **index in `chains`** when the explicit `row` field
is absent, and it always is absent on a recalled preset.
`client.input_chain_rows()` relies on this.

For a worked example, factory **"Brit 2203"** (position 0) reads back as four
chains, none carrying an explicit `row`: `chains[0]` has `in_portid = INPUT_1`
with 8 blocks, `chains[2]` has `in_portid = EMPTY` with 6 blocks and is fed
internally, and `chains[1]` and `chains[3]` are empty. So
`input_chain_rows(p, Input.INPUT_1)` returns `[0]`. (An earlier version of this
note cited "chain[0] on row 1", which cannot happen under the rule it was meant to
illustrate; it came from a user preset, not the factory slot named.)

Note also that in a recalled preset the `Model.column` fields come back unset
(all zero), just as `Chain.row` does. Grid position has to be inferred from
ordering: `bypass` arrives as one group per row, each with one `colBypass` entry
per column.

### Grid block move

Dragging a block one column over sends:

```
GridMove{move{from_col: 4, to_col: 5, is_drop: true},
         grid{rows{modelIds: ...} x4}}
```

That is the move plus a **full 4x8 snapshot of grid model IDs**. No row field was
sent for a row-0 move (proto3 default). The library registers this message type
but does not yet wrap it in a client method.

### 7.6d List parameters, and the side-chain source

**A list (comboBox) parameter stores `index / (count - 1)`.** The option names are in
the PRESET, not the catalog (see
[dynamic_steps](#adding-a-block-rewrites-combobox-values-on-rows-you-never-wrote-to)),
and the count is the length of that list. Confirmed in both directions: setting a
side-chain SOURCE to "Input 2" on the unit stored `0.2` out of 16 options - index 3 of
15 - and a host write of 3/17 on a block whose list held 18 options read back as the
same choice.

**The side-chain SOURCE is an ordinary parameter, not the flag it appears to be.**
`Model.sidechain_source_flag` is device-side bookkeeping - it arrives on every recall
as `input_control{sidechain_source_flag: false}` for all four rows, and a host write of
it does nothing. What the unit actually sends when a SOURCE is chosen is a normal
row/column-keyed parameter write. On a "Solid State Comp (S/C)" the catalog names index
6 `SOURCE`, of type `comboBox`.

Its options are the fixed inputs followed by **one entry per block earlier in the
chain**, which is exactly what the manual describes as selectable:

```
Off, Follow Input, Input 1, Input 2, Input 1/2, Return 1, Return 2, Return 1/2,
USB input 5..8, USB input 5/6, USB input 7/8, <blocks ahead of this one>
```

So the list - and therefore the value that selects a given entry - depends on the
preset. `set_param_option()` takes the name and does the arithmetic.

### 7.6c Preset fields that are NOT writable

Tried and refused, so nobody repeats them:

| field | attempted | result |
|---|---|---|
| `BinaryPreset.author_name`, `description` | `Grid` update carrying them | ignored. The device stamps `author_name` itself from the signed-in Cortex Cloud account on every user save, so a factory preset's "Neural DSP" becomes the account name |
| `BinaryPreset.volume`, `pan` | `Grid` update carrying them; `ProductData.gain` on the File save | both ignored. And **the unit has no control for them**: they read 1.0 and 0.5 on every preset examined, factory and user alike, so they are inert fields rather than a gap |
| `BinaryPreset.scene_tempo` | `Grid` update with eight values | ignored, reads back empty. **The unit has no per-scene tempo either** - its Tempo menu has a MODE of global or preset, and nothing scene-specific |
| `Model.sidechain_source_flag` | `Grid` update, row/column keyed | ignored, reads back false - it is bookkeeping. The SOURCE is a `comboBox` PARAMETER, see [7.6d](#76d-list-parameters-and-the-side-chain-source) |
| `BinaryPreset.tags` | three routes, see [7.7](#77-file-operations) | ignored; a saved preset has no tags at all |

The side-chain case is worth a note: the flags are clearly part of how side-chaining is
stored (`side_chain_follow_exists` sits on the preset, and the source list is readable
through `Param.dynamic_steps`), so the write almost certainly travels some other way.

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

**Tags are not preserved by ANY save path, including the unit's own.** Factory presets
carry tags on the wire (six on "California Dream"), and the unit's own Save As produced a
copy with NONE - same result as every host-initiated route. So tags exist only in Neural
DSP's build chain (and presumably Cortex Cloud), every derived preset loses them however it
is saved, and no library can do better. The unit's UI offers no tag editor, which is
consistent. The instrument category is separate and does survive: it lives on the LISTING
entry (`ProductData.instrument`), not the preset body, and the unit's picker maps to the
`Instrument` enum (Guitar 1, Bass 2, Synth 3, Vocal 4, Other 5 - each confirmed by setting
it on-screen and reading back).

**A preset's descriptive `tags` cannot be written, and a saved preset has none.**
`BinaryPreset.tags` (`repeated string`, field 12) carries the descriptors factory
content ships with - 01C reads `['Guitar', 'Clean', 'Crunch']` - and the same list
appears on the listing's `ProductData.tags` (field 7). Neither is reachable. Three
routes were tried on hardware and all three are accepted and leave the list empty:
`ProductData.tags` on the `File` CREATE, a `File` UPDATE carrying them against an
existing entry, and a `Grid` UPDATE carrying `preset.tags`.

The control settles what happens without any of them: a plain save reads back with an
EMPTY tag list, whatever the source preset carried. So nothing stale is inherited - a
preset derived from a guitar preset is simply untagged, not mislabelled - and
`instrument`, which IS settable and is what the unit filters on, is the metadata that
matters. Whether the unit's own screen can add tags to a user preset is not
established.

### 7.6 Per-preset MIDI Out

A preset can send MIDI when a footswitch is pressed, when an expression pedal moves,
and when the preset loads. The preset STORES these in `BinaryPreset.midi_messages`
(on load), `midi_messages_general_v2` (footswitch and expression) and the legacy
`midi_messages_general` - **but a `Grid` update carrying any of those fields is
accepted and ignored.** They are applied by `MIDISettings` (type 8) instead:

```
MIDISettings{UPDATE, general_midi_messages{messages{source: 0,
                        msg{type, channel, param1, param2, param3}}}}
MIDISettings{UPDATE, preset_load_messages{messages{msg{...}}}}
```

A `MIDISettings` READ gets no reply on this firmware, so verify by reading the saved
preset rather than asking the device.

**Sources.** `GeneralMIDIMessage.source` is 0-7 for footswitches A-H and 8-9 for the
two expression pedals. `midi_messages_general_v2` is 10 sources x 12 messages with a
stride of 12, so source N starts at slot `N*12`: writing to sources 0, 1, 2, 7, 8 and 9
landed in slots 0, 12/13, 24, 84, 96 and 108. The device mirrors each source's FIRST
message into the 10-slot legacy `midi_messages_general`.

**Types and what the three params mean.** Each confirmed by entering the message on the
unit and reading the saved preset:

| type | meaning | param1 | param2 | param3 |
|---|---|---|---|---|
| 1 | CC (footswitch) | CC number | value | - |
| 1 | CC (expression source) | CC number | sweep min | sweep max |
| 2 | CC Toggle | CC number | min | max |
| 3 | PC | bank MSB (CC#0) | bank LSB (CC#32) | program |

Note that a plain CC means different things by source: a footswitch sends one value,
while an expression pedal sweeps, so the unit asks for a range even for `type: 1`.

### 7.6b Moving blocks, and creating a branch

**`GridMove` is drivable host-to-device**, which earlier work had left as
"captured only". `GridMoveElement` carries `{from_row, from_col, to_row, to_col,
is_drop}` - all four addressed, so leaving the rows at their default 0 moves within
row 0. Confirmed: row 2 column 1 to column 7 moved that block and left every other
cell on the row, and all of row 0, exactly where it was.

**A cross-row move creates a parallel path**, as the manual says dragging a block from
path A to path B does. Moving row 0 column 6 to row 1 column 6 on factory "Brit 2203" -
a serial preset - left the device reporting `Split(row=0, split_column=0, mix_column=7)`.
The branch and rejoin columns are computed by the DEVICE, not supplied by the caller.

The message also takes an optional `grid` snapshot of every row's model ids, which is
ADVISORY: this library sends only the move.

**A branch can also be created directly**, which is how to place it deliberately:

```
Grid{UPDATE, preset{chains{row, split_control_points{split: 3, mix: 5}}}}
```

Every even row ALREADY carries a splitter, mixer and combined splitter - dormant, with
`split_control_points` reporting `-1`. So there is nothing to create: activating a
branch means setting the columns. Confirmed on "Brit 2203": after the write `splits()`
reported the branch and the splitter, mixer and mute setters all drove it, a `LEVEL TO
B` of 0.25 and a mixer `LEVEL B` of 0.5 both reading back. Writing `-1` to both clears
it again, and clearing row 0 left row 2's branch untouched.

**Expression bypass** writes both halves in one message:
`models{column, bypass_expression{expression, expression_min, expression_max},
expression_bypass_info{type, invert, delay_ms, latch_emulation}}`. Confirmed round
tripping pedal 1, type 1, invert, 250 ms and latch emulation.

**`type` is `STOP = 0`, `SWITCH = 1`, `HEEL_TOE = 2`** - not the manual's listed order.
Established by setting each one deliberately with a scene change fencing them apart, so
the value landed on in each window was unambiguous. It also explains the unit's SWITCH ON
control cycling numerically: from Heel-Toe (2) a press gives Stop (0), then Switch (1),
then Heel-Toe again.

### 7.7b Global device settings

Unlike a preset edit, these change the UNIT: there is nothing to save, and nothing
to recall in order to undo. Each is an ordinary `{action: UPDATE, <field>}` on its own
message type, sparse - only the fields sent are changed - and each is confirmed on
hardware by writing a value, reading it back, and restoring it.

| message | confirmed writable | notes |
|---|---|---|
| `GeneralSettings` | `screen_brightness`, `led_brightness`, `scene_block_bypass` | most of the Device Settings and System menus in one message |
| `IOSettings` | `in_port[].level`, `out_port[].level` | sparse and keyed by `input_port_id` / `output_port_id`; writing one port left the other three byte-identical |
| `GlobalEQ` | `bypassed` | the unit also disables it itself under CPU pressure |
| `Mode` | `mode` | a SLOT index, not a named mode |
| `ShowGigView` | `show` | opens and closes Gig View |

**`GeneralSettings` is where the Device Settings menu lives.** One READ returns
`screen_brightness`, `led_brightness`, `dimmed_led_brightness`, the three
`enable_*_dimmed` flags, `lock_screen_and_volume_knob`, `global_bypass_cab` and
`global_bypass_ir`, `scene_block_bypass`, `stomp_mode_auto_assign`, `hold_timing`,
`swap_tempo_tuner_access`, `gig_view_stomp_access_enabled`,
`enable_dynamic_delay_compensation`, `midi_over_usb`, `midi_channel`,
`ignore_duplicate_pc`, `internal_midi_clock_enabled`, `midi_clock_out`,
`power_button_sensitivity`, `master_volume_assignment{out12, out34, send12,
headphones}`, `looper_stomp_assignments`, `cloud_endpoint` and the
`available_disk_space`/`total_disk_space` pair.

**`looper_stomp_assignments` is the "Reassign Looper X Actions" menu**, and it is GLOBAL
rather than per-preset. Eight entries, **indexed by footswitch A-H**, each value being the
Looper X block's own catalog parameter index - the same 0-7 transport switches
`set_param` drives:

| value | action | value | action |
|---|---|---|---|
| 0 | RECORD OVERDUB | 4 | ONE SHOT |
| 1 | PLAY STOP | 5 | HALF SPEED |
| 2 | UNDO | 6 | PUNCH |
| 3 | DUPLICATE | 7 | REVERSE |

The factory layout is `[3, 4, 5, 6, 0, 1, 7, 2]`, i.e. A DUPLICATE, B ONE SHOT, C HALF
SPEED, D PUNCH IN, E RECORD, F PLAY, G REVERSE, H UNDO. Writable with
`update_settings(looper_stomp_assignments=[...])`: a host swap of the A and H entries was
verified on the unit's own Looper Actions screen. The on-screen menu SWAPS rather than
duplicates, since all eight actions are always assigned to all eight switches.

**The MIDI CC follows the ACTION, not the footswitch.** With A reassigned to UNDO its tile
read CC56, while H holding DUPLICATE read CC49. So the manual's Looper X block of CCs binds
in a fixed order regardless of layout: CC49 DUPLICATE, CC50 ONE SHOT, CC51 HALF SPEED,
CC52 PUNCH IN, CC53 RECORD, CC54 PLAY, CC55 REVERSE, CC56 UNDO.

**`scene_block_bypass` changes what `set_bypass` persists**, so it is worth reading
before concluding a bypass write failed. Its three values are the manual's three
choices: `ALWAYS_OVERWRITE` (the default), `NONSTOMP_OVERWRITE` (footswitch presses in
STOMP mode are not saved) and `NEVER_OVERWRITE` (nothing is saved).

**Which routes each mode actually saves**, driven on the unit and measured on the wire.
A HOST write behaves like a touchscreen edit, not like a footswitch press - the manual
names only the two physical routes, so this was not inferable from it:

| mode | touchscreen | footswitch | host `set_bypass` |
|---|---|---|---|
| `ALWAYS_OVERWRITE` | persists | persists | persists |
| `NONSTOMP_OVERWRITE` | persists | discarded | persists |
| `NEVER_OVERWRITE` | discarded | discarded | discarded |

"Discarded" means the write applies and is then dropped on the next scene change, which
is indistinguishable from a failed write unless you know the setting.

**The unit's own wording**, which differs from the manual's shorthand and is what an owner
actually reads. Body text: *"This feature controls whether changes to the bypass state of a
block in Scene Mode are automatically saved to the active Scene"*. The three options, in
order:

1. *Always overwrite bypass state (default)*
2. *Do not overwrite bypass state when changing bypass state via footswitches in Stomp Mode
   (including Hybrid Stomp Mode) **or MIDI**. Changes made with the touchscreen will be saved.*
3. *Do not overwrite bypass state when changing bypass state by any method.*

Option 2 groups **MIDI with footswitches**, which the manual's summary omits entirely. That
is consistent with what was measured - a USB HID write behaves like the touchscreen, and USB
HID is not MIDI - but the MIDI half is UNTESTED here, since this library has no MIDI path.
Anyone adding one should not assume it inherits the host write's behaviour.

> **`ColBypass.column` has no presence and reads 0 on every entry** - the list is
> POSITIONAL, `preset.bypass[row].colBypass[column]`, exactly like `Chain.row`. Filtering
> by `column` matches nothing and silently yields the first entry or none at all. That
> produced three confident "the write was discarded" results in a row before the constant
> was noticed.

**Do not confuse settings with commands.** `GeneralSettings.power_option` takes
`SHUTDOWN`, `REBOOT`, `STANDBY` or `WAKE_UP`, and `reset_wifi_networks` discards saved
networks. `QuadCortex.update_settings()` refuses both rather than risk an accident.

**Two things to know before trusting a read-back.**

*State pushes can be partial.* A push following an UPDATE may carry only what changed,
so a reader must wait for one that actually contains the field it wants rather than
taking the first message of that type. This library's readers do that.

*A read immediately after a write can return the PREVIOUS value.* These pushes are
eventually consistent in the same way `File` listings are: a scene-bypass write read
back as the old value, and reading again a moment later showed the new one. Allow a
settle, or re-read, before deciding a write was refused.

**What is writable, field by field.** Confirmed by writing, reading back and
restoring: input `level`, `ground_lift` and `input_type`; output `level` and
`ground_lift`; `usb_port.dry_wet`; `midi_port.midi_thru`; and the
`xlr1_2_linked`/`out3_4_linked` pairing flags.

**`input_port_id` is the `Input` enum, not 1/2/3/4.** Combined ids are interleaved, so
Return 1 is **4** and Return 2 is **5** - 3 is INPUT_1_2 and 6 is RETURN_1_2. Anything
addressing input ports by counting jacks writes the wrong entry.

**An input port's `level` is -12..+60 dB: `dB = -12 + 72 * level`.** Solved from four
owner-set trims read simultaneously on screen and on the wire:

| wire | screen | -12 + 72w |
|---|---|---|
| 0.4055555462837219 | +17.2 | +17.200 |
| 0.40042707324028015 | +16.8 | +16.831 |
| 0.5000885725021362 | +24.0 | +24.006 |
| 0.1666666716337204 | 0.0 | 0.000 (the wire value is exactly 1/6) |

It also matches the spec sheet's "MAX INPUT GAIN: +60dB". `input_level_db()` /
`db_to_input_level()` convert. This is the INPUT span only - lane and mixer levels
run -40..+12 dB (see `UNITY_LEVEL` and `lane_level_db()`), and the output span has
not been measured.

A cautionary note on how this was nearly gotten wrong: before the screen readings
existed, the wire values were fitted to the Input Gate block's catalog range (-24..+24 dB)
and produced two suspiciously clean landings - 0.5 sitting at "unity" and 1/6 at exactly
-16. Both were coincidence; the true scale puts them at +24 and 0. Two clean points can
confirm a wrong line. It takes simultaneous readings of both sides to pin a scale.

**Some port fields must travel ALONE.** Output `mute` and input `input_zmode`
(impedance) are both writable, but both are silently dropped when they share a port entry
with another field - and both work when sent by themselves. That matches the unit's own
broadcast for a mute, which carries nothing but `{output_port_id, mute}`.

This misled this project twice: mute was recorded as unwritable, and impedance's failure
was wrongly explained by the manual's note about impedance being disabled for Mic inputs.
Both were the same packing problem. Rather than work out which combinations are safe,
`set_input_port()` and `set_output_port()` now send **one field per message**.

**The USB port packs the same way**, which was found by testing for it rather than by
being surprised again. `usb_port.level`, `hp_select` and `dry_wet` are each writable
alone; sent as `{level, dry_wet}` in one message the level landed and the dry/wet was
silently dropped. `set_usb_port()` splits them too. Note that `usb_port` is a single
submessage rather than a repeated port entry, so this is not about repeated fields - the
rule is about how many fields an I/O update carries, whatever shape it has.

**Tuner.** `ShowTuner{show}` opens and closes it, and `Tuner{input_port_id}` chooses
the input (1 to 2 and back, confirmed). `Tuner.frequency` IS the reference pitch, but
as an **offset in Hz from 440**: 442 on the unit broadcast `frequency: 1.99999809` and
445 broadcast `5`, which is why an earlier write of `442.0` did nothing. Two points on a
line, so the Hz scale is measured rather than assumed.

**Tuner input coverage, swept AND screen-confirmed:** the device accepts ids 1-5 (both
inputs, both returns, INPUT_1_2 combined) plus USB_5 and USB_6, and REFUSES everything
else - notably `RETURN_1_2` (6), so no combined-returns tuning exists and no mode covers
all four inputs. Rejected writes revert to the previous value rather than erroring. The
owner read the unit's own picker: its seven options match the accepted set one for one
(USB_5 displays as "USB input 5"), so for once acceptance and support agree - measured
rather than assumed, after mode value 9.

**Any host write to the Tuner engages an INVISIBLE tuner state.** Field-measured with a
person at the unit: after `Tuner{UPDATE}` carrying either `input_port_id` or `mute`, the
unit behaves as if the tuner is open - nothing on screen says so - and if the stored mute
preference is true, THE OUTPUTS ARE SILENT with no visible cause. The state survived ~100
recalls, 60 saves and every scene switch of a 33-minute build. `ShowTuner{show}` does NOT
create or release it in either direction (a measured no-op on d14e), and no read exposes
it - `Tuner{READ}` reports every field faithfully while the rig is silent.

### The disengage message does not exist

A dedicated capture session went looking for what a physical tuner close broadcasts, so a
host could send it. **It broadcasts nothing at all.** Two captures with a person at the
unit:

* Opening the tuner emits `Tuner{UPDATE, frequency: 0}` - one per OPEN (two open/close
  cycles produced exactly two).
* **Closing it emits nothing.** A capture covering an open, two on-screen MUTE toggles and
  a close contains only three messages: the open announcement and the two toggles.
* The unit's own MUTE control sends `Tuner{UPDATE, mute: <bool>}` - byte-identical to what
  `set_tuner_mute()` sends. There is no hidden field, so the engagement is not caused by
  anything in the message content; the device simply treats any tuner write as "the tuner
  is open".

Replaying the open announcement does not release the state, and neither does
`Tuner{DELETE}` nor `ShowTuner{DELETE}` (both tried, both left the rig silent). So the
lossless release genuinely requires a human, and no amount of protocol work will change
that on this firmware.

**What DOES work from the host: clear the mute preference.** Engagement alone is harmless -
engaged-but-unmuted is fully audible, verified by ear. `restore_audio()` does this, at the
cost of discarding the player's silent-tuning preference. That tradeoff is the honest
state of the art here: a host can guarantee the rig makes sound, or preserve the
preference, but not both.

`Tuner.mute` is writable (the menu's MUTE preference, for silent tuning; it mutes nothing
by itself). `enable_meter` is NOT: it
is sent, stays `false`, and `meter` stays `0.0`. So the needle itself is not readable over
USB, which is the one part of the Tuner the host cannot see.

## Why the preset changed: `RecallPreset.reason`

`RecallPresetMessage` carries a fourth field, `reason` (`RecallPresetReason.Enum`:
OTHER=0, UNDO=1, SAVE=2), populated on real pushes including the connect seed. Measured:
a host recall and a plain READ reply carry **OTHER**; the push a save emits carries
**SAVE**; UNDO is defined but not yet observed (presumably the unit's undo). A state
tracker watching RecallPreset pushes can use it to tell a save's echo from a genuine
preset change. Which value accompanies a USER recall (a footswitch press) is not yet
characterised. `RecallReason` names the values.

## Device loss, and the read/write asymmetry

**A READ raising means the device is gone; a WRITE raising means nothing at all.**
Measured over a 145-second healthy session: 0 read exceptions, 91 write exceptions -
every write "fails" with the QC's status-stage STALL. The two can carry byte-identical
text (the first read error after an unplug said `0xE0005000`, exactly like the benign
stall; only the SECOND read said "Device is disconnected"), so the distinguishing fact is
which call raised, never the message.

The transport turns this into behaviour: a read failure is retried once (a lone blip is
transient); two in a row confirm loss, storing the second - honest - error. Every
transport entry point then raises `DeviceLostError`, blocked waiters are woken to fail
fast instead of timing out, and the RX and keepalive threads wait quietly rather than
spinning on a dead handle.

## Standby, reboot, shutdown - and which of them says goodbye

`PowerOptions.Enum` is SHUTDOWN=0, REBOOT=1, STANDBY=2, WAKE_UP=3, carried as
`GeneralSettings.power_option`. The three departures behave completely differently
(measured):

* **STANDBY ("Be Right Back") does not disconnect.** The USB session stays fully alive -
  2 ms probe answers throughout - and the unit announces it with a partial
  GeneralSettings push carrying ONLY `power_option: 2`, then `power_option: 3` on waking.
  Connecting fresh while it sleeps works normally. A script can be talking to a sleeping
  unit over a perfectly healthy connection; whether writes are honoured there is
  untested.
* **REBOOT and SHUTDOWN send nothing.** Healthy 3 ms probes to the last moment, then
  reads raise. No announcement.

So one field distinguishes "asleep" (it told you) from "gone" (it did not).

**Recovery is two-phase and self-healing.** After a reboot: ~39 s not enumerated, ~9 s
openable-but-silent, then a ~2 s handshake - about 55 s unattended. After a cold boot:
~11.7 s openable-but-silent once enumerated - and a live host-triggered reboot here
measured **~17 s**, so the window varies by a factor of two across sessions. It is why
`connect()` retries the handshake (`handshake_patience`, default 30 s - a 15 s budget was
measured failing): a successful open proves nothing about readiness. This is all much faster than the ~2.5 minutes
troubleshooting.md warns about - that warning concerns the USB-link-death fault, a
different failure.

**State pushes are often PARTIAL - one field set, everything else absent.** Long known
for mode pushes, and confirmed most cleanly by the standby announcements above, which
carry a single field. A reader treating an absent field as "changed to default" corrupts
its cache; merge only what is present. (This is the documented reason `settings()` and
`mode_cycle()` insist on pushes carrying the fields they need.)

**`GlobalTempo` arrives in pairs because it alternates two shapes** - one push carrying
`metronome_status`, one carrying the 25 params. Anyone counting messages or diffing
consecutive pushes should expect the alternation. **A reader of this type must match on
CONTENT**, waiting for the shape it needs: taking the first `GlobalTempo` to arrive is
how a READ for the tempo parameters once came back holding only the clock and was
recorded as a dead end for eight releases. In a 14-second window the params shape
arrived twice, so allow roughly seven seconds per attempt.

## Two parameters called MUTE, with opposite polarities

The single most expensive naming trap found in this project. Both measured on hardware:

| control | wire | `1.0` means |
|---|---|---|
| Lane Output `MUTE` | `output_control` param 2 | **muted** (silences that row) |
| The unit's Tempo-page MUTE | `tempoProgramData` param 4 | **audible** (the click plays) |

The tempo one is inverted against its own on-screen label, and it is the one this library
published backwards for two releases - which left a 36-preset field build with a faint
click running on every preset. `set_metronome_muted()` and `set_metronome_running()` both
exist so callers never have to remember which way the raw value goes; the raw name `"MUTE"`
is refused by the `Tempo` target precisely because honouring it would do the opposite of
what a caller means.

Two further facts from the same trace: **the unit's Tempo page has no start/stop control at
all** - the transport always runs, and MUTE is how a player silences it - and **muting a
lane does not silence the metronome**, which has its own `ROUTING` (param 9) and bypasses
lane outputs. Lane `SOLO` (param 3) remains unmeasured in either direction.

## The Grid echo is a sparse KEYED delta - the opposite of a recall

The long discussion above explains that chains from a RECALL carry no explicit `row`,
which is why wholesale write-back does nothing. The device's EDIT ECHO is the opposite,
and the asymmetry is load-bearing for anyone maintaining a cached preset:

* Writing one parameter produced a Grid push of **23 bytes total**: one chain with `row`
  SET, one model with `column` SET, one param. Fully keyed, no positional guessing - an
  echo can be merged straight into a cached preset.
* Echo latency, measured: **113-116 ms** for a parameter write; **290-420 ms** for a
  block placement (the basis of `set_block(verify=True)`'s window). These two remain the
  slowest and are what the watcher's window is sized against.
* The other write types read far quicker, and the figures need their caveat stated with
  them. Scene label and scene colour land at **about 2 ms** (single-digit ms across four
  runs; one first-write-of-session read 25 ms, on too few samples to call it a warm-up).
  Routing reads **9-19 ms** and global settings **about 2 ms**. The caveat: a *type-only*
  match on these same writes is what produced the discredited 2-11 ms band described in
  `tests/hardware/readme.md`, so a number in this range is only worth as much as the
  predicate behind it. The two scene figures come from predicates matched on the exact
  index and value written, and pinned offline in
  `tests/test_scene_echo_predicates.py`. The routing figure does **not**: its predicate
  accepts any chain carrying the target port rather than the row that was written, so
  treat 9-17 ms as provisional until it is row-keyed. Only the parameter control asserts
  a latency *floor*, so none of these figures would fail the suite if it started timing
  the wrong message.
* Block bypass is still unmeasured: its test skips unless the target block already
  carries a stored bypass entry. See `tests/hardware/test_write_echo.py`.

## `PresetDirty` announces a CHANGE of flag, not an edit

The `Grid` echo above arrives for every edit. `PresetDirty` does not, and the
difference matters to anything watching one to learn about the other.

Measured 2026-08-14 on d14e, as a controlled pair inside one connection. The same
`set_param` write was made twice on the same block, with the RX path tapped for
everything the unit sent in the two seconds after each:

| The flag before the write | What the unit sent |
|---|---|
| `false` | a `Grid` echo **and** a `PresetDirty` carrying `is_dirty: true` |
| `true` | a `Grid` echo, and nothing else at all |

So `PresetDirty` is an announcement about the FLAG, and the flag only changes once
until something clears it. A save clears it (confirmed earlier, watched flipping
across a save). Writing an edited parameter back to the value it had did NOT clear
it within the same connection, which is worth knowing before treating a restore as
an undo.

This corrects "also pushed unsolicited ... on edits", which was true of the edit
that first dirties a preset and read as though it were true of every edit. Anything
waiting for a `PresetDirty` to confirm an edit landed will wait out its timeout on
an already-dirty preset - correctly, since the unit really did not say anything.
The `Grid` echo is the per-edit signal.

## Connect burst, measured

About 3 s of quiet, then the ModelRepo payload as one huge message, then ~400 File
messages streaming at ~1490 reports/s for ~5 s, then every other subscribed state type at
once - including the seed RecallPreset - at about **9 s after connect**, consistent across
several sessions on d14e. (Earlier sessions recorded 10-25 s for lazily-serviced pushes;
keep timeouts generous, but 9 s is the typical seed arrival.)

**`connect()` returns before the burst arrives.** Re-measured 2026-08-12 on d14e with a
transport listener registered before the handshake: `connect()` handed back its client 2.0 s
in, having seen only the `ResetCommsBuffers` echo and one `Version`. (That session read the
`Version` as the unit's own READ. Re-measured 2026-08-27 on d14e it is the unit's `UPDATE`
answering our version announce, carrying `cortex_control_version_valid`; `_hello()` sends no
host `Version` READ, and with none sent the unit asks nothing back. See section 4.) The
ModelRepo landed at 4.9 s, the 399 `File` listings and most settings at 5.1 s, and the seed
`RecallPreset` at 10.1 s - 474 messages of 24 distinct types by 15 s. So a listener attached
to the client `connect()` returns is about 3 s too late for the ModelRepo and 8 s too late
for the current preset, which is why `connect(before_handshake=...)` exists.

Two ambient-traffic facts for anyone instrumenting the link: `GlobalTempo` streams one
pair of messages per BEAT - 1.5 s apart at 40 bpm - so its rate follows the tempo and it
is a poor heartbeat but a decent liveness hint; and a single knob turn on the touchscreen
broadcasts a burst of `Grid` messages (~40 observed for one edit), so edit-time traffic is
far heavier than steady state.

## Reading the LIVE grid, and the active scene

**`RecallPreset{READ, request_id}` answers with the preset as it exists RIGHT NOW** -
unsaved edits included, confirmed by writing a parameter without saving and finding the
value in the reply. The read has no side effects: the unsaved edit survived it and the
active scene did not move. `read_current_preset()` wraps it.

This kills the old inspection cycle (save to a scratch slot, read the slot back), and it
separates two failures that used to be indistinguishable: a write that never applied versus
a write that applied and was later reset.

**`Scene{READ, request_id}` answers with `selected_scene`**, echoing the request id.
Confirmed live by switching scenes between reads. `active_scene()` wraps it.

**`SetlistPosition{READ, request_id}` answers with `folder_key`, `position` and
`is_factory`**, echoing the request id. Measured 2026-08-15 on d14e: answered in 3 ms on
the first attempt. `loaded_position()` wraps it. Note this is the same message type as a
recall and the action is the whole difference between asking which slot is loaded and
loading one, so a READ here must never carry a slot.

The unit also pushes one unsolicited in the connect burst and one on every recall, so a
state tracker can subscribe rather than poll. **The burst's push carries no `request_id`**,
which is why a read has to correlate on the id rather than take the first one that arrives.

**What a recall really pushes**, measured 2026-08-15 across two host recalls, all within
about 120 ms of the request and in this order:

| | |
|---|---|
| `Grid` x 8 to 13 | the new grid, block by block |
| `RecallPreset` | the whole new preset, with `reason` |
| `Scene` | the new active scene |
| `SetlistPosition` | which slot is loaded now |

and **no `PresetDirty` at all**. A recall discards unsaved edits and the unit says nothing
about it, so anything tracking the dirty flag has to re-read it after a recall rather than
wait to be told.

**The connect burst delivers the same four**, at about 10.0 s, inside ten milliseconds, in
the order `RecallPreset`, `SetlistPosition`, `PresetDirty`, `Scene` - and with no `Grid`
pushes, because nothing changed. The seed `RecallPreset` sets `action`, `preset` and
`reason`.

**`read_preset()` RECALLS the slot it reads** - that was already documented - and the
recall **resets the active scene to the preset's default, discards unsaved edits, and
interrupts the audio**.

**Every recall interrupts audio, including a redundant recall of the preset already
loaded.** Measured by ear across four consecutive recalls with a player at the rig: three
of the same factory preset and one genuine change, and all four cut the sound. Only the
DURATION varies - the genuine preset change was noticeably longer than the redundant
recalls, which still cut out audibly. (An earlier session guessed a redundant recall was
free; deliberate listening says otherwise. It was an observation made while not listening
for it.)

The consequence for anything automating a rig somebody is playing: a verify-by-re-reading
loop built on `read_preset()` stutters the audio on EVERY iteration, even when it reads the
same slot and nothing changes. `read_current_preset()` is the side-effect-free read.
The consequence bites hard: a `read_preset` interleaved between `switch_scene` and a
scene-targeted write silently retargets that write at the default scene. This manufactured
a false protocol finding here (see the bypass section below) and very likely produced a
field report's "capture blocks silently ignore bypass". Inspect with
`read_current_preset()` while editing; keep `read_preset()` for stored slots.

## Bypass semantics, measured

A preset stores a full 4x8 bypass table - `bypass[row].colBypass[column]`, POSITIONAL like
`params[].index` (the stored entries leave `row` and `column` unset). Each cell holds
`sceneMode` and eight `sceneBypass` slots. What writes do, all measured on d14e:

* **`sceneMode` false: one global state.** A single-entry bypass write lands on ALL EIGHT
  stored slots at once. (The "unmaintained entries" caveat is too pessimistic - the write
  keeps them consistent.)
* **`sceneMode` true: the write lands on the ACTIVE scene's slot.** `sceneBypass[0]` means
  "the active scene", exactly as documented for `param_values[0]`.
* **Entries beyond `[0]` are ignored** - a full 8-entry map with one slot flipped changed
  nothing. There is no direct write to a non-active scene's slot; switch scenes first
  (which is what `set_bypass(scene=...)` does).
* **`sceneMode` is NOT host-writable.** Sent alone and sent beside a bypass entry, both
  ignored, both directions. Factory content arrives with it set on some blocks; the unit's
  UI presumably sets it. (Note the flag has no field presence, so disabling it could not be
  expressed on the wire even if the device honoured the write.)
* **The bypass table persists for EMPTY cells.** A freshly placed block inherits whatever
  bypass state the preset last stored at that cell - a block placed into a cell whose old
  occupant was bypassed arrives bypassed. Read the cell after placing, not before.

**Neural Capture blocks bypass like any other block ON THE LIVE GRID - but a bypass
written before the preset's FIRST save does not survive that save.** Two field reports and
a reproduction here converge on the full picture. On the live grid, model 14000 needs
nothing special: writes land, read back via the live read, and survive unrelated edits.
But the save that first MATERIALISES a freshly placed capture drops its bypass back to
default, while an ordinary block placed in the same row by the same sequence keeps it.
(Same family as the capture load resetting parameters - though parameters written after
the load DO survive that save; bypass does not.) The sequence that persists, field-verified
on 24 presets and reproduced here: save, recall the stored slot, write the bypass again,
save again - re-saving the same name to the same slot does not trigger `_N` renaming.

The first field report's "captures silently ignore bypass" was still misdiagnosed - the
interleaved `read_preset` resetting the active scene was real and is documented above -
but its instinct that something was capture-specific was right, and the 0.34.0 conclusion
"exactly like any other block" was overbroad: true of the live grid, wrong about the first
save. Verify bypass against the STORED preset (`bypass_state()` on a `read_preset()`
result), not only the live grid.

## Recents and Favorites

`RecentsFavorites` carries both lists, and **the request's `is_favorites` flag chooses
which one you get**:

```
RecentsFavorites{READ, request_id: N}                      -> Recents  (51 entries here)
RecentsFavorites{READ, is_favorites: true, request_id: N}   -> Favorites
```

Measured 10/10 and 0/5: asking with the flag returned Favorites every time, and a plain
read never once returned it.

**The REPLY does not set the flag.** Both lists come back with `is_favorites` absent, so
the two are told apart by what you asked, not by what arrives. The device does echo
`request_id`, so correlate on that.

An empty Favorites list answers with a real, EMPTY push rather than silence, so zero
entries means "none favourited". The first read after connecting is often dropped
entirely - a lazy-delivery trait shared with folder listings - so retry rather than
concluding anything from one timeout.

Entries carry `name`, `folder_key`, `folder_name` and `is_factory`, and can be fed straight
to `find_preset()` / `recall_preset()` / `remove_favorite()` with no translation.

### How this was nearly written off

An earlier version of this document claimed the Favorites list "has no known read path over
USB". That was wrong, and the reason is worth recording: the read was being made with

```python
match=lambda m: bool(m.is_favorites) == want    # rejects every valid reply
```

Since no reply ever sets the flag, the predicate discarded the correct answer and the
symptom was a clean, repeatable timeout - which read exactly like a device that refuses to
answer. Two conclusions were then built on it: that Favorites was unreadable, and that
`favorites()` should be an alias for `recents()`.

That is the **third** time in this project a measuring instrument hid a working feature,
and the three share a shape worth naming:

| The instrument | What it hid |
|---|---|
| Unregistered message types dropped before dispatch | ~27 types, so features looked silent |
| Filtering the device's constant chatter | a dead USB link looked like a quiet one |
| Matching a reply on a field the reply never sets | a readable list looked unreadable |

In all three the device was behaving correctly and the tooling was lying. When a negative
result is clean and repeatable, suspect the instrument before believing the finding - a
flaky failure is usually the device, but a perfectly consistent one is often the observer.

### Writing both lists

**Both lists are maintained one ENTRY at a time.** Sending the whole list back with an
extra item does nothing - which is how an earlier session concluded, also wrongly, that the
list was read-only. Watching the unit recall a preset shows the real idiom, a pair of
single-entry messages:

```
RecentsFavorites{DELETE, items{name, folder_key, folder_name}}   # drop any existing copy
RecentsFavorites{CREATE, items{name, folder_key, folder_name}}   # add it at the head
```

`action` unset is `CREATE` (0), so the second carries no action field on the wire.
Favouriting uses the same pair with the flag set, alongside a `BulkOperation` narrating
`"Adding to Favorites, please wait."` On the unit it is multiselect plus the heart button,
and only presets can be favourited.

**The device echoes the changed entry back** with `is_favorites` set, and that echo is what
`add_favorite()`/`remove_favorite()` wait for. It matters because a mismatched entry is
ignored in silence: the name, `folder_key` and `is_factory` must match the device's record.
"Fuzz This" lives in `/opt/neuraldsp/Factory Library` with `is_factory: true`, and naming it
under My Presets produced no error, no echo and no favourite.

Two things the schema does NOT offer, both checked, so neither is worth hunting for: there
is no per-preset favourite flag anywhere (`ProductData` has 21 fields and none is one), and
no folder carries `FolderInfo.is_favorites` - across 810 folder pushes none was set. So
"Favorites and Recent" is a view over this message rather than a folder to enumerate, and
`local_nc_root`-style magic keys do not apply either: `list_presets()` sends a BARE
`File{READ}` and filters the flood, so that key is what the device REPORTS, not a request
parameter.

## IR import: how far the host can get

Cortex Control imports IRs by drag-and-drop, over this same interface, so a host path
exists. Most of it is now mapped, and it stops at one unknown.

**`FileMessage.type` is a category selector**, which nothing else in this document needed
until now. Attributed by `request_id` (without that, replies from earlier requests
contaminate the counts):

| `type` | what it lists |
|---|---|
| 0 | Presets - 223 folders, the setlists and user folders |
| 1 | **IRs** - `local_ir_root` ("IRs Library"), `2_q` ("My IRs", `is_user_default`), and `/opt/neuraldsp/impulse_responses` (588 plugin assets) |
| 2 | Captures - `local_nc_root` (2063), plus per-product folders |
| 3+ | nothing |

So the user's own IR folder is `2_q`, whose parent is `local_ir_root`. Both were empty on
the unit measured, which is why its IR browser had nothing in it.

**The import request needs `total_bulk_create_count`.** Without it the device does not react
at all - no reply, no error, nothing. With it set, the same message starts a real operation:

```
File{CREATE, type: 1, total_bulk_create_count: 1, folder{key: "2_q", files{name: "..."}},
     ir_payload: <bytes>}

  -> BulkOperation{progress_message: "Importing IRs, please wait.", blocking: true,
                   type: 1, destination_folder{key: "2_q", parent_key: "local_ir_root",
                                               name: "My IRs", is_user_default: true}}
  -> BulkOperation{UPDATE, progress: 1}
  -> BulkOperation{DELETE, finished: true}
```

The device resolves the destination correctly and reports the operation finished. **But no
file appears**, in either folder, on a short or full listing, after 60 s of polling.

**What `ir_payload` should contain is the open question.** Eight encodings were tried, all
producing the same "finished, nothing imported" result: 16- and 24-bit PCM WAV at 48 kHz and
44.1 kHz, 1024 and 4096 samples, a hand-built IEEE-float32 WAV, raw int24 and raw float32
sample arrays, with and without a `.wav` extension on the entry name, and with and without a
sha256 `key` on the entry. The manual's note that uploaded WAVs are "automatically resized to
1024 samples" suggests the conversion happens off-device, so the device may expect a
pre-processed form that none of these matched.

**One hypothesis was tested and refuted**, worth recording because it would have been the
tidy explanation: that outbound FRAGMENTATION was at fault. This was by far the largest host
write ever attempted here (~25 reports), and multi-report host writes had never been
verified. Measured directly by writing strings of growing length into a parameter and reading
them back after a save: 50, 100, 120, 130, 200, 400, 800, 1600 and **3200 characters (26
fragments) all round-tripped exactly**. Outbound fragmentation is sound, so the import
failure is about payload content, not transport.

**A caution while probing this:** the unit's USB link died during a run of repeated
multi-kilobyte import attempts, and only a power cycle brought it back. Causation is not
established - this link has died spontaneously before - but a large unvalidated payload aimed
at a file-import path is a plausible trigger. Space these attempts out, and expect to power
cycle.

**A caution about the IR library first.** `/opt/neuraldsp/impulse_responses` lists 588
entries, but on the unit measured here NONE of them were loadable and the owner had no IRs
available in the IR Loader's browser at all. Every name carries a plugin prefix - 333 `NG_`,
134 `ME_`, 97 `ML_`, 18 `CW_`, 6 `JP_` - so these are assets belonging to purchased desktop
plugins rather than IRs installed on the hardware. That is consistent with a block pointed at
one of them reporting the file missing. So "588 IRs are listable" does not mean 588 IRs are
usable, and testing IR loading needs an IR the owner has actually imported.

The IR Loader blocks are models **29001-29008** (`Single`/`Dual`, mono/stereo, each with a
`Lite` variant), catalog category `IRLoaders`. Two things about their parameter layout are
worth knowing before trying to load an IR from a host.

**Every IR Loader has TWO IR slots**, whatever its name suggests: parameters 0-7 are the
first (`MUTE`, `INVERT`, `IR PATH`, `LEVEL`, `HI PASS`, `LOW PASS`, `PAN`, `DELAY`) and
8-15 repeat them for the second. 16-21 are shared (`ROOM MIX`, `PRE DELAY`, `REV HI PASS`,
`REV LOW PASS`, `SIZE`, `GLOBAL OUTPUT`), and 22 and 23 are an `IR NAME` per slot.

So an IR reference is **two strings, not one**: `IR PATH` (2 or 10) and `IR NAME` (22 or 23).

**`IR PATH` does not take a path. It takes the library entry's `key`.** Read off a block a
human loaded on the unit:

```
params[2]  (IR PATH) = "CIR_eb6d6d347e75f988010a9746580c31c"
params[22] (IR NAME) = "Rex 57 on axis"

library entry       = {key: "CIR_eb6d6d347e75f988010a9746580c31c",
                       name: "Rex 57 on axis", is_readonly: false,
                       date_ms_since_epoch: ...}
```

The key is `CIR_` plus a content id. Both strings come straight from `list_irs()`, and
`set_ir()` writes them - confirmed twice over: by pointing a loader at a DIFFERENT IR from the
host and reading back the library's own key and name byte for byte on both slots, and by then
looking at the unit, which showed that IR loaded with no warning icon.

This differs from a Neural Capture block, which holds ONE string concatenating hash and name.

An earlier session lost time guessing path forms here - bare name, full path, path with
`.wav` - because the parameter is called `IR PATH` and because the only IR listing available
then, `/opt/neuraldsp/impulse_responses`, reports entries with a `name` and **no key**. Those
588 are plugin assets the unit cannot load, so the field that mattered was missing from the
only data on hand. `list_irs()` therefore filters to entries that HAVE a key.

**The device does not validate either string on write.** Any value stores back
byte-identical, including `"NOT AN IR AT ALL zzz"`, so no host-side read distinguishes a good
reference from a broken one.

**The unit does report it, on screen.** A block written with
`IR PATH = /opt/neuraldsp/impulse_responses/CW_212 Cory Wong Cab 1_Condenser 184.wav` and
the matching `IR NAME` came up with a WARNING ICON on the grid, and opening it showed
`"CW_212 Cory Wong Cab 1_Condenser 184 is missing"` - the name from `IR NAME` - while still
allowing its other parameters to be edited. So the firmware does resolve the reference at
load time and does fail loudly; it just fails somewhere a host cannot see.

Two things follow. That full-path-with-extension form is **wrong**, so the path the library
would need is still unknown - and since the IR library lists entries by display name only,
with no hash and no filename, the on-disk name may differ from what is shown. That is why
this is documented rather than wrapped in a method.

Note also that `params[].index` is unset on every entry the device sends, so **position in
the list is the parameter index** - which is why the catalog's index lines up with
`params[i]` directly.

## Global settings: what actually writes

`GeneralSettings` is one wide message covering most of Device Settings. Fifteen fields are
confirmed writable, each sent on its own and restored: the three brightnesses, the three
dimming toggles, `scene_block_bypass`, `stomp_mode_auto_assign`, `swap_tempo_tuner_access`,
`enable_dynamic_delay_compensation`, `gig_view_stomp_access_enabled`, `hold_timing`,
`midi_channel`, `midi_over_usb`, `midi_clock_in_enabled`, `ignore_duplicate_pc` and
`disable_internet_connection_check`.

Three results are worth more than the list:

**`internal_midi_clock_enabled` refuses.** It stays `true` whatever is sent, with
`midi_clock_in_enabled` true or false, so the obvious guess - that the internal clock can
only be switched off when an external one is selected - is wrong. It is simply not
host-writable.

**`dimmed_led_brightness` is capped just below `led_brightness`.** Asking for 100 landed on
25 with `led_brightness` at 28, on 9 with it at 13, and on 56 with it at 59. The dimmed
state has to stay dimmer than the normal one. This first read as a flat refusal, because a
poll demanding the exact value written cannot tell a clamp from a rejection - worth
remembering when testing any bounded field.

**`hold_timing` is an index, not milliseconds.** The unit offers six values - 500 to 1000
ms in 100 ms steps - and the field is the index into them, settled by reading 3 over USB
while the screen showed 800 ms. So `ms = 500 + 100 * hold_timing`. The device does not
validate the field: 0 and 5000 both round-tripped, which is why `set_hold_timing()` takes
milliseconds and checks them rather than passing an index straight through.

Also worth knowing: the MIDI settings the manual lists under a MIDI submenu - channel,
over USB, ignore duplicate PC, clock in - are in `GeneralSettings`, NOT in the
`MIDISettings` message, which carries per-preset MIDI output instead.

**Looper.** `Looper{READ}` reports a full `status`: `state`, `progress`,
`loop_length`, `free_samples`, `armed`, `in_reverse`, `half_speed`, `undo_count`,
`redo_available` and more. Readable; what the `state` numbers mean is not established,
so nothing here drives the transport. The manual notes MIDI CC#48-61 also control the
Looper, which is a second route worth comparing against.

**A SUBMESSAGE write replaces the whole submessage.** Top-level fields are sparse -
sending `screen_brightness` alone changes only that - but a nested submessage is not.
Sending `master_volume_assignment` with only `send12` set left the other three flags
FALSE, which quietly stops the Master Volume knob governing outputs 1/2, 3/4 and the
headphones. So read the current submessage and send it complete.

The same applies to `global_bypass_cab` and `global_bypass_ir`. Repeated fields keyed by
an index are different again: writing one `GlobalEQ.parameters{parameter_index, value}`
left the other 27 alone.

**Values are quantized.** Brightness written as 30 read back as 31, and 60 as 59. Port
levels are stored as float32, so a value must be written at full precision to
round-trip: writing `0.769231` (six decimal places) stored something measurably
different from the `0.769230783` already there, while writing `10/13` reproduced it
exactly.

### 7.7b2 Creating a setlist

The Directory's folders are setlists, and they can be created. The mistake that made an
earlier attempt fail was the path: **setlists sit side by side under
`/media/p4/Presets`**, not nested inside "My Presets".

```
File{CREATE, type: 0, folder{key: "/media/p4/Presets/<name>", name: "<name>",
                             is_factory: false}}
```

Captured from the unit's own "New Setlist" and then confirmed host-to-device: the new
key appears in the folder listing and works anywhere a setlist path does. So the MIDI
documentation's 'User folders' at bank-select LSB 2-12 are folders a player creates,
not fixed setlists.

**Deleting a setlist works**, with `File{DELETE, folder{key, name}}` against the
setlist's own key - the folder leaves the listing, subject to the usual eventual
consistency.

**There is no host-drivable copy, and none is needed.** The unit's duplicate action
sends a `File` CREATE for the destination and then narrates itself through
`BulkOperation` - `"Duplicating, please wait."`, a progress fraction, then `finished` -
and doing the same from the host creates an EMPTY destination. Everything in that window
is the device REPORTING, not a command.

The unit's per-preset copy/paste gives the way in: pasting broadcasts
`File{CREATE, folder{key, files{key, index, name, ...}}}`, which is the same shape as a
Save As pointed at a different folder. And a save DOES accept any folder key (confirmed:
recalling a factory preset and saving it into `/media/p4/Presets/probe` put it there). So
copying a preset is recall-then-save, and duplicating a setlist is that per preset -
which is what `copy_preset()` and `duplicate_setlist()` do. The cost is inherent: each
one recalls the source on the unit.

### 7.7b3 Looper X, master volume, pinning, and the Global EQ

**Looper X state.** `LooperStatus.state` was mapped by watching each transport
control pressed in a known order:

| state | meaning | how it was seen |
|---|---|---|
| 1 | idle / stopped | at rest, and after an UNDO removed the loop |
| 2 | playing | after PLAY/STOP |
| 4 | recording | after RECORD, with `loop_length` streaming upward |
| 5 | armed | after RECORD with no signal present |
| 6 | overdubbing | after OVERDUB during playback; pressing it again returned to 2 |

`3` has never been observed. Overdub was the obvious guess for it and turned out to be 6,
which is a reason not to guess again. Two things a caller should know: with nothing plugged in the
Looper sits in **armed** indefinitely, because RECORD waits for the input to cross the
threshold, and the other controls stay inert until it does. And **REVERSE and HALF SPEED
do not change `state`** - they set `in_reverse` and `half_speed` while playback
continues.

**Master volume IS writable.** `Grid`-style sparseness does not apply here; the whole
write is `MasterVolume{UPDATE, volume}` with a normalized 0..1, and it lands on its own
with no companion field. Confirmed by eye and by ear: a host write of `0.30` took the
overlay to 30 and audibly dropped the level.

This corrects a recorded finding. Earlier work measured the write as "accepted and
changes nothing", and that measurement was a **stale read**, not a device refusal -
`master_volume()` called immediately after the write returns the PREVIOUS value, so a
sequence of write-then-read reports each result one step late. Reconnect, or wait, before
believing a read. The same trap has now produced two wrong conclusions in this project.

`volume` maps to the 0-100 on screen as `round(volume * 100)`: the wire value
0.566115677 displayed 57, not 56. The knob quantizes in steps of exactly 1/121.

**After a host write the physical knob soft-takes-over.** It does nothing until it is
turned past the value the host set, and only then resumes control. That is precisely the
behaviour the manual describes for Cortex Control - "adjusts output level and temporarily
deactivates the hardware wheel" - so Cortex Control is not using some undiscovered route.
It is writing this field.

> **`calibrate: true` is not a flag, it is an action.** Sending it opens the full-screen
> Master Volume Calibration dialog on the unit and waits for the owner to sweep the knob
> min-to-max and tap SAVE. Do not include it to "be safe" alongside a level write; it
> takes over the screen and forces a recalibration. Found the hard way.

**And it is a gain stage of its own, not a rewrite of the port levels.** Across 114
`MasterVolume` pushes while the knob was turned, no `IOSettings` port level changed at
all - the only I/O traffic in that window was `plugged` notifications. So the knob is
applied downstream of the stored levels.

The nearest host-side equivalent is therefore to set the individual OUTPUT levels, which
are writable, and `master_volume_assignment` says which outputs the knob is governing.
That covers the physical outputs but not everything: `hp_port.level` is NOT writable,
refusing a write even when sent alone, so the headphone level cannot be driven this way.

**Pinning a model** works, but not the way the other state types do:

```
PinnedModels{models: [<id>]}          <- note: NO action field
```

An `UPDATE` does nothing, which is why an earlier attempt read as a refusal. And the
write **APPENDS** rather than replacing: pinning something already pinned leaves two
entries for it. `action: DELETE` with an id removes EVERY entry for that id, which is
how a duplicate gets cleaned up.

**Global EQ parameter layout: 5 per band.** `parameters` is a flat list of 28
`{parameter_index, value}` pairs, sparse by index on write. Band N's controls sit at
`(N - 1) * 5 + offset`:

| offset | control | notes |
|---|---|---|
| 0 | GAIN | 0.5 is 0 dB, 0.75 is +6 dB on the manual's -12..+12 dB |
| 1 | FREQUENCY | |
| 2 | Q | |
| 3 | TYPE | a five-option list, so `index / 4` - see below |
| 4 | band ENABLE | 1.0 is ACTIVE, 0.0 bypasses the band - the manual's EQ BAND BYPASS |

Established by changing each of band 1's controls in turn, with a scene change fencing
each so only one index moved per window, and then checked structurally against the whole
list. Laid out five per band the shipped defaults line up exactly as a five-band
parametric EQ should:

```
band 1   gain 0.5   freq 0.142   Q 0.0613   type 1.00 (Lo Shelf)
band 2   gain 0.5   freq 0.207   Q 0.0613   type 0.00 (Peak)
band 3   gain 0.5   freq 0.405   Q 0.0613   type 0.00 (Peak)
band 4   gain 0.5   freq 0.616   Q 0.0613   type 0.00 (Peak)
band 5   gain 0.5   freq 0.729   Q 0.0613   type 0.75 (Hi Shelf)
```

Identical gains and Qs, monotonically rising frequencies, and shelf/peak/peak/peak/shelf
types.

**Indices 25 to 27 are the OUT tab**, the manual's "assign the GLOBAL EQ to one or both
output pairs and adjust its overall output level":

| index | control |
|---|---|
| 25 | the OUT tab's overall level. Its dB mapping is NOT established - the knob was watched moving continuously, so no value could be tied to a reading |
| 26 | assign to OUT 1/2, confirmed by assigning it on the unit |
| 27 | assign to OUT 3/4, by elimination - it is the only index left and was never seen written |

Note that `GlobalEQMessage.bypassed` is the whole EQ's switch and is the INVERSE of the
unit's On/Off control: `bypassed: true` is the EQ off, which is how the observed unit
ships.

**Filter types** are `0.0 Peak, 0.25 Hi pass, 0.5 Lo pass, 0.75 Hi Shelf, 1.0 Lo Shelf`,
mapped by cycling the control through every shape on the unit and confirmed independently
by those defaults.

Note that a `parameters` block carrying no `parameter_index` IS index 0, and one carrying
no `value` IS 0.0 - both fields are plain scalars, so their zeros are not serialized. Two
writes were missed on a first pass for exactly that reason.

**A HYBRID slot is a composite value in `available_modes`, and it IS drivable.** Merging two
modes on the unit produced

```
Mode{UPDATE, available_modes{modes: 7, modes: 1}}
```

Two slots - the hybrid as `7`, Scene as `1` - and cycling modes alternated `mode: 7` and
`mode: 1`, matching what the screen showed. Sending `available_modes{7, 1}` from the host
builds the same thing.

**A HYBRID gives each footswitch ROW its own mode** - A-D and E-H - so the composite
encodes an ORDERED pair. All six are mapped, read one at a time off the unit's own MODE
indicator, which names the top row first:

| value | A-D (top) | E-H (bottom) |
|---|---|---|
| 3 | Preset | Scene |
| 4 | Preset | Stomp |
| 5 | Scene | Preset |
| 6 | Scene | Stomp |
| 7 | Stomp | Preset |
| 8 | Stomp | Scene |

The six ordered pairs in lexicographic order over Preset=0, Scene=1, Stomp=2. So **4 and 7
are the same pairing in opposite arrangements**, which is what the manual's "tap the right
edge of the HYBRID slot to swap the Modes rows" produces - and it retro-explains the original
capture, where merging Preset with Stomp reported `7`, i.e. Stomp on top.

Build them with `hybrid_mode(top, bottom)`; name any value with `describe_mode()`.

**The range the device accepts is wider than the range that works.** Writing `[N, 1]` for N in
3..15 and polling until the cycle settled: 0-9 survive and 10+ are dropped. But **9 is
broken** - its indicator reads "<blank> + Scene" and the footswitches stop responding
altogether. It is a genuine trap, since the device gives no hint: the write is accepted, the
value reads back, and the unit is left unusable until the mode is changed. `set_mode_cycle()`
refuses it.

This is also a caution about the accept/reject method itself. Acceptance was measured
mechanically and reported seven composites; only reading the screen showed that one of them
does not work. A device that stores a value is not a device that supports it.

**Two structural limits**, both measured: a cycle holds at most ONE composite (`[3, 4, 5]`
comes back as `[3]`), and a composite cannot be the only slot (`[7]` alone is refused and the
unit reverts to its default). Pair a hybrid with a base mode.

**Mode pushes are frequently PARTIAL** - a mode switch broadcasts `mode` alone, with no
`available_modes` - so a read that accepts any push mentioning `mode` can hand back an empty
cycle for a perfectly configured unit. This produced two contradictory accept/reject tables
before it was noticed. `mode_cycle()` waits for a push that actually contains the cycle; see
the partial-push warning at the top of this document.


**It only appears once the menu is confirmed.** An earlier session merged and un-merged
WITHOUT pressing OK, and the merge broadcast nothing - which had been recorded here as the
pairing not being on the wire at all. It is; the state simply is not published until
commit. Note this does NOT generalise: the Tempo menu's MODE control stayed silent through
an OK press and a save, and yet answers a READ - "publishes on commit" and "readable" are
independent properties, and neither predicts the other.

What the composite value encodes is unknown. 7 was Preset+Stomp, by elimination since
Scene was the slot left standing. Other pairings have not been observed, so read the value
back after making the pairing once on the unit.

### 7.7b4 Neural Capture

**Creating** a capture is not driven by this library, and there is a trap worth knowing
about first: the unit hands its capture flow to a connected HOST.

Choosing "New Neural Capture" on the unit broadcasts
`NeuralCapture{try_to_show_dialog: true}` and then waits. The host is expected to answer
`NeuralCapture{show_dialog: true}` and present the UI itself - which is what Cortex
Control's Neural Capture does. Consequences, both observed:

- **Staying silent is not neutral.** With a host connected and not answering, the tap does
  nothing at all and the unit returns to the grid. Simply being connected suppresses
  on-device capture; disconnect to use the unit's own wizard.
- **Answering without a UI is worse.** Replying `show_dialog: true` from a library that
  draws nothing put the device into the flow - it reported `state: 1` and prepared its A/B
  model, `model_ab` carrying a cab - with no interface anywhere.

`NeuralCapture` also carries `show_dialog_fail_reason`, `state`, `progress`,
`toggle_ab_model`, `model_ab_bypass`, `save_info` and `error_id`.

The engine itself is exposed as three internal models in the catalog's Neural Capture
Internal category, whose parameters are the controls and whose meters are the telemetry:

| model | parameters |
|---|---|
| `NC_Recorder` | Progress, Bulk Delay and Sanity Check meters |
| `NC_Trainer` | START TRAINING, SET CONDUCTOR, SET NODE, CANCEL TRAINING, SET SEED; Progress and Loss meters |
| `NC_Refiner` | START AUTO REFINE, START MANUAL REFINE, SET LATENCY, SET AB, OUTPUT GAIN, EXPORT MODEL, IMPORT MODEL |

**Using** a capture that already exists needs none of that - see the `file_name` parameter
above.

### 7.7c The folder tree, and what else is enumerable

A single `File` READ makes the device enumerate far more than the two setlists. On the
observed unit **399 folders** arrive over roughly fifteen seconds:

| key | name | contents |
|---|---|---|
| `/media/p4/Presets/My Presets` | My Presets | 256 slots, the only USER setlist present |
| `/opt/neuraldsp/Factory Library` | Factory Library | 256 slots, all occupied |
| `local_nc_root` | Captures Library | **2062** factory captures |
| `NNN_f` (176 of them) | an amp name | that amp's captures, e.g. `106_f` is "Darkglass VMT" with three |
| `/opt/neuraldsp/impulse_responses` | - | 588 factory IRs |
| `/opt/neuraldsp/Plugins/<plugin>/Artists/<artist>` | artist name | that plugin's artist presets |
| `local_ir_root`, `cloud-0-1`, `cloud-2-1` | - | empty here |

**Every one of those keys works with `list_presets`**, confirmed for `106_f` and for a
plugin artist folder - so a caller is not limited to the two setlists, and
`list_folders()` is how to discover what is addressable.

Note what this does NOT show: the MIDI documentation describes bank select LSB values
2-12 as 'User' folders, but only one user setlist exists on this unit, so those are
folders a player can create rather than fixed setlists. A `File` CREATE naming a new
folder key was accepted and created nothing, so how a folder is made is unresolved.

`RecentsFavorites` reports the unit's Favorites and Recents as `items{name, folder_key,
folder_name}` - 49 entries here - which can be fed straight back into a recall.

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

Named forms of all of the above live in `pyquadcortex/protocol/enums.py` (`Input`,
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
| version read | `Version{action: READ}` | read-back | serial and firmware returned. TWO messages come back: the `UPDATE` with fifteen fields, then the device's own `Version{READ}` 0.5-0.8 ms later. `version()` accepts only a `Version` carrying `device_serial_number` or `app_fw_version`, so the second one is never returned as the answer; five back-to-back calls after that change all returned the full reply (2026-09-03) - see "A `Version` READ is answered twice" |
| `recall_preset` / `read_preset` | `SetlistPosition{UPDATE, folder_key, position, is_factory, request_id}` then a `RecallPreset` push | read-back | the push echoes the recall's `request_id` |
| `read_current_preset` / `read_current_preset_push` | `RecallPreset{READ, request_id}` | read-back | the live grid, no side effects. The push variant hands back the whole reply, which carries `reason` beside the preset |
| `loaded_position` | `SetlistPosition{READ, request_id}` | read-back | which slot is loaded; 3 ms measured. A READ names no slot - an UPDATE that did would recall it |
| `list_presets` | `File{action: READ}` then `File{folder{files[] = ProductData}}` | read-back | factory listing gzipped; 256 slots; listings lag a few seconds after a `File` mutation |
| `switch_scene` | `Scene{UPDATE, selected_scene}` | on-unit | zero-based |
| `set_chain_input` / `reroute_grid_input` | `Grid{UPDATE, preset{chains{row, in_portid}}}` | read-back + on-unit | row-keyed; the only shape that persists input routing |
| `set_param` | `Grid{UPDATE, preset{chains{row, models{column, params{index, param_values{float_value}}}}}}` | read-back | value round-trips 0.0 to 1.0; param index is positional; not every index is a visible knob. Per-scene values via promote + switch_scene + write |
| `set_bypass` | `Grid{UPDATE, preset{bypass{row, colBypass{column, sceneBypass[scene]{bypass}}}}}` | on-unit | block greyed out on the unit |
| `set_scene_label` / `set_scene_color` | `SceneLabel` / `SceneColor{UPDATE, index, label/color}` | read-back | color is ARGB uint32; exact round-trip |
| `copy_scene` | `SceneCopy{UPDATE, from_index, to_index, is_swap}` | read-back + on-unit | fully confirmed: `from_index` (copying B onto D produced B, not A), `is_swap` (scenes exchanged), and that the scene LABEL and COLOUR both travel with the state. Cortex Control cannot copy a scene, so the shape came from the device's own broadcast when copying on the unit |
| `save_current_preset` | `File{CREATE, folder{key, files{index, name, instrument}}}` | read-back | snapshots the GRID; `preset_payload` is IGNORED for CREATE |
| `delete_preset` | `File{DELETE, folder{files{key: "<setlist>/<name>.pb"}}}` | read-back | works, but asynchronous: a listing within about 2 s is stale, about 5 s is reliable |
| `move_preset` | `File{MOVE, folder{files{key}}, to_folder{files{index}}}` | read-back | source by file path, destination by index; asynchronous like delete |
| `set_param_scene_mode` | `Grid{UPDATE, ..., params{index, scene_mode}}` (flag ALONE) | read-back | promotes a parameter to scene-following; a value in the same message voids it |
| `set_chain_output` | `Grid{UPDATE, preset{chains{row, out_portid}}}` | read-back | required for a new chain: the device never assigns an output on its own |
| `set_param(Mixer(row), ...)` | `Grid{UPDATE, preset{chains{row, mixer{params{index, param_values}}}}}` | read-back | supports per-scene; how factory presets build scenes |
| `disconnect` | `Connection{connected: false}` | sent, unverified | matches Cortex Control's captured behaviour on quit; no device state to read back, and no observable effect measured either way |
| `set_param(Splitter(row), ...)` | `Grid{UPDATE, preset{chains{row, combined_splitter{params{index, param_values}}}}}` | read-back | writes `combined_splitter`, NOT `splitter[]`, which is a read-only view; indices follow the unified model 10004 |
| `splits` | reads `Chain.split_control_points` | read-back | branch and rejoin columns. `split == -1` means serial; `mix == -1` with `split >= 0` is a branch that never rejoins (`Split.rejoins`). Only rows 0 and 2 can carry one |
| `set_param(Tempo(), ...)` | `Grid{UPDATE, preset{tempoProgramData{params{index, param_values}}}}` | read-back | per-preset tempo, LED and metronome level; NOT row-keyed yet applied |
| `tempo_mode` / `set_tempo_mode` | `GlobalTempo{READ}`, and `GlobalTempo{UPDATE, params{index: 1, param_values}}` | read-back + on-unit | the Tempo menu's MODE switch: `0.0` PRESET, `1.0` GLOBAL. A DEVICE setting, not a preset one - nothing to save. No change event is emitted when the switch moves, but the value rides the ambient params push; the reader must match on a reply carrying PARAMETERS, since `GlobalTempo` alternates a clock shape with a params shape |
| `set_param(LaneOutput(row), ...)` | `Grid{UPDATE, preset{chains{row, output_control{hash: 23000, params{index, param_values}}}}}` | read-back | VOLUME/PAN/MUTE/SOLO per row; PAN 0.5 -> 0.0 survived save and read-back. `Db(...)` takes dB for VOLUME, over the -40..+12 span the catalog names as `MIN_MIXER_DB` |
| `move_block` | `GridMove{move{from_row, from_col, to_row, to_col, is_drop}}` | read-back | drivable host-to-device; a cross-row move makes the device create a branch |
| `set_split` / `clear_split` | `Grid{UPDATE, preset{chains{row, split_control_points{split, mix}}}}` | read-back | activates or clears a row's branch; the splitter itself always exists |
| `set_expression_bypass` | `Grid{UPDATE, ..., models{bypass_expression, expression_bypass_info}}` | read-back + on-unit | `type` is `ExpressionSwitchMode`, all three confirmed: STOP 0, SWITCH 1, HEEL_TOE 2. The mode decides which other controls exist - SWITCH greys out the delay, HEEL_TOE greys out latch emulation |
| `list_folders` | `File{READ}`, collecting every push | read-back | 399 folders on the observed unit, including a 2062-entry Captures Library |
| `favorites` | `RecentsFavorites{READ}` | read-back | read-only |
| `tuner` / `show_tuner` / `set_tuner_input` | `Tuner{READ}` / `ShowTuner{UPDATE, show}` / `Tuner{UPDATE, input_port_id}` | read-back | `frequency` is a readout, not the reference pitch |
| `looper` | `Looper{READ}` | read-back | full status; transport not driven |
| `set_input_port` / `set_output_port` / `set_usb_port` / `set_midi_thru` / `set_output_pairing` | `IOSettings{UPDATE, settings{...}}` | read-back | sparse and port-keyed. Output `mute` and input impedance did NOT take when packed with another field. Levels take a typed value: the input gain converts `Db` over a measured -12..+60 dB, the output and USB levels take `Encoded` only because no span is known |
| `set_param_option` | `Grid{UPDATE, ..., params{index, param_values{float_value}}}` | read-back + on-unit | picks a list parameter's option by name; the value is `index / (count - 1)` |
| `set_output_mute` | `IOSettings{UPDATE, settings{out_port{output_port_id, mute}}}` | read-back + on-unit | must travel ALONE; dropped if another field shares the port entry |
| `set_tuner_reference` | `Tuner{UPDATE, frequency}` | read-back + on-unit | an OFFSET in Hz from 440, taken as `Hertz`; the wire carries the Hz itself, so there is no `Encoded` line here |
| `captures` / `set_capture` | `File{READ}` on `local_nc_root`; then `Grid{UPDATE, ..., params{index: 5, param_values{string_value}}}` | read-back + on-unit | a capture block's model id is the block TYPE; `file_name` = hash + display name selects the capture |
| `master_volume` / `set_master_volume` | `MasterVolume{READ}` / `{UPDATE, volume}` | read-back + on-unit + by ear | WRITABLE. The recorded "a write is ignored" was a STALE READ - a read straight after a write returns the previous value. Takes `Encoded`: the wire is 0..1 and the screen shows 0-100, but what that number IS has not been established, so no dB is offered. After a host write the physical knob soft-takes-over |
| `pin_model` / `unpin_model` / `pinned_models` | `PinnedModels{models}` with NO action / `{DELETE, models}` | read-back + on-unit | pinning APPENDS and can duplicate; DELETE removes every entry for an id |
| `delete_setlist` | `File{DELETE, folder{key, name}}` | read-back | removes the setlist and its contents |
| `create_setlist` | `File{CREATE, folder{key: "/media/p4/Presets/<name>", name}}` | read-back + on-unit | setlists are siblings under the presets root, not children of My Presets |
| `set_split_mute` | `Grid{UPDATE, preset{chains{row, splitBypass{bypass}}}}` | read-back | the single splitter/mixer MUTE; reported back in `mixBypass`, and one write sets all eight scenes |
| `set_stomp_assignment` | `Grid{DELETE, stomp_mode_assignments{row, column}}` then `Grid{UPDATE, ...{stomp_index}}` | read-back + on-unit | the unit's own two-message sequence; an UPDATE alone leaves the old assignment |
| `set_stomp_momentary` | `Grid{UPDATE, preset{stomp_is_momentary{key, value}}}` | read-back + on-unit | keyed by footswitch, not column. **Only lands on a switch driving exactly one block** - the device refuses multi-block switches silently, as its own toggle does |
| `set_stomp_label` | `Grid{UPDATE, preset{stomp_labels` or `single_stomp_labels{key, value}}}` | read-back + on-unit | `single_stomp_labels` is the one the unit writes when the switch drives a single block; it clears both on unassign |
| `set_expression` | `Grid{UPDATE, preset{chains{row, models{column, params{index, expression, expression_min, expression_max}}}}}` | read-back + on-unit | pedal 1 or 2 with a normalized sweep range |
| `clear_expression` | same shape with `expression: 0` and the sweep back to `0.0..1.0` | read-back | unassigns a block parameter; confirmed clearing a pedal-2 assignment |
| `expression_assignments(preset)` | reads `params{expression, expression_min, expression_max}` across `models`, `output_control`, `input_control`, `mixer` and `combined_splitter` | fixture | the read-back side of `set_expression`, which had none. `params[].index` is proto3-optional so it HAS presence, and the device never sends it - absent on all 576 params in every committed fixture - so the POSITION is the index. `expression: 0` is unassigned, being what `clear_expression` writes, and IS sent. NOT walked, neither established: `chain.splitter` (a different model from `combined_splitter` - 10002 with 3 params against 10004 with 8) and `Tempo`, which has no row and sits outside `chains` |
| `set_expression(LaneOutput(row), ...)` | `Grid{UPDATE, preset{chains{row, output_control{hash: 23000, params{index, expression, expression_min, expression_max}}}}}` | read-back + on-unit + by ear | `set_lane_output`'s shape with the three expression fields. **VOLUME and PAN only** - the device SILENTLY DROPS the same message aimed at MUTE or SOLO, in both directions, so those refuse rather than lying. `scene_mode` is NOT sent: the unit does not set it when it assigns a pedal |
| `clear_expression(LaneOutput(row), ...)` | same shape with `expression: 0` and the sweep back to `0.0..1.0` | read-back | unassigns; refuses MUTE and SOLO for the same reason |
| `set_midi_out` / `set_preset_load_midi_out` | `MIDISettings{UPDATE, general_midi_messages` or `preset_load_messages{messages{source, msg}}}` | read-back | per-preset MIDI Out. A `Grid` update carrying the preset's own midi fields does nothing |
| `set_param(..., "a string")` | `Grid{UPDATE, ..., params{index, param_values{string_value}}}` | read-back + on-unit | string-valued parameters, e.g. cab microphone selection |
| `param_options` | reads `Param.dynamic_steps` | read-back | the option names of a list parameter, which the catalog does not carry |
| `set_master_volume_assignment` | `GeneralSettings{UPDATE, master_volume_assignment{...}}` | read-back | which outputs the knob governs. Read-merge-write, because a submessage is replaced wholesale |
| `set_master_volume` | `MasterVolume{UPDATE, volume}` | read-back + on-unit + by ear | normalized 0..1, displayed as `round(v * 100)`. Travels alone. The earlier "accepted and ignored" was a stale read. Never add `calibrate` - it opens the calibration dialog |
| `set_global_bypass` | `GeneralSettings{UPDATE, global_bypass_cab` / `_ir{row1..row4}}` | read-back | global Cab / IR bypass per row |
| `set_global_eq_band` | `GlobalEQ{UPDATE, parameters{parameter_index, value}}` | read-back | sparse by index; which index is which band control is unestablished, so it takes `Encoded` only - `set_global_eq` knows the offsets and takes `Db` for a band's GAIN |
| `set_mode_cycle` | `Mode{UPDATE, available_modes{modes}}` | read-back | the mode cycle order; the whole list is replaced |
| `settings` / `update_settings` | `GeneralSettings{READ}` / `{UPDATE, <fields>}` | read-back | the Device Settings and System menus; sparse. `power_option` and `reset_wifi_networks` are refused as commands rather than settings |
| `set_scene_bypass_behavior` | `GeneralSettings{UPDATE, scene_block_bypass}` | read-back | global, and it decides what `set_bypass` persists |
| `io_settings` / `set_input_level` / `set_output_level` | `IOSettings{READ}` / `{UPDATE, settings{in_port` or `out_port{port_id, level}}}` | read-back | sparse and port-keyed; also reports impedance, type, ground lift and `plugged` |
| `global_eq` / `set_global_eq_bypassed` | `GlobalEQ{READ}` / `{UPDATE, bypassed}` | read-back | five bands reported as 28 parameters |
| `mode` / `set_mode` | `Mode{READ}` / `{UPDATE, mode}` | read-back | a slot index; `available_modes` lists the configured slots |
| host undo / redo (no client method yet; PR #42 adds `undo()` / `redo()`) | `UndoRedo{UPDATE, undo: true}` / `{redo: true}` | preset read-back | measured 2026-09-03 on Quad Cortex, CorOS 4.0.1 / d14e: `08 01 28 01` reversed a bypass edit on a scratch preset and `08 01 30 01` reapplied it, the edit restored and the preset saved clean afterwards. Reported by a contributor on 4.1.0. Whether the recall that follows carries `reason: UNDO` was not captured |
| `preset_dirty` | `PresetDirty{READ}` | request_id echo | answers as UPDATE in 2-11 ms (two hardware sessions); `is_dirty` has no presence, absent IS false; flips false across a save; also pushed unsolicited, but only when the flag CHANGES - see below |
| `set_gig_view` | `ShowGigView{UPDATE, show}` | read-back + on-unit | `show` has no presence |
| `tap_screen` | two `RemoteControl{UPDATE, mouse{x, y, RELEASE/PRESS}}` messages | on-unit + screenshot | raw 800 x 480 pixels; runtime enum semantics are inverted |
| `capture_screen` | `RemoteControl{READ, screenshot:{}}` | async read-back | returns the PNG from the following uncorrelated UPDATE |
| `set_param(LaneInput(row), ...)` | `Grid{UPDATE, preset{chains{row, input_control{hash: 28000, params{index, param_values}}}}}` | read-back | the per-row noise gate; NOISE REDUCTION, BYPASS and INPUT GAIN all confirmed in both directions, per-scene included. GAIN REDUCTION is a meter (`grMeter`), not a control |
| `free_rows` | reads `models[]` + `Chain.split_control_points` | read-back | rows available for an independent chain: excludes the lane row of a branch, which is spoken for even when empty |
| `wait_for_listing` | repeated `File{READ}` | read-back | polls until a listing settles; not a device operation of its own |
| `write_preset` | `Grid{UPDATE, preset}` | read-back | low-level primitive; applies ONLY row/column-keyed elements. A full recalled preset written back does NOTHING. Use the keyed wrappers |
| `set_block` | `Grid{UPDATE, preset{chains{row, models{column, hash}}}}` | read-back + on-unit | creates a block in an empty cell, replaces one in an occupied cell; the same shape the device broadcasts when a block is added on the unit. A placement can be REFUSED silently, for DSP capacity OR a port conflict; verified by default against the echo AND a read-back |
| `remove_block` | `Grid{action: DELETE, preset{chains{row, models{column, hash: 0}}}}` | read-back + on-unit | the ACTION marks the removal; an UPDATE carrying `hash: 0` is transmitted but ignored |
| `catalog` | `ModelRepo{READ}` then `ModelRepo{model_repo_payload}` | read-back | payload is gzip(tar(ModelRepo.xml)): the unit's full block catalog |
| `GridMove` | `GridMove{move{from_col, to_col, is_drop}, grid{rows{modelIds} x4}}` | captured only | observed in a Cortex Control session; no client method, not driven host-to-device by this library. Its `grid` snapshot is ADVISORY - replaying it with a cell zeroed does NOT delete a block, and the device echoes back only the `move` element |

### Every reading taken off the screen, and what it proves

These are the measurements behind the firmware constants above. They were taken
before anyone noticed the catalog names its own bounds, so at the time they were
the ONLY source for these spans; now they are the evidence that the catalog's
numbers are right, and `tests/test_scales.py` holds each one to the display's own
precision.

Each was read against the unit's screen at three or more well-separated points
INCLUDING BOTH ENDS, because two close points cannot distinguish spans - that is
how the lane levels shipped `-100..+30` for two releases.

| family | span | evidence |
|---|---|---|
| Lane / mixer / splitter LEVEL | `dB = -40 + 52 * wire` | lane VOLUME: -3.1 at 0.71, +12.0 at 1.0, -39.5 at 0.01. Splitter `LEVEL TO B`: -3.1 at 0.71, +12.0 at 1.0. `MIXER LEVEL`: -24.4 at 0.30, +12.0 at 1.0. Mixer `LEVEL A`: -24.4 at 0.30, +12.0 at 1.0. Mixer `LEVEL B`: -3.1 at 0.71, +12.0 at 1.0. Splitter `LEVEL TO A` agrees at 0.30 and reads OFF at 0.0 |
| Block EQ band GAIN (`4000`, `4001`, `4004`) | `dB = -12 + 24 * wire` | Parametric-8 at four points: -12.0 at 0.0, -9.6 at 0.10, 0.0 at 0.50, +12.0 at 1.00. Parametric-3 and Output Equalizer measured at both ends each |
| FX loop SEND side (`Send.LEVEL`, `Send.THRU`, `FX Loop.SEND LEV`) | `dB = -40 + 40 * wire` | five points, every one exact: -39.6 at 0.01, -36.0 at 0.10, -20.0 at 0.50, -10.0 at 0.75, 0.0 at 1.00. Tops out at UNITY - a send cannot boost |
| FX loop RETURN side (`Return.LEVEL`, `FX Loop.RET LEV`) | `dB = -40 + 52 * wire` | -39.5 at 0.01, -34.8 at 0.10, -14.0 at 0.50, +12.0 at 1.00. **The same scale as the lane, mixer and splitter levels** |
| Per-preset TEMPO | `bpm = 40 + 200 * wire` | 59 at 0.095, 111 at 0.355, 120 at 0.400 |
| Input port gain | `dB = -12 + 72 * wire` | four owner trims read on screen against the wire |

**Reading a scene-following parameter: `param_values[N]` is SCENE N, not the
active one.** The device honours a WRITE's `param_values[0]` against whichever
scene is active, so the two halves are not symmetric, and a reader that takes
`[0]` is reading scene A. On a unit sitting in scene E that looks exactly like a
write the device refused - four of them, in the case that produced this note.
Check `Param.scene_mode` and index by the scene you mean;
`protocol.param_state(preset, cell, index)` hands back all eight with the flag.

**Wire 0.0 is an OFF detent, not the bottom of the dB scale.** The splitter's
`LEVEL TO A` displays "OFF" there, exactly as the lane VOLUME does, so this
belongs to the level family rather than to one control. For silence write
`Encoded(0.0)`; `Db(-40)` is the scale's floor and a different thing.

**A band's TYPE decides whether its GAIN means anything.** Lo Pass and Hi Pass
disable the control, and a gain written to such a band is stored and ignored by
the unit. Nothing in the wire says so, so `Db(...)` will happily convert dB into
a parameter that does nothing.

**`N BYPASS = 1` means the band is ON** for the block EQs, the same inverted
polarity already recorded for the Global EQ. A disabled band displays `0.0 dB`
whatever it stores, which makes a write look like it never landed.

**Cab LEVEL is TAPERED**, and it is the reason "three well-separated points" is
not a sufficient standard. Twelve points on a `212 Darkglass Neo (M)`, per mic:

| wire | 0.00 | 0.01 | 0.10 | 0.25 | 0.35 | 0.50 | 0.75 | 0.95 | 1.00 |
|---|---|---|---|---|---|---|---|---|---|
| screen | OFF | -21.8 | -11.1 | -5.2 | -2.8 | **0.0** | +3.4 | +5.5 | **+6.0** |

**`dB = -39.96 + 45.96 * wire^0.202`**, worst error 0.034 dB across all twelve -
inside the display's own 0.1 dB rounding. Wire 0.15 and 0.60 were PREDICTED from
the law and then found, before being fitted to.

The three obvious laws all fail, which is the cautionary part. `20*log10(2v)`
matches the top three points beautifully and is **12 dB out at wire 0.01**;
linear-in-amplitude matches 0.25 exactly and is 3.1 dB out at 0.01;
linear-in-dB would need +12 at full travel. This parameter was written up as
having no closed form on the strength of eight points and three failed laws. It
took four more points and a taper exponent. **A control can be perfectly regular
and still defeat every law you happen to try first.**

The constants are probably the design intent -40 -> +6 with a fifth-root taper;
`-40 + 46 * wire^0.2` fits to 0.17 dB, which the display can just about resolve,
so the measured values are what ships.

**Confirmed on three blocks in three categories.** Fitted on a `212 Darkglass
Neo (M)` (Cabsim Bass); then PREDICTED and found on a `412 CA Stand OS S V30 90s
(M)` (Cabsim Guitar): 0.20 -> -6.7 and 0.85 -> +4.5. Then predicted and found
inside **Parallax**, a Bass Overdrive carrying its own two-mic cab section:
0.50 -> 0.0 and 0.25 -> -5.2. So the taper belongs to the **cab section wherever
it appears**, not to cab models, and Parallax is keyed explicitly because the
category aliasing cannot reach a Bass Overdrive.

It shares the lane VOLUME's STRUCTURE - a numeric floor at wire 0.01 with the Off
detent below it - but not its values: -21.8 dB here against the lane's -39.5 dB
at the same wire position.

Note what this rules out. Cab LEVEL sits in the same dB family
as the lane and mixer levels and is **not** their -40..+12 scale - unity
is at 0.5 rather than 10/13. Treating the bucket as one scale would put a value
20 dB wrong on the wire.

Everything NOT in that table still refuses a `Real`. That is the honest answer:
an unmeasured span cannot be converted, only guessed.

**One parameter will not be measured.** `NC_Recorder`'s `OUT LEVEL` is reachable
only by placing the internal Neural Capture recorder on the grid, and doing that
**crashed the unit** - "Something went wrong", requiring a reboot. Its `internal`
and `hidden` flags are both false, which is what made it look like an ordinary
block; the category name "Neural Capture Internal" was the real signal. The same
session then placed a cab and Parallax without it and nothing went wrong, so the
recorder is the cause. It is recorded in `units.DO_NOT_PROBE` rather than left
looking merely unmeasured, so nobody repeats it. This is the second time probing
capture/IR machinery has taken the unit down.

**Five FX-loop parameters turned out to be TWO scales, not one and not five.**
Worth the method note: they were grouped by setting all five to the SAME wire
value and reading them together, twice, at 0.10 and 0.50. Two readings agreeing
at one point could be two laws crossing; agreeing at two points is a family. The
send side tops out at unity and the return side at +12, so pooling them would
have put a send 12 dB out at full travel.

### What a host can assign an expression pedal to

Every `Model`-shaped collection was tested directly, one write per target, read
back after a settle and restored. The result is that **one shape works
everywhere and there are exactly two exceptions**:

| collection | parameters tested | host assignment |
|---|---|---|
| `models` (a block) | Jewel `HIGH CUT` (`switch`) | yes |
| `models` bypass | via `bypass_expression` | yes - `set_expression_bypass` |
| `input_control` (input gate) | `NOISE REDUCTION`, `INPUT GAIN` (`float`), `BYPASS` (`switch`) | yes, all three, and `BYPASS` clears too |
| `mixer` | `LEVEL A` (`float`), `PHASE` (`switch`) | yes |
| `combined_splitter` | `LEVEL TO A` (`float`), `TYPE` (`switch`) | yes |
| `output_control` | `VOLUME`, `PAN` (`float`) | yes |
| `output_control` | **`MUTE`, `SOLO`** (`switch`) | **NO - silently dropped, both directions** |

The MUTE/SOLO refusal is a measured fact and not an instance of any rule. Three
were tried and all are false:

* **"switch parameters are refused"** - `HIGH CUT`, `PHASE` and `TYPE` are all
  `switch` and all accept an assignment.
* **"bypass-like parameters are refused"** - the input gate's `BYPASS` is
  bypass-like, accepts an assignment, and accepts a clear.
* **"`output_control` rejects `expression`"** - `VOLUME` and `PAN` live in the
  same block and accept one.

`output_control.bypass_expression` is not a back door to it either: a write there
is ignored and `params[2].expression` does not move. The unit's own touchscreen
writes `params[].expression` directly, and that write is what a host cannot
reproduce. Note also that the unit **broadcasts nothing** when a lane output is
edited - 180 seconds of listening while an assignment was made on the screen
produced no `Grid` push carrying `output_control` - so this shape cannot be
learned by watching, only by writing and reading back.

**The differential capture (ADR-0010).** Because the unit broadcasts nothing
here, "no broadcast was observed" is not on its own grounds for the refusal, so
the state was captured in both positions of the control and diffed - every type
the device answers a READ for, every set field, and every field number the
recovered schema does not know. Between MUTE assigned and MUTE unassigned, the
only device state that moved was `PresetDirty.is_dirty` and the free-storage
counter (the save), plus the running metronome clock, which the harness labels
as noise rather than dropping. Nothing carries this control but the field the
touchscreen writes. On the preset side, assigning MUTE on the unit changed
exactly two lines across all four rows' `output_control`, `input_control` and
`models`: `params[2].expression` and `expression_bypass_info[0]`.


## Grid blocks

A grid cell holds a model id (`BinaryPreset.Model.hash`); `0` means empty. Both
edits use the row/column-keyed sparse `Grid` update, and the ACTION is what
distinguishes them:

- **Create or replace** - `Grid{UPDATE, chains{row, models{column, hash}}}`.
  The device makes no distinction between filling an empty cell and overwriting
  an occupied one.
- **Remove** - the same shape with `action: DELETE`. Sending an UPDATE with
  `hash: 0` does nothing: the zero really is transmitted (`hash` sits in a
  proto3 oneof, so it has explicit presence), the firmware simply treats a zero
  hash on an update as "no model specified".

Both were confirmed twice over: driven from the host with a read-back, and by
watching the device's own broadcast when the same edit is made on the unit -
deleting a block on the touchscreen emits exactly
`Grid{action: DELETE, chains{row: 0, models{column: 2, hash: 0}}}`.

Save the grid afterwards to keep the change, as with any other grid edit.

### A placement can be refused, for two known reasons

A preset has a finite processing budget, and a block that does not fit in what is
left **is accepted on the wire and simply is not there afterwards**. There is no
error: every host write is STALLed regardless, `GenericError` carries only cloud and
version codes, and `CompilerInhibitedModules` carries only two global booleans
(`global_gate`, `global_eq`).

Confirmed, and deterministic. Adding a six-block chain - two Neural Captures, an EQ, a
compressor, a bass cab (21005), an 8-band EQ - to a free row of factory "OneStar Clean
Tweed" (02C) placed five of the six and dropped the cab. The same happened on "Major
Strat Vibes" (10B). It is the block, not the position or the count: the cheaper EQ
placed AFTER the cab in the same chain landed both times, and shifting the chain a
column left refuses the cab in its new position instead.

**A port conflict is the second cause, and it looks identical from the host.**
Placing an `FX Loop 1` where a `Send 1` already claims the same physical send is
refused, and the unit puts a modal on its OWN screen that no message carries:
"Port Conflict / Send is used as an output by FX Loop Send 1 on path 2, please
change it first". Observed 2026-08-26, stacked four deep from four attempts, and
it stays refused until somebody dismisses it on the unit. The unit has a Send 1
and a Send 2, so an `FX Loop 2` beside `Send 1` places fine - it is the specific
port that collides, not the block type.

**A missing echo is NOT on its own a refusal.** `set_block(verify=True)` raised
twice in one session on blocks that had landed, so it now reads the preset back
before deciding, and only reports a refusal when the cell really is not holding
the model.

**The refusal is detectable without saving.** The device echoes a `Grid` broadcast
naming each cell it accepts, and a refused block produces none:

| placed on 02C row 1 | `Grid` echoes | `UndoRedo` | read-back |
|---|---|---|---|
| Eltron 30 (capture) | 3 | yes | landed |
| Capture 2 | 3 | yes | landed |
| Graphic-9 | 3 | yes | landed |
| Solid State Comp (M) | 2 | yes | landed |
| **212 Darkglass Neo (M)** | **0** | **no** | **missing** |
| Parametric-8 | 2 | yes | landed |

Echo latency was 0.29-0.42 s over six accepted placements, and a repeat write to a
cell that already holds that model echoes too, so an idempotent placement is not
mistaken for a refusal. `set_block()` waits for that echo by default and raises
`BlockRefused` when it does not arrive; `verify=False` restores fire-and-forget.

**DSP load itself is not readable on this firmware.** `CPULoadMessage` (type 26)
exists, with `cpu_total_load` and per-column `CPUColumnLoad`, and the RX path decodes
it, but nothing ever arrives: a bare `CPULoad{READ}` times out with no reply, adding
`"CPULoad"` to the connect burst's subscribe READs produces no pushes, and 40 seconds
of listening across those three conditions saw zero. So there is no way to check
headroom before placing a block; place it and check the echo.

## The model catalog (ModelRepo)

`ModelRepo` is fetched during the connect burst as a readiness gate, but its
payload is the device's whole block catalog: **gzip(tar(ModelRepo.xml))**, about
46 KB compressed and 557 KB expanded on the observed unit.

The XML is `<Models><Category id name><Model id name .../></Category></Models>`.
Two things make it valuable:

- **`Model/@id` IS the wire hash.** Ids are globally unique rather than
  per-category counters: category 4 (Equalizer) holds 4000-4007, category 21
  (Cabsim Bass) holds 21001-21009. So hash 21003 resolves directly to "810
  Amped VT Aln 70s (M)".
- **`<Parameter>` children are in wire-index order**, each with `min`, `max`,
  `defaultValue` and `units`. This is what gives a parameter index meaning, and
  it explains a puzzle from earlier work: writing index 0 of a cab moved no
  visible knob because a cab's only parameters are internal `ir selector`
  entries.

Parameter values on the wire are **normalized 0..1**, confirmed on hardware:
sending `1.0` to a `THRESHOLD` whose catalog range is -60..+12 dB made the unit
display +12.0 dB.

### A parameter's scale is in the catalog, and we were not reading it

The device publishes **24 attributes** on each `<Parameter>`. This library read
eight of them until 2026-08-26, and four of the seventeen it discarded carry facts
the project had spent days measuring off the unit's screen.

#### One law covers every parameter

```
real = min + (max - min) * wire ** (1 / skew)
```

`skew` has three spellings: a number, `LIN_SKEW` (1.0), and `LOG_SKEW`. **`LOG_SKEW`
is not a logarithmic sweep** despite the name - it is the same power law at skew 0.3.
Two dirty values ship in the catalog: `" 0.4"` with a leading space, on two
parameters, and `""` on two more. Both fall back to linear.

Confirmed on hardware 2026-08-26, over three unrelated blocks in two different units:

| block | parameter | skew | wire | predicted | screen |
|---|---|---|---|---|---|
| any cab | `LEVEL`, indexes 2 and 10 | 4.9594844 | 0.01 / 0.50 / 1.00 | -21.82 / 0.00 / 6.00 dB | -21.8 / 0.0 / 6.0 |
| Low-High Cut | `HPF FREQ` | 0.3 | 0.25 | 216.7 Hz | 217 Hz |
| Low-High Cut | `LPF FREQ` | 0.3 | 0.75 | 7678.3 Hz | 7678 Hz |
| Low-High Cut | `OUTPUT` | none | 0.25 | -10.0 dB | -10.0 dB |
| Envelope Filter | `FREQ` | LOG_SKEW | 0.25 | 197.4 Hz | 197 Hz |
| Envelope Filter | `RESO` | LOG_SKEW | 0.75 | 4.450 | 4.45 |

The two `LOG_SKEW` readings solve independently to exponent 3.3366 and 3.3330, both
`1/0.3`. A true log sweep would have shown 316 Hz and 5.62; linear, 2575 and 7.75.

**615 parameters carry a non-linear skew**, and every one of them converted as a
straight line before this was read.

#### There is no such thing as a placeholder range

This document said, for several releases, that a parameter published as `min="0"
max="1"` with a real-world unit was a "placeholder" whose true span was not in the
catalog. **Zero parameters in the shipped catalog are published that way.**

What actually happens is that `min` and `max` are sometimes a NAME:

```xml
<Parameter name="LEVEL" units="dB" min="MIN_CABSIM_DB" max="MAX_CABSIM_DB"
           skew="4.9594844" min_string="OFF"/>
```

The parser handed those to a float conversion that fell back to `0.0` and `1.0`. That
fallback invented the concept, and a table of hand-measured spans grew for months to
work around it. Eight families, 55 parameters:

| constant | count | parameters | value | how it is known |
|---|---|---|---|---|
| `EQ_DB` | 16 | the band gains, over three EQ models | -12..12 | `steps=241` fixes 0.1 dB steps; measured at four points |
| `CABSIM_DB` | 12 | cab `LEVEL` | -40..6 | a PCOM cab spells the same knob out literally |
| `FXLOOP_OUT_GAIN_DB` | 9 | `LEVEL`, `SEND LEV`, `THRU` | -40..0 | measured at five points; tops out at unity - a send cannot boost |
| `FXLOOP_IN_GAIN_DB` | 6 | `LEVEL`, `RET LEV` | -40..12 | measured at three points; a return can boost |
| `MIXER_DB` | 8 | `LEVEL A/B`, `LEVEL TO A/B`, `MIXER LEVEL`, `VOLUME` | -40..12 | measured; and `LEVEL TO A/B` sit at 10/13, which is 0 dB on that span |
| `TEMPO` | 1 | `TEMPO` | 40..240 | `steps=201` fixes whole bpm; measured at three points |
| `EQ_FREQ` | 2 | `FREQUENCY` on both splitter models | 20..20000 | solved, see below |
| `INPUT_TRIM` | 1 | `NC_Recorder OUT LEVEL` | **unknown** | placing the block crashes the unit |

The numbers live in `units.FIRMWARE_CONSTANTS`, each with its evidence. A name this
build has never met now **raises** rather than falling back, because falling back is
what created the bug.

`MIN_EQ_FREQ` needed no screen at all. The catalog states `defaultValue="400.0"` and
`skew="0.17722914651016206"`, and the unit was holding wire `0.49547526240348816` for
that knob. Solving the law for `max` with `min=20` gives **20000.000** to three
decimals: two catalog facts and one wire reading pin both ends.

The recorder's bound stays unknown on purpose. `Parameter.minimum` and `.maximum` are
`None` there, and converting refuses rather than answering against a made-up number.

#### The bottom of a scale is sometimes a word

`min_string` is set on 254 parameters - `OFF` on 191, and also `-Inf` and `L`. It says
the bottom of the range shows a word rather than a number. It does **not** say where
the numbers resume, and only measurement knows that: a cab LEVEL's law runs to -40 dB
while its quietest real position is **-21.8 dB at wire 0.01**, with OFF below.

That gap is not cosmetic. Without a floor, asking a cab for -30 dB converts to wire
0.0005 and **mutes the microphone** - a write that looks like it worked and did
something else. `units.FLOOR_WIRE` holds the measured floors, keyed by the same
family names, and a value below one is refused. For silence, write the wire's `0.0`.

**Unity for the level parameters is `0.76923077`** - 10/13, i.e. 0 dB on -40..+12.
Measured: `MIXER LEVEL` and `LEVEL TO A`/`LEVEL TO B` read exactly that on every one
of the 34 rows carrying them across 17 factory presets, and lane `VOLUME` on 52 of 68
rows. So that value is the default, not attenuation somebody dialled in - which is
what makes a `LEVEL B` of 0.0 next to it recognisable as a deliberately silenced lane.
`pyquadcortex.protocol.UNITY_LEVEL` holds it.

Two releases said the level span was -100..+30 dB, and the error is worth keeping:
both spans put 0 dB at exactly 10/13 (100/130 = 40/52), so the unity measurement -
however many rows it covered - could never distinguish them. **A span needs at least
one point away from the reference**, and three points in one half are not enough
either: three in the cab LEVEL's upper half fit a straight line beautifully and are
12 dB wrong at wire 0.01.

#### The option names were there all along

`stepNames` carries them, on 539 parameters. `set_param_option`'s docstring said the
names were "not in the catalog - they are in the preset, per block"; that is true of
the **12** parameters marked `dynamic="true"`, whose list includes one entry per block
earlier in the chain, and false for the other 527.

Those 527 use only **113 distinct lists**, of which two - `Off,On` and `OFF,ON` - cover
247 parameters. `pyquadcortex.protocol.options` publishes the other 111 as enums;
Off/On parameters take a `bool`. Confirmed on hardware: wire 0.25 on a Low-High Cut's
`HPF SLOPE`, option 2 of 9, showed `-12 dB/o`.

The device's own spelling is preserved on the wire. Sixteen `INVERT` parameters offer
`Noral,Inverted`; the enum member reads `NORMAL` and `options.OPTION_LABELS` keeps
`Noral`, which is what a dynamic list matches against.

#### `expAssignable` is the unit's rule, not the host's

Fourteen parameters carry `expAssignable="false"`. **It does not govern a host write.**
ADR-0010 capture, 2026-08-26: a Pattern Tremolo's `STEPS` (one of the 14) and `DEPTH`
(not) both took a pedal identically - `(1, 0.15, 0.85)` - and both survived a
disconnect and a fresh read. It is published as `Parameter.exp_assignable` and nothing
in the library acts on it. What it most likely governs is which knobs the touchscreen
offers for assignment, and that is a guess; whether the unit ACTS on the stored
assignment is a separate unknown that needs audio, not a wire read.

### Two parameters that never round-trip, and one mirror

**`input_control` index 2 (GAIN REDUCTION) is a live meter, not a setting.** The device
samples it into the preset at save time, so two saves of an identical rig differ there.
Exclude it from any before/after comparison - `GAIN_REDUCTION_PARAM` names it, and
`params_equal()`'s docstring carries the same warning.

**Some block parameters MIRROR a preset-level setting.** Writing the per-preset metronome
mute (tempo settings index 4) also changed a Looper X block's METRONOME MUTE parameter
(observed at row 3 column 7, param 21, 0.0 -> 1.0, on factory 01C). A diff of "rows I did
not touch" sees an unexplained change that is neither corruption nor a stray write. Known
mirrors so far:

| preset-level setting | mirrored block parameter |
|---|---|
| tempo settings index 4 (the unit's MUTE; 1.0 = audible) | Looper X `METRONOME MUTE` (param 21) - whose NAME is misleading: it tracks param 4 exactly, so 1.0 there also means AUDIBLE. **One press of the unit's MUTE button writes BOTH**, same value, in the same burst - captured |

The list is almost certainly longer; add entries as they are hit. A caution this table
earned the hard way: a mirror proves two parameters are LINKED and nothing more. Index 4's
polarity was once inferred from the mirror target's NAME (METRONOME MUTE) - and the name
was the misleading part. 1.0 means running, on both ends.

### `param_values` can contain NaN

Factory "Strat Ambience" (05B) stores NaN in `param_values` at several parameter
indices across several scenes; "Classic Pedalboard" (07C), "Rols Jazz" (09A) and
"Major Strat Vibes" (10B) do too. It round-trips a save unchanged, so it is
presumably a legitimate unused slot rather than corruption.

Because `nan != nan`, a preset compared field-by-field against ITSELF reports
differences. Anything diffing presets - to check a build is reproducible, or that an
edit left other rows alone - has to treat NaN as equal to NaN, or it will report a
false failure on a preset that is in fact identical.

### Adding a block rewrites comboBox values on rows you never wrote to

A comboBox parameter whose option list has one entry per block in the preset has its
stored normalised value **recomputed by the device when the preset's block count
changes**, including on rows nothing was written to. The selected INDEX stays put; the
denominator moves.

Confirmed on factory "US TWN Vibrato" (01C), whose row 2 column 0 is a Doubler
(`16011`) with `TRIGGER` (index 4, type comboBox). Each case recalled 01C, made the
stated edit ON ROW 1, saved to a scratch slot and read back:

| case | `TRIGGER` reads | blocks |
|---|---|---|
| shipped | 0.0526316 = 1/19 | 13 |
| recall and save, no edit | 0.0526316 = 1/19 | 13 |
| add 1 block | 0.0500000 = 1/20 | 14 |
| add 4 blocks | 0.0434783 = 1/23 | 17 |

The denominator is `blocks + 6`. The control case matters: a save round-trip on its
own does not move it, so this is a response to the option list changing length rather
than to saving. A Tremolo `WAVEFORM` comboBox in the same preset, whose 5-option list
is fixed, came back byte-identical - so this is specific to selectors that enumerate
the preset's blocks.

The consequence is for diffing: the natural way to show "I added a chain and left the
rest alone" is to compare the untouched rows against what shipped, and that comparison
fails on a row never addressed.

**comboBox option names are not in ModelRepo - they are in the PRESET.** The catalog
gives such a parameter only `min`, `max`, `steps` and `type` (`TRIGGER` is published as
`min=0 max=44 steps=45`, static and inconsistent with the observed option count of
19/20/23). The rendered list is carried per preset in `Param.dynamic_steps`, with
`Param.dynamic_icons` alongside. Read from 01C, the Doubler's `TRIGGER` options are:

```
Off, Follow Input, Input 1, Input 2, Input 1/2, Return 1, Return 2, Return 1/2,
USB input 5, USB input 6, USB input 7, USB input 8, ...
```

So option index 1 is **'Follow Input'**, a fixed entry, and the fixed entries are
followed by one per block in the preset - which is exactly why the stored value's
denominator tracks the block count. `pyquadcortex.protocol.param_options()` reads this.

Attributes that classify a model:

| Attribute | Meaning |
|---|---|
| `sku`, `plugin_id` | purchasable plugin content (the Archetype models); a given unit may not have it |
| `hidden`, `internal` | not user-facing; `hidden` also appears on whole categories |
| `replaces` | this model supersedes the listed id(s). Both stay in the catalog and they can share a display name - there are two "Graphic-9" equalizers, 4005 replacing 4002 |

Because the catalog comes FROM the device it also covers Neural Captures
(categories 14 and 20), which are user content. That is why the library ships
generated constants only for factory content (412 of the 533 models on the observed
unit) and resolves everything else at runtime.

**A capture id is a BLOCK TYPE, not a capture.** Category 14 holds only a couple of
models - `14000` and `14001` on the observed unit - and saving a new capture does not add
one. Which capture a block plays is the string parameter **`file_name`** at index 5:

```
file_name = <64-char content hash><display name>
```

the hash being the `key` of the file in the Captures Library, concatenated directly with
its name and no separator. Factory 28A's capture block holds
`"3c06...3a2dDarkglass VMT 1"`.

Confirmed both ways: read off factory content, and by creating a capture on the unit and
then pointing a host-placed block at it - `set_block(model=14000)` followed by a
`file_name` write read back exactly, with the new capture named on the unit.

This corrects an earlier inference recorded here. Thirteen of 17 surveyed factory presets
reference id `14000` from positions no single capture could fill at once - the amp slot in
28A and 27E, a pedal slot ahead of the real amp in 02C, row 0 column 2 in 09A opposite a
real amp - which had been read as evidence that the id was a per-unit SLOT. It is not:
they all use the same block model with different `file_name` strings, and the earlier
reading came from assuming the id carried the identity.

**The catalog cannot enumerate what captures are available**, so browse the library
instead - `local_nc_root`, over two thousand entries on the observed unit, sub-divided on
screen into Factory Captures V1, Factory Captures V2 and My Captures.

## Open questions

Stated explicitly so nobody builds on a guess:

- **The two device-filled trailer bytes** (offset `n+6`) have no known meaning.
  They do not match common CRC-16 variants. Sending zeros works; ignoring them
  on receive works.
- ~~**The "raw payload" trailer flag**~~ - ANSWERED. It is two flag bytes, not
  one, and they sit at `n+4` (ENCRYPTED) and `n+5` (COMPRESSED). Confirmed on
  2026-09-03 over 15,675 logical messages from three USBPcap captures of Cortex
  Control 4.0.1, in both directions: ENCRYPTED appears only on `License` and
  `CloudLogin`, COMPRESSED agrees with the gzip magic bytes 15675/15675, and
  both vary WITHIN a single message type, so neither is a property of the type.
  See [section 2.3](#23-the-message-envelope-trailer). Two smaller things stay
  open, and are listed here rather than folded into the answer: the type field's
  width is not observable (the high two bytes are always zero and every type is
  under 256), and the reason CortexUSB skips decompression for types 32 and 33
  was not reproduced on this firmware.
- ~~**`FileMessage.type`** was always `0`~~ - ANSWERED: it is a category selector,
  `0` presets / `1` IRs / `2` captures, established with request_id-attributed sweeps
  (see the IR section) and independently corroborated by a state-tracking session that
  watched the connect burst enumerate `0 -> 2 -> 1`.
- **`delete_from_library`** (on `FileMessage`) exists in the schema but was never
  sent. Its effect is unknown.
- **Most output port ids** are schema-derived rather than hardware-confirmed
  (see [section 8](#output-ports-chainout_portid)).
- **DSP cost per model** is not published anywhere reachable, and `CPULoad` never
  arrives (see [above](#a-placement-can-be-refused-for-want-of-dsp-capacity)), so
  whether a block will fit can only be discovered by placing it.
- ~~**The true spans behind the placeholder 0..1 ranges**~~ - ANSWERED, and the
  question was wrong. There are no placeholder ranges: `min` and `max` are
  sometimes a NAME the parser could not read, and it fell back to `0..1`. Seven of
  the eight families now have numbers; splitter `FREQUENCY` was the last holdout
  and fell to arithmetic rather than a screen reading, from the catalog's own
  `defaultValue` and `skew` against one wire value. **`NC_Recorder`'s `OUT LEVEL`
  is the one bound still unknown**, and permanently so: placing the block crashes
  the unit.
- **What `expAssignable="false"` actually governs** is unknown. It marks 14
  parameters and demonstrably does NOT stop a host expression write - both halves
  of a differential capture took the pedal. The touchscreen's own assignment menu
  is the obvious candidate and has not been checked. Separately, whether the unit
  ACTS on an assignment stored against such a parameter needs audio, not a wire
  read.
- **What `toggleOn` / `toggleOff` / `toggleStep` mean**, on 212 parameters. The
  obvious reading is a footswitch toggle's two values; obvious and untested.
- **Whether a capture id denotes different content on a different unit** is untested
  here, needing a second unit.
- ~~**Whether a preset's descriptive `tags` can be set at all**~~ - ANSWERED: no. The
  unit's own Save As strips them too (factory 5D's six tags -> none on the on-unit
  copy), so tags are build-chain/cloud metadata that no save path preserves.
- **Whether host writes are honoured during STANDBY** is untested. (Writes ARE
  honoured during lock mode, but that is a different state.)
- **Echo latency for a bypass write** is still unmeasured. Routing, scene
  label/colour and global settings are now characterised in
  `tests/hardware/test_write_echo.py`; the bypass case skips unless the block it
  targets already carries a stored bypass entry, so it needs a preset whose first
  block has been bypassed at least once.
- **Cross-setlist moves**, downloads/plugin folders
  (`SetlistPosition.is_downloads`, `is_plugin`), IR payloads
  (`FileMessage.ir_payload`), and bulk operations
  (`total_bulk_create_count`, `BulkOperation`) are all present in the schema and
  entirely unobserved.
- **Roughly half the schema's 71 message types** have never been seen on the
  wire by this project, including tuner, looper, MIDI settings, Neural Capture,
  backups, and diagnostics. Their field layouts are known from the schema; their
  behaviour is not.

# Quad Cortex USB control protocol

> Purpose: what is known about the wire the Quad Cortex speaks to Cortex Control, per device profile, with an evidence tag on each fact.

Everything here was established by observing Cortex Control sessions on the wire
and then confirming each finding on a unit.

> **Applies to a device profile, and says which.** A profile is what the unit
> reports in its `Version` reply, `device_type` and `zenos_git_hash` (the CorOS
> version), and it is a client class: `QuadCortex` (4.0.1) in
> `pyquadcortex/protocol/client.py`, `QuadCortex41` and `QuadCortexMini` in
> `pyquadcortex/protocol/profiles.py` (ADR-0020). Every statement below was
> measured on **Quad Cortex, CorOS 4.0.1** (`app_fw` `d14e`), the maintainer's
> unit, unless it says otherwise. An observation from another profile is written
> beside the 4.0.1 record, dated and named. `app_fw` alone does not identify a
> profile: a contributor reports `d14e` on CorOS 4.1.0 too.
>
> **The protocol is unversioned.** Nothing on the wire identifies a schema or
> protocol version, so none of this is guaranteed across a CorOS update. Re-verify
> after a firmware change ([architecture.md](architecture.md#adding-a-device-profile)).
>
> Unofficial: not affiliated with, endorsed by, or supported by Neural DSP.

## Contents

- [1. The USB HID interface](#1-the-usb-hid-interface)
  - [1.1 Exclusive access](#11-exclusive-access)
  - [The benign write stall](#the-benign-write-stall)
- [2. Report framing](#2-report-framing)
  - [2.1 Report layout](#21-report-layout)
  - [2.2 Fragmentation and reassembly](#22-fragmentation-and-reassembly)
  - [2.3 The message envelope (trailer)](#23-the-message-envelope-trailer)
  - [2.4 Compressed payloads](#24-compressed-payloads)
  - [2.5 Annotated real frames](#25-annotated-real-frames)
- [3. Message types and actions](#3-message-types-and-actions)
- [4. The connect handshake](#4-the-connect-handshake)
- [5. Request/response correlation](#5-requestresponse-correlation)
- [6. Addressing presets](#6-addressing-presets)
- [7. Presets and the grid](#7-presets-and-the-grid)
- [8. Per-preset tempo and the metronome](#8-per-preset-tempo-and-the-metronome)
- [9. Preset-level settings](#9-preset-level-settings)
- [10. File operations and the Directory](#10-file-operations-and-the-directory)
- [11. Global device settings](#11-global-device-settings)
- [12. What the unit announces, and when](#12-what-the-unit-announces-and-when)
- [13. Port, instrument, and preset enums](#13-port-instrument-and-preset-enums)
- [14. The model catalog (ModelRepo)](#14-the-model-catalog-modelrepo)
- [Operation coverage](#operation-coverage)
- [Open questions](#open-questions)

## 1. The USB HID interface

The Quad Cortex enumerates as vendor `0x152A` (Neural DSP), product `0x880A`.
Control traffic uses one HID interface, **interface 5** (`bInterfaceClass = 3`),
usage page `0x0001`, usage `0x0000`. The unit also exposes USB audio interfaces;
those are unrelated to control.

The HID report descriptor (read from the OS registry, no device open required):

```
05010900a101150026ff008501750895800900818285027508958009009182c0
```

It declares report ID `0x01` as a 128-byte input report (unit to host), report
ID `0x02` as a 128-byte output report (host to unit), and no feature reports. The
OS reports `MaxInputReportSize` and `MaxOutputReportSize` as **129**: 128 payload
bytes plus the report-ID byte, which hidapi includes in both directions.

The interface has one interrupt-in endpoint and no interrupt-out endpoint, so
output reports are delivered as `SET_REPORT` control requests on endpoint 0. That is what
produces the write stall below.

With no host session driving it, the unit emits no unsolicited input reports at
all (a 30-second idle listen produced zero). Unit-to-host traffic is entirely a
reaction to host traffic or to a person operating the unit.

### 1.1 Exclusive access

On macOS, Cortex Control opens the HID interface with
`kIOHIDOptionsTypeSeizeDevice`. While it runs, no other process can open the
interface, not even read-only: attempts fail with `kIOReturnExclusiveAccess`
(`0xE00002C5`). **Quit Cortex Control before using this library.** This is also
why passive sniffing beside a running Cortex Control is not possible on macOS.

### The benign write stall

**Every HID output report is accepted by the unit and then reported as a write
error by the host. The error is meaningless and must be ignored.**

The unit consumes the 128-byte data stage of the `SET_REPORT` transfer, acts on
it, and then stalls the transfer's status stage. Host stacks surface that as a
failed write:

| Host stack | Symptom |
|---|---|
| Windows (USB layer) | transfer completes with `USBD_STATUS_STALL_PID` (`0xC0000004`) |
| Windows / hidapi | `hid_write()` returns `-1` |
| macOS `IOKit` | `IOHIDDeviceSetReport failed: (0xE0005000)` |
| libusb (raw control transfer) | `LIBUSB_ERROR_PIPE` (`-9`) |

Three facts make this normal behaviour rather than a client bug:

1. Cortex Control gets the same stall. In a captured session all 273 of its
   host-to-unit `SET_REPORT` transfers completed with `USBD_STATUS_STALL_PID`,
   and the unit acted on every one.
2. Two independent USB stacks see it: Apple's `IOKit` HID stack and raw libusb
   (`bmRequestType=0x21`, `bRequest=0x09`, `wValue=0x0202`, `wIndex=5`). The
   stall is the firmware's decision.
3. Writes work despite the "failure": with every `hid_write()` returning `-1`,
   the unit echoed a session token, answered a `Version` read, recalled presets
   and switched scenes.

Inbound control requests are not stalled: `GET_REPORT` on the input report
succeeds. There is no unlock behind the stall. Tested and ruled out: buffer
length, the report-ID byte, exclusive versus shared open, the client library,
retry bursts, code-signing entitlements, standard HID init requests, and whether
USB audio is streaming.

**Consequence:** a transport swallows write errors (`transport._write_report`
logs them at debug) and detects a dead unit through request timeouts. If writes
ever fail for real, the symptom is timeouts, not write errors.

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

Padding is zero from the host. From the unit, padding is stale buffer content and
must be ignored; `len` is authoritative.

Constants in `pyquadcortex/protocol/framing.py`: `REPORT_SIZE = 128` (body),
`CHUNK_SIZE = 126`, `OUT_REPORT_ID = 0x02`, `IN_REPORT_ID = 0x01`,
`FLAG_FIRST = 0x40`, `FLAG_LAST = 0x80`.

### 2.2 Fragmentation and reassembly

A logical message is split into 126-byte chunks, one per report:

- the first report has `FLAG_FIRST`, the last has `FLAG_LAST`, a single-report
  message has both (`0xC0`), and middle reports have `0x00`;
- middle reports carry no header beyond `len` and `flags`: no sequence number,
  no chunk id, no offset;
- non-final fragments always carry a full 126 bytes;
- **there is no total-length field anywhere.** Reassembly is flag-driven:
  concatenate each report's `len` data bytes until a report with `FLAG_LAST`.

Two safety behaviours follow, both in `transport._read_loop`: a `FLAG_FIRST`
report while a partial message is buffered means the previous message was lost,
so drop the buffer and start again; and cap the buffer so a lost `FLAG_LAST`
cannot accumulate forever. The cap is 1 MiB, well above the largest observed
message (a `ModelRepo` reply, about 47 KB gzipped over 371 reports).

### 2.3 The message envelope (trailer)

A reassembled message is `protobuf ++ trailer(8)`:

```
offset  size  field
0       n     protobuf-serialized message (see the recovered schema)
n       4     CortexMessageType.Enum value, uint32 little-endian
n+4     1     ENCRYPTED: 1 = the payload is not protobuf and cannot be read
n+5     1     COMPRESSED: 1 = the payload is a gzip stream
n+6     2     zero from the host; the device fills varying nonzero values
```

Two facts: **the message type lives in the trailer, not in a
header**, so a receiver cannot know a message's type until the last fragment
arrives; and there is no length field.

The unit-filled bytes at `n+6` do not match common CRC-16 variants and their
meaning is unknown. Send zeros; ignore them on receive. `Frame.device_bytes`
reports them and nothing reads them.

**The two flag bytes.** Measured over the three USBPcap captures of Cortex
Control 4.0.1 in the lab repository (15,675 logical messages, both directions,
`research/scripts/trailer_flags.py`), and reproduced live on 2026-09-03 through
this library's own client on macOS (1,000 messages, `trailer_flags_live.py`).
Same firmware both times, so this is evidence about a second client and a second
host, not a second CorOS.

| what | result |
|---|---|
| `n+2` and `n+3` | zero in all 15,675 messages, both directions |
| `n+4` set | 13 messages, all `License` or `CloudLogin` |
| `n+4` clear | 15,662 messages, none of which failed to parse for that reason |
| `n+5` set | 39 messages, every one starting with the gzip magic `1f 8b` |
| `n+5` against the gzip magic | agree 15,675 of 15,675 (1,000 of 1,000 live) |
| `n+6..n+7` | always zero from the host; nonzero on 2,700 of 14,539 unit messages |

**Both flags describe the frame, not the message type**, so a per-type table
would be wrong:

| type | direction | flag | payload |
|---|---|---|---|
| `License` | host to unit | encrypted 0 | plain protobuf, `action: READ` |
| `License` | unit to host | encrypted 1 | 17 bytes, not protobuf |
| `File` | unit to host | compressed 0 | plain protobuf, x1,173 |
| `File` | unit to host | compressed 1 | gzip, x30 (the factory library listing) |
| `RecallPreset` | unit to host | compressed 0 | plain protobuf, x1 |
| `RecallPreset` | unit to host | compressed 1 | gzip, x9 |

`License` is in the handshake's subscribe burst, so an ordinary `connect()`
provokes the encrypted reply; no cloud operation is needed to see the flag.

What "encrypted" means here: the flag marks a payload a receiver must not parse
as protobuf, and only `License` and `CloudLogin` carry it. The name comes from the
`.NET` library CortexUSB, which decrypts on the same flag. This library reads the
flag, labels the frame, and hands the bytes back untouched (ADR-0019).

Two things are recorded as open rather than settled. CortexUSB skips
decompression for `KeepAlive` (32) and `GlobalTempo` (33); in these captures both
types have both flags clear (`GlobalTempo` x3,785, `KeepAlive` x944), so that
exception was not reproduced on CorOS 4.0.1. And the type field's width is not
observable: bytes `n+2` and `n+3` are always zero and every type is under 256, so
uint32 and uint16-plus-two-reserved produce identical bytes. The codec takes the
uint32 reading, matching CortexUSB, and nothing depends on it.

### 2.4 Compressed payloads

Two independent kinds of compression appear:

- **Frame-level gzip.** Some payloads are gzip streams: the reassembled payload
  starts `1f 8b` and the decompressed bytes are the ordinary protobuf message.
  `RecallPreset` pushes and the factory-library folder listing do this. The
  trailer's `COMPRESSED` byte says the same thing and agreed with the magic bytes
  on all 15,675 messages measured. **The library tests the magic bytes, not the
  flag**: CortexUSB reads the flag and needed a skip-list for two message types,
  and magic bytes need no exceptions. `framing` reports the flag and the RX path
  logs a line if the two ever disagree.
- **Field-level gzip.** Large protobuf replies such as `ModelRepo` gzip their
  content inside a normal protobuf `bytes` field, so the frame payload itself is
  plain protobuf.

### 2.5 Annotated real frames

A `Version` read, single report (`tests/fixtures/frames/version_read.json`):

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

A 290-byte `Version` reply from the unit, spanning three input reports
(`tests/fixtures/frames/version_reply_multi.json`):

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

The `Version` reply carries the unit's kernel string, `zenos` version,
`app_fw_version`, bootloader version, network address and serial number, among other
fields (`VersionMessage` in the schema).

A `License` reply, the smallest frame with a flag set
(`tests/fixtures/frames/license_reply_encrypted.json`):

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

The host's `License` read that provoked it carries `08 03` and `ENCRYPTED = 0`
(`tests/fixtures/frames/license_read.json`). Same type, opposite flag.

## 3. Message types and actions

Every frame's trailer carries a `CortexMessageType.Enum` value. The schema
declares **71 types** (`Undefined = 0` through `GenerateTestPreset = 70`, with
`NumberOfMessageTypes = 71` as a sentinel). The ones this library uses most:

| Value | Type | Role here |
|---|---|---|
| 1 | `Grid` | grid edits (params, bypass, chain routing) |
| 2 | `SetlistPosition` | preset recall |
| 4 | `File` | enumerate, save, delete, move |
| 10 | `Version` | version read, and the Cortex Control version announce |
| 12 | `GridMove` | move a block between grid positions |
| 13 | `Scene` | select the active scene |
| 15 | `RecallPreset` | the unit's push of the full current preset |
| 22 | `SceneCopy` | copy or swap scenes |
| 23 | `SceneLabel` | scene name |
| 32 | `KeepAlive` | session keepalive |
| 48 | `SceneColor` | scene colour (ARGB) |
| 49 | `Connection` | connected / disconnected announce |
| 51 | `ModelRepo` | required readiness step in the handshake, and the catalog |
| 52 | `ResetCommsBuffers` | session hello with a session token |

`registry.py` registers 70 of those 72 enum values (the 71 types plus the
sentinel) so the RX path can decode them.

Most messages carry `action` (field 1) from `MessageAction.Enum`:

```
CREATE = 0    UPDATE = 1    DELETE = 2    READ = 3
MOVE = 4      COPY = 5      UPLOAD = 6    DOWNLOAD = 7    SWAP = 8
```

`CREATE = 0` is the proto3 default, so an omitted `action` means `CREATE`. That
is how Cortex Control's "Save As" is sent.

**Field presence.** Most scalar fields in a preset payload are wrapped in a
synthetic `oneof` (proto3 `optional`), so "set to zero" and "not set" are
distinguishable via `HasField()`. Absent fields mean "unchanged", which is what
makes sparse keyed grid edits work. Presence is not universal: `Preset.proto` has
22 singular fields without it and `ProductionAutomation.proto` has 243, including
every `action` field, all of `SceneCopyMessage`, `SceneLabelMessage` and
`SceneColorMessage`, and `SceneBypass.bypass`. `HasField` raises on those. Use
`pyquadcortex.protocol.field_present(msg, "name")`, which answers `False`
instead. Two more presence facts from real payloads: `Chain.row` is absent on a
recalled preset (section 7), and `Param.index` is absent too, so a parameter's
index is its position in `params`.

**The protocol is symmetric.** The same types flow both ways. The unit answers a
host `Version` read and then asks one of its own, and it announces `Scene`,
`SceneLabel`, `SceneColor`, `SceneCopy`, `Grid` and `RecallPreset` when a person
operates the touchscreen. So a message of a tracked type is not always news: the
unit's own `Version` read carries `action` and nothing else. Anything counting
inbound messages needs a rule for the ones that say nothing; the model's cache
counts a message only when it carried a field the cache keeps or one it does not
(`device/state.py`, `_apply_one`).

## 4. The connect handshake

### 4.1 Why it is required

**The unit will not push state to a client that has only opened the pipe.** A
minimal `ResetCommsBuffers` plus `Connection{connected: true}` is not enough:
with only that, preset recalls produced zero traffic. The unit answers direct
requests, but the pushes that carry real state (the `RecallPreset` dump, `Grid`
and `Scene` live sync, folder listings) never arrive.

Two steps matter and are not obvious:

- **The Cortex Control version announce.** The unit gates its pushes on receiving
  a valid `cortex_control_version`. The library announces `"4.0.1"`
  (`QuadCortex.CC_VERSION`), the string seen on the wire from Cortex Control.
- **A `ModelRepo` read.** Empirically required: with it the unit starts pushing;
  without it, with everything else present, it stays silent.

### 4.2 The sequence

As Cortex Control performs it:

1. host: `ResetCommsBuffers{request_id: 0, session_id: <fresh 32 hex chars>}`.
   The unit echoes the same `session_id`.
2. host: `Version{action: READ}`. The unit replies with its full version blob,
   and 0.5 to 0.8 ms later with its own `Version{action: READ}` carrying `action`
   alone (measured 2026-08-27, ten reads of ten). Cortex Control answers
   `Version{action: UPDATE, cortex_control_version: "4.0.1"}`. The unit keeps
   talking even if its own read is never answered, but the `UPDATE` is what opens
   the push gate.
3. host: `Connection{connected: true}`, then a burst of reads for device state
   (`ModelRepo`, `IOSettings`, `Scene`, `SetlistPosition`, and the rest).
4. host: `KeepAlive{action: UPDATE}` about every second thereafter.

Each read in step 3 acts as a subscription: the unit pushes that state type to
clients that asked for it. `QuadCortex._SUBSCRIBE_TYPES` lists the set, mirroring
Cortex Control's burst: 22 types by default, or 21 with `initial_file_listing=False`.
A contributed CorOS 4.1.0 check on 2026-09-08 connected with the 21-type form
and then enumerated all 586 folders explicitly under `Support.EXPERIMENTAL`
(a 4.0.1 unit reports 399).

**`File` is the one type measured not to gate pushes.** Measured 2026-09-09 on
CorOS 4.0.1: a client that never sends the `File` read is still told when a
preset is saved on the unit, and the save's cascade (`File`, `UndoRedo`,
`RecallPreset`, `Scene`, `RecentsFavorites`, `SetlistPosition`, `Grid`) is
identical with and without it. Omitting the read costs the 399-message
enumeration and nothing else a save announces. One trial per arm. The other 21
types were not tested one at a time; a delete, a rename and an IR import were
not measured; and on CorOS 4.1.0 the question is unmeasured.

`QuadCortex._hello()` does the same as Cortex Control with one difference: it
issues no host `Version` read, because a redundant read would race a caller's
later `version()` (read replies carry no `request_id`). Since ADR-0020,
`connect()` itself makes one `Version` read before `_hello()` to resolve the
profile. Measured 2026-09-07: a `connect()` and its burst carry exactly three
inbound `Version` messages, the full reply (15 fields, at +0.71 s), the unit's
own `Version{READ}` about 1 ms behind it, and the answer to the announce
(carrying `cortex_control_version_valid`, at +0.73 s).

### 4.3 Keepalive and disconnect

Cortex Control sends `KeepAlive{action: UPDATE}` about once per second. The
library sends every 5 seconds; the unit tolerated 20-second idle gaps in the
capture, so the interval is not critical. On quit, Cortex Control sends
`Connection{connected: false}`, and so does this library, as the first step of
teardown.

Abandoning a session without the goodbye leaks nothing observable. Measured: 12
sessions opened and abandoned, then the seed push still arrived, subscriptions
still fired, and `read_preset` took the same time. Handshake duration is a poor
instrument for this: six clean sessions spanned 2.03 s to 3.80 s. Not established: whether the
unit supersedes an old session on a fresh `ResetCommsBuffers`, reaps sessions
whose keepalives stop, or has a ceiling on accumulated sessions. The goodbye is
sent because it matches Cortex Control, not because a fault was found.

### 4.4 A `Version` read is answered twice

Measured 2026-09-03. A host `Version{READ}` is answered by the full `UPDATE` and,
1 ms later, by the unit's own `Version{READ}` carrying `action` alone. Neither
carries a `request_id`, and `Transport.request` correlates by type when no id is
present, so the second message is handed to whichever `Version` waiter is
registered when it lands. Five `version()` calls in a row returned full, empty,
full, empty, full.

`version()` therefore accepts only a `Version` carrying an identity field,
`device_serial_number` or `app_fw_version`. A partial reply carrying one of the
two is still returned, because the cache keeps what the unit sent and re-reads for
the rest. A sparse host `Version{UPDATE, custom_name}` (a rename) is echoed once
with the same two fields and does not re-run the handshake gate. `connect()`
waits for a reply carrying both `device_type` and `zenos_git_hash`, inside the
same `handshake_patience` budget.

### 4.5 Settle, and the seed push

After the burst the unit needs a moment before it treats the client as connected;
a command sent too soon gets no push. `connect(settle=...)` waits 2 seconds by
default.

The `RecallPreset` subscription produces a **seed push** of the loaded preset,
delivered lazily. Any code waiting for a preset push must be able to ignore it
(section 5). The whole burst is measured in section 12.

## 5. Request/response correlation

Every host message carries an incrementing `request_id` (field 2 on most types).
Correlating replies is less simple than that suggests:

- **Read replies carry no `request_id` echo.** A `Version` read comes back with
  no id.
- **A state-changing request triggers a cascade of other-type messages that all
  echo its `request_id`.** Recalling a preset produced `UndoRedo`, `Grid`,
  `Scene` and `RecentsFavorites` all carrying the recall's id, plus an echo of
  the `SetlistPosition` itself.
- **Some answers are pushes of a different type** emitted in reaction to the
  action: the `RecallPreset` push, the `File` folder listings.

So correlation is by message type first, with `request_id` as a consistency check
when both sides carry one (`transport._dispatch`).

**`RecallPreset` pushes echo a host recall's `request_id`.** A host recall makes
the unit echo that id on the `RecallPreset` push. An unsolicited push (the
handshake seed, or a recall performed on the unit) carries no `request_id`.
Without matching on the id, a reader returns whichever `RecallPreset` arrives
first, which lags by one whenever an earlier push is still in flight.
`read_preset` tags its recall with a fresh `request_id` and accepts only the push
echoing it.

## 6. Addressing presets

### 6.1 Setlist paths

Setlists are addressed by their filesystem path on the unit:

| Setlist | Path | `is_factory` |
|---|---|---|
| User ("My Presets") | `/media/p4/Presets/My Presets` | `false` |
| Factory Library | `/opt/neuraldsp/Factory Library/` | `true` |

The factory path carries a trailing slash on a recall, and the unit reports the
same folder's listing key as `/opt/neuraldsp/Factory Library` without it. Code
matching a pushed `folder.key` against a setlist constant must normalize the
slash on both sides; comparing them raw matches nothing. `enums.Setlist` carries
both strings.

Presets exist as files inside the setlist directory, named
`<setlist path>/<preset name>.pb`. That path is how `File` `DELETE` and `MOVE`
address a preset. User setlists are siblings under `/media/p4/Presets`, not
children of "My Presets" (section 10).

### 6.2 Slot names and linear positions

The unit shows presets as bank-plus-letter names such as "28C". On the wire a
preset is a zero-based linear position:

```
position = (bank - 1) * 8 + letter_index      where A = 0 ... H = 7
```

So "28C" is 218. Confirmed against captured traffic. A setlist holds 256 slots
(32 banks of 8). Slot names are mode-dependent: under a `PRESET`-containing
`HYBRID` mode a bank holds 4, so linear position 5 reads "1F" normally and "2B"
there. `slot_to_position` speaks the non-hybrid naming.

## 7. Presets and the grid

Field paths use `{}` for nested submessages. Every shape in this section was seen
on the wire unless noted.

### 7.1 Recall a preset

```
SetlistPosition{action: UPDATE,
                folder_key: "/media/p4/Presets/My Presets",
                position: 218,
                is_factory: false}
```

For a factory preset, `folder_key: "/opt/neuraldsp/Factory Library/"` and
`is_factory: true`. `RecallPreset` is not the recall request; the unit uses that
type to push the preset.

### 7.2 Read a preset

**There is no host-initiated "read this slot" request.** A `Grid` or
`RecallPreset` read of a slot gets no reply. Whenever a preset is recalled, by the
host or on the unit, the unit pushes:

```
RecallPreset{action: UPDATE, preset: <BinaryPreset>, reason: <RecallPresetReason>}
```

The `preset` field carries the full preset (about 21 KB for a four-chain,
eight-scene preset), usually gzip-compressed at the frame level. Consequences:

- reading a stored preset recalls it, which loads it onto the grid, discards
  unsaved edits, resets the active scene, and interrupts the audio;
- the unit services the push lazily (10 to 25 seconds observed), so timeouts
  must be generous (the library uses 40 seconds);
- the push is correlated by `request_id` (section 5).

`RecallPresetReason.Enum` is `OTHER = 0`, `UNDO = 1`, `SAVE = 2`. A host recall
and a read reply carry `OTHER`; the push a save emits carries `SAVE`; `UNDO` has
not been observed, and which value accompanies a footswitch recall on the unit is
not measured. `RecallReason` names them.

**The live grid is readable without a recall.** `RecallPreset{READ, request_id}`
answers with the preset as it is right now, unsaved edits included, with no side
effects: the unsaved edit survives and the active scene does not move.
`read_current_preset()` wraps it. `Scene{READ, request_id}` answers with
`selected_scene`; `active_scene()` wraps it. `SetlistPosition{READ, request_id}`
answers with `folder_key`, `position` and `is_factory` in 3 ms (measured
2026-08-15); `loaded_position()` wraps it. A `SetlistPosition` read must never
carry a slot, because an `UPDATE` that does is a recall.

**Every recall interrupts the audio**, including a redundant recall of the preset
already loaded. Measured by ear across four consecutive recalls: all four cut the
sound, and only the duration varied. A verify-by-re-reading loop built on
`read_preset()` stutters a rig on every iteration, and a `read_preset` between
`switch_scene` and a scene-targeted write silently retargets that write at the
default scene. Inspect with `read_current_preset()` while editing.

### 7.3 The pushed preset structure

A `BinaryPreset` from a `RecallPreset` push is structural:

- `chains`, and `models` within them, are identified by `hash`, with `column`
  and parameter `index` implied by position rather than stored;
- chains read back from a recall carry no explicit `row`, so a chain's grid row
  is its index in `chains` (`input_chain_rows()` relies on this);
- `param_values` are present and round-trip exactly;
- `in_portid`, `out_portid`, scene labels and scene colours are present;
- per-scene bypass state lives in the separate `bypass` list, positional by row
  then column then scene.

`Model.column` also comes back unset on a recalled preset. Grid position is
inferred from ordering. See `protocol/proto/Preset.proto`.

**Every row reports all 8 column slots.** Empty ones arrive as `Model` entries
whose `hash` is absent or zero, so `len(chain.models)` is 8 for every row and is
not a block count. `output_control` and `input_control` are padded the same way,
one entry per row. `in_portid == EMPTY` is not an occupancy signal either: it
means "not fed from a physical jack", the normal state of any non-input row. Use
`protocol.blocks(preset)` to iterate the cells that hold something.

**`splitter`, `mixer`, `combined_splitter` and `split_control_points` exist only
on rows 0 and 2.** A branch can only originate on an even row, with its parallel
lane on the row below. Counted across all 68 rows of 17 factory presets:

| collection | row 0 | row 1 | row 2 | row 3 |
|---|---|---|---|---|
| `models` | 136 | 136 | 136 | 136 |
| `output_control` | 17 | 17 | 17 | 17 |
| `input_control` | 17 | 17 | 17 | 17 |
| `splitter` | 17 | 0 | 17 | 0 |
| `mixer` | 17 | 0 | 17 | 0 |
| `combined_splitter` | 17 | 0 | 17 | 0 |
| `split_control_points` | 17 | 0 | 17 | 0 |

`Splitter(row)` and `Mixer(row)` raise `ValueError` on an odd row.

**`param_values` can contain NaN.** Factory "Strat Ambience" (05B), "Classic
Pedalboard" (07C), "Rols Jazz" (09A) and "Major Strat Vibes" (10B) store NaN at
several indices across several scenes, and it round-trips a save unchanged. Since
`nan != nan`, a preset compared field by field against itself reports
differences. `params_equal()` treats NaN as equal to NaN.

### 7.4 Grid edits and the edit path

The grid is 4 rows by 8 columns. Grid edits are `Grid{action: UPDATE, preset:
<sparse BinaryPreset>}` messages, and this is the most important behavioural
fact in the protocol:

> **The unit applies a `Grid` `UPDATE` by locating each chain and model by its
> `row` / `column` key, and a save snapshots whatever is currently on the grid.**

Two consequences, both confirmed by discriminating experiments:

1. **A full-preset `Grid` `UPDATE` is not applied.** A preset from a
   `RecallPreset` push carries no explicit `row` on its chains, so a wholesale
   write-back has nothing to key on and is silently dropped.
2. **`File` `CREATE` ignores `preset_payload`; it snapshots the grid.** With the
   grid on a factory preset (`in_portid = 1`), a `CREATE` whose `preset_payload`
   carried `in_portid = 2` saved a slot that read back `in_portid = 1`. The
   `files[].name` is taken from the message, so the slot gets the right name, but
   the content is always the current grid. Cortex Control's own "Save As" sends no
   payload.

So the edit flow is always:

```
recall the preset            (loads it onto the grid)
  -> one or more row/column-keyed sparse Grid UPDATEs
  -> save                    (File CREATE, which snapshots the grid)
```

`examples/reroute_and_save.py` is the smallest end-to-end demonstration.

**Chain input routing:**

```
Grid{action: UPDATE, preset{chains{row: 0, in_portid: 4}}}
```

**Row output routing** is the mirror, `chains{row, out_portid}`, and survives a
save and recall. **The unit does not assign an output automatically.** A row
given blocks and a physical input keeps `out_portid` at 0, so setting the output
is a requirement when building a chain on a fresh row. The unit stores any id
without validating it: 5, 6, 7, 8, 9, 11, 20 and 22 were all accepted and read
back verbatim. Values 16 to 18 are internal row-to-row routing (`NEXT_ROW_*`);
19 (`MULTIPLE`) is a real destination and what factory presets use for the
Multi-Out ("Brit 2203" has `16` on row 0 feeding the next row and `19` on row 2);
0 is unrouted; 1 to 15 reach jacks or USB.

**Block parameter:**

```
Grid{action: UPDATE,
     preset{chains{row: 0,
                   models{column: 1,
                          params{index: 1,
                                 param_values[scene]{float_value: 0.4553}}}}}}
```

`float_value` is normalized 0..1. A knob drag in Cortex Control streams one
`Grid` `UPDATE` per step. `params.index` is positional within the model's
parameter list, and not every index is a visible knob.

**Block bypass:**

```
Grid{action: UPDATE,
     preset{bypass{row: 0,
                   colBypass{column: 4,
                             sceneBypass[scene]{bypass: true}}}}}
```

**A bypass update must not carry a `chains` element.** The same `bypass` group
sent with an otherwise-empty `chains{row}` beside it made the unit ignore the
whole message (observed as a controlled A/B, twice). A sparse update carries
exactly the one thing it means to change.

**A `ParamValue` can carry a `string_value`.** Cab microphone selection uses it
(`string_value: "NG_212 DG Neo_Condenser U47"`), and a host write of the same
shape persists.

**Per-scene values.** `params{index, param_values[scene]{float_value}}` reads as
though the index selects a scene. It does not. Three facts, each established by
controlled experiment with a save and read-back:

- **`param_values[0]` is applied to whichever scene is active.** Entries past
  index 0 are ignored. Padding `param_values` up to a scene index is destructive:
  the entries below carry protobuf defaults, the unit reads index 0, and the
  parameter ends up 0.0 in every scene. Nothing in this library pads.
- **Only a parameter whose `Param.scene_mode` is set keeps per-scene values.**
  Without it the parameter has one global value that appears in all eight scenes.
- **`scene_mode` is host-writable only when sent alone.** A `Grid` update
  carrying both `scene_mode` and a `param_values` entry is treated as a plain
  value write and the flag is dropped.

So a per-scene parameter write is three messages, and ordering over the pipe is
enough:

```
Grid{UPDATE, chains{row, models{column, params{index, scene_mode: true}}}}   # flag alone
Scene{UPDATE, selected_scene: N}                                            # sit on the scene
Grid{UPDATE, chains{row, models{column, params{index, param_values{value}}}}}
```

It works identically in `chains[].output_control[]`, which is how a "silent
scene" is built. `set_param(scene=...)` issues exactly this for every target that
has scenes.

**Reading a scene-following parameter is not symmetric with writing it.** In a
stored preset `param_values[N]` is scene N, not the active one. A reader that
takes `[0]` is reading scene A. Check `Param.scene_mode` and index by the scene
you mean; `protocol.param_state(preset, cell, index)` hands back all eight with
the flag.

**Bypass semantics.** A preset stores a full 4x8 bypass table,
`bypass[row].colBypass[column]`, positional (the stored entries leave `row` and
`column` unset, so filtering on `column` matches nothing). Each cell holds
`sceneMode` and eight `sceneBypass` slots. Measured:

- **`sceneMode` false: one global state.** A single-entry bypass write lands on
  all eight stored slots at once.
- **`sceneMode` true: the write lands on the active scene's slot.**
  `sceneBypass[0]` means "the active scene", as for `param_values[0]`. Entries
  beyond `[0]` are ignored; an index-3 `true` did not reach scene D. To bypass a
  block in one scene, switch to that scene and write index 0, which
  `set_bypass(scene=...)` does.
- **`sceneMode` is not host-writable.** Sent alone and sent beside a bypass
  entry, both ignored. The flag has no presence, so disabling it could not be
  expressed on the wire even if the unit honoured the write. Factory content
  arrives with it set on some blocks (5 of 32 positions in one preset). A block whose `sceneMode` is false can appear from
  its `sceneBypass` array to differ between scenes while the unit shows it the
  same in all of them; filter on `sceneMode` before comparing scenes.
- **The bypass table persists for empty cells.** A freshly placed block inherits
  whatever bypass state the preset last stored at that cell. Read the cell after
  placing.
- **A bypass written to a Neural Capture block before the preset's first save
  does not survive that save**, while an ordinary block in the same row keeps it.
  On the live grid the capture bypasses like any other block, and parameters
  written after the load do survive that first save; only the bypass does not.
  The sequence that persists, verified on 24 presets: save, recall the stored
  slot, write the bypass again, save again. Re-saving the same name to the same
  slot does not trigger `_N` renaming. Verify a bypass against the stored preset
  (`bypass_state()` on a `read_preset()` result), not only the live grid.

**`Chain.row` and `Model.column` are zero-based on the wire, and the screen
labels rows 1 to 4.** `chains[0]` is the top row.

### 7.5 Blocks: place, remove, move, refuse

A grid cell holds a model id (`BinaryPreset.Model.hash`); `0` means empty. Both
edits use the row/column-keyed sparse `Grid` update, and the action is what
distinguishes them:

- **Create or replace:** `Grid{UPDATE, chains{row, models{column, hash}}}`. The
  unit makes no distinction between filling an empty cell and overwriting one.
- **Remove:** the same shape with `action: DELETE`. An `UPDATE` with `hash: 0`
  does nothing; the zero is transmitted (`hash` has presence) and the firmware
  treats it as "no model specified".

Both confirmed twice: driven from the host with a read-back, and by watching the
unit's own push when the same edit is made on the touchscreen, which for a
delete is exactly `Grid{action: DELETE, chains{row: 0, models{column: 2, hash: 0}}}`.

**Move.** `GridMove{move{from_row, from_col, to_row, to_col, is_drop}}` is
drivable host to unit; row 2 column 1 to column 7 moved that block and left every
other cell where it was. A cross-row move creates a parallel path, as dragging a
block from path A to path B does on the screen: moving row 0 column 6 to row 1
column 6 on the serial "Brit 2203" left the unit reporting
`Split(row=0, split_column=0, mix_column=7)`, computed by the unit. The message
also takes an optional `grid` snapshot of every row's model ids, which is
advisory: replaying it with a cell zeroed does not delete a block, and the unit
echoes back only the `move` element. This library sends only the move.

**A placement can be refused, for two known reasons, and both are silent.** A
preset has a finite processing budget, and a block that does not fit is accepted
on the wire and simply is not there afterwards. `GenericError` carries only cloud
and version codes, and `CompilerInhibitedModules` carries only two global
booleans (`global_gate`, `global_eq`).

Confirmed and deterministic: adding a
six-block chain to a free row of "OneStar Clean Tweed" (02C) placed five and
dropped the bass cab (21005), and the cheaper EQ placed after it in the same chain
landed. A port conflict is the second cause and looks identical from the host:
placing an `FX Loop 1` where a `Send 1` already claims the same physical send is
refused, and the unit puts a modal on its own screen that no message carries
("Port Conflict / Send is used as an output by FX Loop Send 1 on path 2"). It
stays refused until dismissed on the unit (observed 2026-08-26). An `FX Loop 2`
beside `Send 1` places fine: the specific port collides, not the block type.

**The refusal is detectable without saving.** The unit echoes a `Grid` push
naming each cell it accepts, plus an `UndoRedo`; a refused block produces neither:

| placed on 02C row 1 | `Grid` echoes | `UndoRedo` | read-back |
|---|---|---|---|
| Eltron 30 (capture) | 3 | yes | landed |
| Capture 2 | 3 | yes | landed |
| Graphic-9 | 3 | yes | landed |
| Solid State Comp (M) | 2 | yes | landed |
| **212 Darkglass Neo (M)** | **0** | **no** | **missing** |
| Parametric-8 | 2 | yes | landed |

Echo latency was 0.29 to 0.42 s over six accepted placements, and a repeat write
to a cell that already holds that model echoes too. A missing echo is not on its
own a refusal: `set_block(verify=True)` reads the preset back before deciding,
and raises `BlockRefused` only when the cell really is not holding the model.

**DSP load is not readable.** `CPULoadMessage` (type 26) exists and the RX path
decodes it, but a bare `CPULoad{READ}` times out, adding it to the connect
burst's reads produces no pushes, and 40 seconds of listening saw zero. Place the
block and check the echo.

### 7.6 Splitter, mixer and branches

Both are sub-collections of a chain, alongside `output_control`:
`chain.splitter[]` holds `Splitter AB` (model `10000`) and `chain.mixer[]` holds
"Mixer" (model `11000`: `LEVEL A`, `PAN A`, `LEVEL B`, `PAN B`, `PHASE`,
`MIXER LEVEL`, `SPLIT MODE`).

**The mixer is writable** with the row-keyed shape used for `models[]`, and it
supports per-scene values. Factory presets build their scenes out of it: in
"Darkglass AO900 1" nothing is bypassed in any scene, and all eight scenes come
from per-scene `LEVEL A` and `LEVEL B` across two rows.

**The splitter is written through `chain.combined_splitter`:**

```
Grid{UPDATE, preset{chains{row: 0, combined_splitter{params{index: 3,
                                                     param_values{float_value}}}}}}
```

A separate repeated field on the same chain, carrying no hash and no column.
Writing there propagates to both representations: setting `LEVEL TO A` to 0.25
read back as 0.25 in `combined_splitter[3]` and in `splitter[0]` alike.
`chain.splitter[]` is a read-only view; a write addressed to it is accepted and
absent on read-back. Parameter indices follow the unified model 10004 (`TYPE`,
`STEREO`, `BALANCE`, `LEVEL TO A`, `LEVEL TO B`, `FREQUENCY`, `MODE`), whatever
type-specific legacy id the preset reports (10000 `Splitter AB`, 10002 `Splitter
Balance`, 10003 `LR Crossover`). Splitter parameters read back with `scene_mode`
false in the factory content examined.

**Where a row splits is readable** from `Chain.split_control_points`, whose
`split` and `mix` fields give the columns where the lane leaves and rejoins.
Neither has presence, so read them directly, as `protocol.splits()` does.
Confirmed: "Darkglass AO900 1" (27H) and "Darkglass AO900 2" (28A) report
`(split=4, mix=4)` on rows 0 and 2, and the parallel lane's single block sits at
column 4. Rows that do not branch report `-1` for both.

**A branch is marked by `split` alone, and `mix` is independent of it.** A lane
may branch and never recombine (`split >= 0`, `mix == -1`): "Strat Ambience"
(05B) reports `(2, -1)` on row 0, "Classic Pedalboard" (07C) `(7, -1)`, "Stereo
Lead" (11B) `(5, -1)`. `split` and `mix` need not agree either: 07C reports
`(3, 4)` on row 2. `Split.rejoins` answers the second question.

**A branch is created by setting the columns**, because every even row already
carries a dormant splitter, mixer and combined splitter with
`split_control_points` at `-1`:

```
Grid{UPDATE, preset{chains{row, split_control_points{split: 3, mix: 5}}}}
```

Confirmed on "Brit 2203": after the write `splits()` reported the branch and the
splitter, mixer and mute setters all drove it. Writing `-1` to both clears it, and
clearing row 0 left row 2's branch untouched.

**The row below a branch is spoken for, even when empty.** Writing to it puts
content inside the existing chain's parallel path. `protocol.free_rows()`
excludes both occupied rows and lane rows.

**Splitter and mixer `MUTE` is one control**, and not a catalog parameter of
either model. Muting the splitter on the unit shows the mixer's `MUTE` already
engaged. The write goes to `Chain.splitBypass`:

```
Grid{UPDATE, preset{chains{row: 0, splitBypass{bypass: true}}}}
```

and the unit reports the result in `Chain.mixBypass`. A write addressed to
`mixBypass` does nothing (four-trial matrix across both fields and rows 0 and 2).
Both fields are `repeated SceneBypass`, one entry per scene, and a single write
sets all eight, so this is not per-scene.

### 7.7 The lane output and input gate

**Lane Output Control** is model `23000`, in `chains[].output_control[]`, one per
row on all four rows. Parameters: `0 VOLUME` (dB), `1 PAN` (0.5 is centre),
`2 MUTE`, `3 SOLO`, and an undocumented index 4. A keyed write into
`output_control` persists like one into `models` (`PAN` 0.5 to 0.0 survived a
save and read-back). Lane `MUTE`: `1.0` is muted, by ear. `SOLO` is unmeasured in
either direction.

**Input Gate Control** is model `28000`, in `chains[].input_control[]`, one per
row. Parameters: `0 NOISE REDUCTION` (%, 0..100), `1 BYPASS` (1.0 bypasses),
`2 GAIN REDUCTION`, `3 INPUT GAIN` (dB, -24..+24, 0.5 is 0 dB), and an
undocumented index 4 that reads 0.0. All three controls are confirmed in both
directions, per-scene included (scene C held 0.9 while the other seven held 0.3).
Six distinct gate settings appear across 68 factory rows, the commonest
`NOISE REDUCTION` 0.372 with the gate engaged (39 rows).

**`GAIN REDUCTION` is a meter, not a control.** The catalog types it `grMeter`
and it is sampled into the preset at save time, so two saves of the same rig can
differ there. Exclude it from before/after comparisons; `GAIN_REDUCTION_PARAM`
names it. A host write is stored (wire 0.5 round-trips) and moves nothing on
screen.

### 7.8 Footswitches, expression pedals and options

**Footswitch (`STOMP` mode) assignments** live in `BinaryPreset.stomp_mode_assignments`,
entries of `{row, column, stomp_index}` where `stomp_index` 0 to 7 is footswitch
A to H. None of the three fields has presence, so an entry for row 0, column 0,
footswitch A arrives looking empty. One footswitch may drive several blocks.
Assigning takes two messages, which is what the unit sends:

```
Grid{DELETE, preset{stomp_mode_assignments{row, column}}}
Grid{UPDATE, preset{stomp_mode_assignments{row, column, stomp_index}}}
```

The `UPDATE` alone leaves the previous assignment in place. Three maps travel
with it and are writable the same way: `stomp_labels`, `single_stomp_labels`
(both `map<uint32, string>`) and `stomp_is_momentary` (`map<uint32, bool>`), all
keyed by footswitch index, not column (a block at column 3 assigned to footswitch
E produced `key: 4`). The unit clears all three when an assignment is removed.
Factory content leaves `stomp_is_momentary` empty, so a missing entry means
latching.

**Momentary is real, and the manual does not mention it.** The touchscreen's
Assign footswitch modal carries a Latching/Momentary toggle, and using it
announces `Grid{UPDATE, preset{stomp_is_momentary{key, value}}}`. **A momentary
write lands only on a footswitch driving exactly one block.** A write aimed at a
multi-block switch is accepted, echoes nothing, and reads back unchanged; the unit
greys out its own toggle in the same case.

**Expression pedal assignment** is a row/column-keyed parameter write using three
fields on `Param`:

```
Grid{UPDATE, preset{chains{row, models{column, params{index, expression,
                                        expression_min, expression_max}}}}}
```

`expression` is the pedal (1 or 2), 0 means unassigned and is sent, and the two
floats are the normalized ends of the sweep; min above max reverses it. Confirmed
as the unit's push when a pedal is assigned on screen and as a host write
surviving save and read-back. The unit leaves `scene_mode` alone when it assigns
a pedal, and the manual excludes an expression-assigned parameter from scene data.

**What a host can assign a pedal to.** Every `Model`-shaped collection was tested
directly, one write per target, read back after a settle and restored:

| collection | parameters tested | host assignment |
|---|---|---|
| `models` (a block) | Jewel `HIGH CUT` (`switch`) | yes |
| `models` bypass | via `bypass_expression` | yes, `set_expression_bypass` |
| `input_control` | `NOISE REDUCTION`, `INPUT GAIN` (`float`), `BYPASS` (`switch`) | yes, all three, and `BYPASS` clears too |
| `mixer` | `LEVEL A` (`float`), `PHASE` (`switch`) | yes |
| `combined_splitter` | `LEVEL TO A` (`float`), `TYPE` (`switch`) | yes |
| `output_control` | `VOLUME`, `PAN` (`float`) | yes |
| `output_control` | **`MUTE`, `SOLO`** (`switch`) | **no: silently dropped, both directions** |

The `MUTE` and `SOLO` refusal is a measured pair, not an instance of a rule.
Three rules were tried and all are false: "switch parameters are refused"
(`HIGH CUT`, `PHASE` and `TYPE` are `switch` and accept), "bypass-like parameters
are refused" (the gate's `BYPASS` accepts and clears), and "`output_control`
rejects `expression`" (`VOLUME` and `PAN` accept). `output_control.bypass_expression`
is not a back door either. The unit announces nothing when a lane output is
edited (180 seconds of listening), so the differential capture ADR-0010 requires
was run: between `MUTE` assigned and unassigned, the only device state that moved
was `PresetDirty.is_dirty`, the free-storage counter, and the running metronome
clock. On the preset side the assignment changed exactly `params[2].expression`
and `expression_bypass_info[0]`. `ControlNotDrivable` carries this.

**Expression bypass** writes both halves in one message:
`models{column, bypass_expression{expression, expression_min, expression_max},
expression_bypass_info{type, invert, delay_ms, latch_emulation}}`. Confirmed
round-tripping pedal 1, type 1, invert, 250 ms and latch emulation. `type` is
`STOP = 0`, `SWITCH = 1`, `HEEL_TOE = 2`, not the manual's listed order,
established by setting each deliberately with a scene change fencing them apart.
It explains the unit's `SWITCH ON` control cycling numerically. The mode decides
which of the other controls exist: `SWITCH` greys out `SWITCH DELAY`, `HEEL_TOE`
greys out `LATCH EMULATION`. `output_control` pre-allocates two
`expression_bypass_info` slots, `MUTE` at `[0]` and `SOLO` at `[1]`;
`input_control` carries one; an ordinary block carries none until
`set_expression_bypass` adds it. What generates the pre-allocation is not
established; `switch`-typed block parameters carry no slot.

**A list (comboBox) parameter stores `index / (count - 1)`.** Confirmed in both
directions: setting a side-chain `SOURCE` to "Input 2" on the unit stored `0.2`
out of 16 options, and a host write of 3/17 on a block whose list held 18 options
read back as the same choice. For a fixed list the catalog's `steps` is the count
and `stepNames` are the names (section 14). For the twelve `dynamic` parameters
whose list enumerates the preset's blocks, the names are in the preset's
`Param.dynamic_steps`, with `dynamic_icons` alongside, and the count is that
list's length. On a "Solid State Comp (S/C)" the side-chain `SOURCE` is an
ordinary `comboBox` at index 6; `Model.sidechain_source_flag` is bookkeeping and
a host write of it does nothing. A Doubler's `TRIGGER` list reads:

```
Off, Follow Input, Input 1, Input 2, Input 1/2, Return 1, Return 2, Return 1/2,
USB input 5..8, USB input 5/6, USB input 7/8, <blocks ahead of this one>
```

**Adding a block rewrites dynamic comboBox values on rows you never wrote to.**
The selected index stays put; the denominator moves. On "US TWN Vibrato" (01C),
each case recalled, edited on row 1, saved to a scratch slot and read back:

| case | `TRIGGER` reads | blocks |
|---|---|---|
| shipped | 0.0526316 = 1/19 | 13 |
| recall and save, no edit | 0.0526316 = 1/19 | 13 |
| add 1 block | 0.0500000 = 1/20 | 14 |
| add 4 blocks | 0.0434783 = 1/23 | 17 |

The denominator is `blocks + 6`. A fixed 5-option `WAVEFORM` in the same preset
came back byte-identical. Comparing untouched rows against what shipped therefore
fails on a row never addressed; `params_equal(option_count=...)` compares by
selected option.

### 7.9 Scenes

Scene indices are zero-based (scene A is 0). A preset has 8 scenes.

```
Scene{action: UPDATE, selected_scene: 1}                          # select
SceneLabel{action: UPDATE, index: <0-7>, label: "<name>"}         # rename
SceneColor{action: UPDATE, index: <0-7>, color: <ARGB uint32>}    # recolour
SceneCopy{action: UPDATE, from_index: 0, to_index: 3, is_swap: false}
```

`color` is ARGB; a pinkish scene was `0xFFFF02C2`, and it round-trips exactly.
On any scene edit performed on the unit, the unit re-sends all 8 labels and
colours. A host label write was observed echoing only the index it wrote, as two
identical messages (one capture).

`SceneCopy`'s action is `UPDATE`, not `COPY`. Cortex Control cannot copy a scene,
so the shape was read off the unit's own push when a scene was copied on the
touchscreen, and sending it host to unit is confirmed. `from_index` is honoured
(copying B onto D produced B, not A, on a preset whose A and B differ), `is_swap`
exchanges the two scenes, and **the label and colour travel with the state** in
both modes: `copy_scene(E, B)` on factory 28A moved both the label
`'Clean +VMT'` and the colour `0xff45f862` onto scene B. A caller reproducing a
scene map gets the colour for free.

**An unlabelled scene is a single space, not an empty string.** 34 of the 136
scene labels across 17 factory presets are `" "`. So `label.strip()` is the
blank test, `SCENE_UNLABELLED` holds the space, and `set_scene_label(index, None)`
sends it.

**Default scene.** No field on the `File` message carries it. The unit records
whichever scene is active at save time, so `save_current_preset(default_scene=...)`
switches first.

## 8. Per-preset tempo and the metronome

Each preset carries a `TempoControl` block (model `25000`) in
`BinaryPreset.tempoProgramData`, a repeated field holding one entry, with 24
parameters. **It is writable, even though it is not row or column keyed**: a
`Grid` `UPDATE` carrying `tempoProgramData{params{index, param_values}}` is
applied and survives a save and recall, with the hash optional. Confirmed:
`LED LIGHT` 1.0 to 0.0 turns the tempo LED off, and `VOLUME` to 0.0 takes the
metronome to the bottom of its knob (-60 dB, quiet but still audible; `MUTE` is
parameter 4).

In the stored preset these params are positional: all 24 arrive with `index`
absent. A host write sets `index`. `protocol.tempo_params()` reads them.

**The Tempo menu's parameter indices**, mapped by using each control on the unit
in a named order:

| index | control on screen | catalog name | notes |
|---|---|---|---|
| 0 | `TEMPO` | `TEMPO` | 40..240 bpm (`MIN_TEMPO`, `MAX_TEMPO`, `steps=201`) |
| 1 | none, in the preset copy | `TYPE` | stayed 0.0 through every flip. In the device copy the same index is `MODE`; see below |
| 2 | Tempo LED | `LED LIGHT` | |
| 3 | Volume | `VOLUME` | -60..+9 dB; wire 0.0 is -60 dB, quiet but audible |
| 4 | **the unit's `MUTE`** | `START` | `PLAYBACK` in the manual. `1.0` is audible, `0.0` is muted: inverted against the label a player sees. Traced by pressing the unit's own button. There is no start/stop control; the transport always runs |
| 5 | Pan | `PAN` | |
| 6 | Time Signature | `TIME SIGNATURE` | 21 options |
| 7 | Subdivisions | `NOTELENGTH` | 4 options |
| 8 | Sound | `SOUND` | 6 options |
| 9 | Routing | `ROUTING` | 5 options; a lane mute does not silence the metronome, which bypasses lane outputs |
| 10 to 22 | the per-beat cells, beat 1 first | `STEPSTATE0` to `STEPSTATE12` | |
| 23 | none | absent from the catalog | unattributed |

The catalog describes 23 parameters for model `25000` and the preset carries 24.
Two names disagree with the screen (indices 4 and 7), hence `targets.Tempo.NAMES`.
`"MUTE"` is refused by the `Tempo` target because honouring it would do the
opposite of what a caller means; `set_metronome_muted()` and
`set_metronome_running()` exist instead.

**Two parameters called `MUTE` have opposite polarities:**

| control | wire | `1.0` means |
|---|---|---|
| Lane Output `MUTE` | `output_control` param 2 | muted |
| The unit's Tempo-page `MUTE` | `tempoProgramData` param 4 | audible |

**The four list controls** store `index / (count - 1)`, read off the unit's own
dropdowns with the order confirmed by selecting the last entry and seeing the wire
store exactly 1.0:

| control | options, in order |
|---|---|
| `SUBDIVISIONS` (4) | `1/4`, `1/8`, `1/8T`, `1/16` |
| `ROUTING` (5) | `MULTI`, `HP`, `OUT 1/2`, `OUT 3/4`, `SEND 1/2` |
| `SOUND` (6) | `BLIP`, `BLOCK`, `COWBELL`, `DIGITAL`, `DRUM KIT`, `SOFT KIT` |
| `TIME SIGNATURE` (21) | `2/4` to `13/4`, then `3/8`, `6/8`, `9/8`, `12/8`, then `5/8 (3+2)`, `5/8 (2+3)`, `7/8 (3+2+2)`, `7/8 (2+3+2)`, `7/8 (2+2+3)` |

They are the enums `TempoSubdivision`, `MetronomeRouting`, `MetronomeSound` and
`TimeSignature`.

**The per-beat cells: indices 10 to 22 are beats 1 to 13**, each a four-option
list at `option / 3`, with the unit's own names from the catalog's `stepNames`:

| wire | option | name | sounds like | drawn as |
|---|---|---|---|---|
| 0.0 | 0 | `OFF` | the plain click | solid circle |
| 0.333 | 1 | `MUTE` | silent | outlined circle |
| 0.667 | 2 | `DOWN` | the big accent | solid circle, dot above |
| 1.0 | 3 | `ON` | a small accent | solid circle, dot below |

Driven on the unit 2026-08-27, one bar at 60 bpm with all four states on the four
beats, listened to and looked at, and driven again on 2026-09-14. `OFF` and `ON` are about the accent, not about
whether the beat sounds. A cell cycles up by 1/3 and wraps after four touches.
Changing the time signature rewrites these: selecting 7/8 (2+2+3) wrote indices 6,
12 and 14 together. Set the signature before the beats. All 13 exist whatever the
signature is; beats past the current signature are stored and not sounded. How
many beats a compound signature sounds has not been measured.

### 8.1 `MODE` is the device tempo block's parameter 1

Found 2026-08-12. The unit keeps two tempo blocks: the preset's in
`BinaryPreset.tempoProgramData` (24 parameters) and the device's in
`GlobalTempo.params` (25). `GlobalTempo.params[1]` is the Tempo menu's `MODE`
switch:

| wire | the menu shows |
|---|---|
| `0.0` | `PRESET` |
| `1.0` | `GLOBAL` |

Readable by `GlobalTempo{READ}` and writable by
`GlobalTempo{UPDATE, params{index: 1, param_values}}`. `QuadCortex.tempo_mode()`
and `set_tempo_mode()` are the operations. The switch moves neither block; on the
unit measured they disagreed at indices 2, 3 and 9 as well as the tempo (111 bpm
from the preset's `0.355` against 120 from the device's `0.400`, heard and seen).
A host write to parameter 1 left the preset's own parameter 1 at `0.0`, so the
scope of the write is known. It is a device setting: it affects every preset and
there is nothing to save.

Confirmed three ways: the wire value moved and moved back with nothing else in
the unit's readable state moving; a host write moved the unit's own menu, watched
at the unit; and the tempo in effect switched between the two blocks' stored
values. The method was a differential state capture of twelve readable types plus
a 14-second tap of everything pushed, in each position (ADR-0010,
`tests/hardware/state_snapshot.py`). Two negatives from it: `GeneralSettings` was
identical in both positions and no message carried an unknown field number, so
the mode is not in the settings bag; and `BinaryPreset.tempo` (field 10) was
absent in both positions with the preset's whole `tempoProgramData` block
identical across the flip, so the preset does not carry it either. Index 24 of the
device block (absent from the preset's 24 and described nowhere) held `0.0` in
both and is still unattributed.

**The unit emits no change event when the switch moves.** Three listening runs
established that, the last two with 70 of 72 types decoded and a liveness
heartbeat over 420 seconds. The value rides the ambient params-shaped
`GlobalTempo` push, which arrives about twice per 14 seconds whether or not anyone
touches the switch.

**`GlobalTempo` alternates two shapes**, one push carrying `metronome_status`
(the running clock) and one carrying the 25 params. A reader must match on
content and wait for the shape it needs; the params shape arrives about once
every seven seconds. `GlobalTempo` does not echo `request_id`, so a read straight
after a write can return the previous value: the hardware suite waits ten seconds.
`MetronomeStatusUpdate` carries only `is_enabled` and `preroll_enabled`, no mute
or level.

**Some block parameters mirror a preset-level setting.** Writing tempo parameter
4 also changes a Looper X block's `METRONOME MUTE` (param 21), in the same burst,
same value, so 1.0 there also means audible. A diff of rows you did not touch will
see it. A mirror proves two parameters are linked and nothing about meaning.

## 9. Preset-level settings

### 9.1 Per-preset MIDI Out

A preset can send MIDI when a footswitch is pressed, when an expression pedal
moves, and when the preset loads. The preset stores these in
`BinaryPreset.midi_messages` (on load), `midi_messages_general_v2` (footswitch
and expression) and the legacy `midi_messages_general`, **but a `Grid` update
carrying any of those fields is accepted and ignored.** They are applied by
`MIDISettings` (type 8):

```
MIDISettings{UPDATE, general_midi_messages{messages{source: 0,
                        msg{type, channel, param1, param2, param3}}}}
MIDISettings{UPDATE, preset_load_messages{messages{msg{...}}}}
```

A `MIDISettings` read gets no reply, so verify by reading the saved preset.

`GeneralMIDIMessage.source` is 0 to 7 for footswitches A to H and 8 to 9 for the
two expression pedals. `midi_messages_general_v2` is 10 sources by 12 messages
with a stride of 12, so source N starts at slot `N*12`. The unit mirrors each
source's first message into the 10-slot legacy `midi_messages_general`. Types,
each confirmed by entering the message on the unit and reading the saved preset:

| type | meaning | param1 | param2 | param3 |
|---|---|---|---|---|
| 1 | CC (footswitch) | CC number | value | none |
| 1 | CC (expression source) | CC number | sweep min | sweep max |
| 2 | CC Toggle | CC number | min | max |
| 3 | PC | bank MSB (CC#0) | bank LSB (CC#32) | program |

### 9.2 Preset fields that are not writable

Tried and refused:

| field | attempted | result |
|---|---|---|
| `BinaryPreset.author_name`, `description` | `Grid` update carrying them | ignored. The unit stamps `author_name` from the signed-in Cortex Cloud account on every user save |
| `BinaryPreset.volume`, `pan` | `Grid` update; `ProductData.gain` on the `File` save | both ignored, and the unit has no control for them: they read 1.0 and 0.5 on every preset examined |
| `BinaryPreset.scene_tempo` | `Grid` update with eight values | ignored, reads back empty. The unit has no per-scene tempo |
| `Model.sidechain_source_flag` | `Grid` update, row/column keyed | ignored, reads back false. The `SOURCE` is a `comboBox` parameter (section 7.8) |
| `BinaryPreset.tags` | `ProductData.tags` on the `File` `CREATE`, a `File` `UPDATE`, a `Grid` `UPDATE` | ignored; a saved preset has no tags at all |

**Tags are not preserved by any save path, including the unit's own.** Factory
presets carry tags on the wire (01C reads `['Guitar', 'Clean', 'Crunch']`), and
the unit's own Save As produced a copy with none. So tags exist only in Neural
DSP's build chain, and a preset derived from a factory one is simply untagged. The
instrument category is separate and survives: it lives on the listing entry
(`ProductData.instrument`), and the unit's picker maps to `Instrument` (Guitar 1,
Bass 2, Synth 3, Vocal 4, Other 5, each confirmed by setting it on screen).

## 10. File operations and the Directory

### 10.1 Enumerate

There is no host-initiated "list" request. A `File{action: READ}` makes the unit
push one `File` message per folder:

```
File{folder{key: <setlist path>, is_factory, files: [ProductData, ...]}}
```

Each `ProductData` carries `index` (the linear slot), `name`, `instrument`, and
metadata (`author`, `coros_version`, `date`, `cloud_id`). The factory listing
arrives gzip-compressed at the frame level. What to expect:

- **A single `File` read pushes every folder the unit knows**, not only the
  setlist of interest. Match on `folder.key`, normalizing trailing slashes.
- **A setlist always lists its full 256 slots.** Empty slots appear as entries
  with an `index` and no `name`.
- **A listing that arrives is complete.** Five reads against an 18-preset setlist
  each produced a full 18; duplicate pushes carry identical contents.
- **A read does not reliably produce a listing promptly.** Two of those five saw
  nothing within 8 seconds. A timeout means "ask again", not "the setlist is
  empty"; `wait_for_listing()` does this.
- **The unit pushes empty folder messages** for keys with no contents, so require
  `len(folder.files) > 0`.

**Listings are eventually consistent, and the lag scales with the number of
mutations.** After one `DELETE` or `MOVE`, a listing within 2 seconds can show
the old state and 5 seconds was reliable. After deleting eleven presets, a listing
5 seconds later still returned all eleven. Poll until the listing settles;
`wait_for_listing()` waits for a predicate or for two consecutive identical
listings.

**The folder tree.** On the observed unit **399 folders** arrive over about
fifteen seconds:

| key | name | contents |
|---|---|---|
| `/media/p4/Presets/My Presets` | My Presets | 256 slots, the only user setlist present |
| `/opt/neuraldsp/Factory Library` | Factory Library | 256 slots, all occupied |
| `local_nc_root` | Captures Library | **2062** factory captures |
| `NNN_f` (176 of them) | an amp name | that amp's captures (`106_f` is "Darkglass VMT") |
| `/opt/neuraldsp/impulse_responses` | none | 588 plugin-asset IRs the unit cannot load |
| `/opt/neuraldsp/Plugins/<plugin>/Artists/<artist>` | artist name | that plugin's artist presets |
| `local_ir_root`, `2_q`, `cloud-0-1`, `cloud-2-1` | IRs Library, My IRs, cloud | empty here |

Every one of those keys works with `list_presets`, confirmed for `106_f` and a
plugin artist folder; `list_folders()` discovers them.

**`FileMessage.type` is a category selector**, attributed by `request_id`
(without that, replies from earlier requests contaminate the counts):

| `type` | what it lists |
|---|---|
| 0 | presets: 223 folders, the setlists and user folders |
| 1 | IRs: `local_ir_root`, `2_q` ("My IRs", `is_user_default`), `/opt/neuraldsp/impulse_responses` |
| 2 | captures: `local_nc_root` (2063), plus per-product folders |
| 3+ | nothing |

The connect burst enumerates `0 -> 2 -> 1`.

### 10.2 Save, delete, move

All three use `FileMessage` with `type: 0`.

**Save As** (action `CREATE`, the default, so omitted):

```
File{type: 0,
     folder{key: "/media/p4/Presets/My Presets",
            is_factory: false,
            files{index: 220, name: "My Preset", instrument: 2}}}
```

The target slot is the linear index, the name comes from the message, and there
is no preset payload: the unit saves what is on the grid (section 7.4). The
20-character name limit in Cortex Control is a UI limit. A save accepts any folder
key: recalling a factory preset and saving it into `/media/p4/Presets/probe` put
it there.

**The unit renames a saved preset if the name collides.** A unique name is stored
verbatim (a 36-character name came back intact). A name that already exists in
that setlist is de-duplicated: the base is truncated and a `_N` suffix appended,
to 20 characters in total (`Cali Basswalk [Ret1]` became `Cali Basswalk [Ret_1`).
Re-saving the same name to the same slot is not a collision. A client that cares
what the preset is called reads the slot back (`save_current_preset(confirm=True)`).

**Delete:**

```
File{action: DELETE, type: 0,
     folder{key: <setlist path>, is_factory: false,
            files{key: "<setlist path>/<name>.pb"}}}
```

**Move:**

```
File{action: MOVE, type: 0,
     folder{key: <setlist path>, is_factory: false, is_downloads: false,
            files{key: "<setlist path>/<name>.pb"}},
     to_folder{key: <setlist path>, files{index: 219}}}
```

Source by file path, destination by linear index. Only same-setlist moves have
been observed. `delete_from_library` exists in the schema and was never sent.

Both explicit false flags are measured. The three recorded CorOS 4.0.1
sessions hold one `MOVE` (session 02, frame 25825) and one `DELETE` (session
02, frame 19033); this is what each carries, decoded 2026-09-21.

| field | `DELETE` | `MOVE` |
|---|---|---|
| `folder.is_factory` | present, false | present, false |
| `folder.is_downloads` | absent | present, false |
| `to_folder.is_factory` | n/a | absent |
| `to_folder.is_downloads` | n/a | absent |

### 10.3 Setlists

**Creating a setlist** is captured from the unit's own "New Setlist" and
confirmed host to unit. Setlists sit side by side under `/media/p4/Presets`:

```
File{CREATE, type: 0, folder{key: "/media/p4/Presets/<name>", name: "<name>",
                             is_factory: false}}
```

The new key appears in the folder listing and works anywhere a setlist path
does. So the MIDI documentation's "User folders" at bank-select LSB 2 to 12 are
folders a player creates. **Deleting a setlist** is `File{DELETE, folder{key,
name}}` against the setlist's own key.

On CorOS 4.0.1, the unit's duplicate action sends a `File` `CREATE` and narrates
progress through `BulkOperation`; replaying that `CREATE` from the host produced
an empty destination. `copy_preset()` retains the measured recall-and-save
fallback on that profile.

On CorOS 4.1, Cortex Control sends one host-drivable sparse
`File{COPY, type: 0, folder{key: <source>, is_factory: false}}`. Firmware chooses
the collision-safe destination and performs the copy asynchronously.
`duplicate_setlist()` sends `COPY` exactly once and uses read-only polling until
two complete, identical 256-slot destination generations match the source.
A contributed 4.1.0 hardware run verified a two-preset copy in 49.878 seconds.

### 10.4 Recents and Favorites

`RecentsFavorites` carries both lists, and **the request's `is_favorites` flag
chooses which one you get**:

```
RecentsFavorites{READ, request_id: N}                      -> Recents  (51 entries here)
RecentsFavorites{READ, is_favorites: true, request_id: N}   -> Favorites
```

Measured 10 of 10 and 0 of 5. **The reply does not set the flag**: both lists
come back with `is_favorites` absent, so the two are told apart by what you asked,
not by what arrives. The unit echoes `request_id`, so correlate on that. An empty
Favorites list answers with a real, empty push. The first read after connecting is
often dropped, so retry rather than concluding anything from one timeout. Entries
carry `name`, `folder_key`, `folder_name` and `is_factory`, and feed straight into
`find_preset()`, `recall_preset()` and `remove_favorite()`.

**Both lists are maintained one entry at a time.** Sending the whole list back
with an extra item does nothing. Watching the unit recall a preset shows the
idiom, a pair of single-entry messages:

```
RecentsFavorites{DELETE, items{name, folder_key, folder_name}}   # drop any existing copy
RecentsFavorites{CREATE, items{name, folder_key, folder_name}}   # add it at the head
```

Favouriting uses the same pair with the flag set, alongside a `BulkOperation`
narrating `"Adding to Favorites, please wait."`. Only presets can be favourited.
The unit echoes the changed entry back with `is_favorites` set, and that echo is
what `add_favorite()` and `remove_favorite()` wait for, because a mismatched
entry (wrong `folder_key` or `is_factory`) is ignored in silence. There is no
per-preset favourite flag in `ProductData` (21 fields), and no folder carries
`FolderInfo.is_favorites` (810 folder pushes checked).

### 10.5 Impulse responses and Neural Captures

**IR Loader blocks** are models 29001 to 29008 (`Single`/`Dual`, mono/stereo,
each with a `Lite` variant). **Every IR Loader has two IR slots**: parameters 0
to 7 are the first (`MUTE`, `INVERT`, `IR PATH`, `LEVEL`, `HI PASS`, `LOW PASS`,
`PAN`, `DELAY`), 8 to 15 repeat them for the second, 16 to 21 are shared, and 22
and 23 are an `IR NAME` per slot.

**`IR PATH` takes the library entry's `key`, not a path.** Read off a block a
person loaded on the unit:

```
params[2]  (IR PATH) = "CIR_eb6d6d347e75f988010a9746580c31c"
params[22] (IR NAME) = "Rex 57 on axis"
```

Both strings come from `list_irs()`, and `set_ir()` writes them, confirmed by
pointing a loader at a different IR from the host and reading back the library's
own key and name on both slots, then seeing that IR loaded on the unit with no
warning icon. The unit does not validate either string on write; a broken
reference shows a warning icon and "`<IR NAME>` is missing" on screen, which a
host cannot see. The 588 entries under `/opt/neuraldsp/impulse_responses` carry
plugin prefixes (`NG_`, `ME_`, `ML_`, `CW_`, `JP_`), report a `name` and no key,
and none is loadable; `list_irs()` excludes them.

**IR import from the host stops at one unknown.** The request needs
`total_bulk_create_count`; without it the unit does not react. With it:

```
File{CREATE, type: 1, total_bulk_create_count: 1, folder{key: "2_q", files{name: "..."}},
     ir_payload: <bytes>}

  -> BulkOperation{progress_message: "Importing IRs, please wait.", blocking: true,
                   type: 1, destination_folder{key: "2_q", parent_key: "local_ir_root",
                                               name: "My IRs", is_user_default: true}}
  -> BulkOperation{UPDATE, progress: 1}
  -> BulkOperation{DELETE, finished: true}
```

The unit reports the operation finished, and no file appears after 60 s of
polling. Eight encodings of `ir_payload` were tried with the same result
([roadmap.md](roadmap.md)). Outbound fragmentation is not the cause: strings of
50 to 3,200 characters (26 fragments) written into a parameter all round-tripped.
The USB link died during one run of these attempts; do not run them unattended.

**A capture id is a block type, not a capture.** Category 14 holds `14000` and
`14001` on the observed unit, and saving a new capture does not add one. Which
capture a block plays is the string parameter `file_name` at index 5:

```
file_name = <64-char content hash><display name>
```

the hash being the `key` of the file in the Captures Library, concatenated with
its name and no separator. Confirmed both ways: read off factory content, and by
creating a capture on the unit and pointing a host-placed block at it. Browse the
library (`local_nc_root`) rather than the catalog, which cannot enumerate
captures.

**Creating a capture hands the flow to a connected host.** Choosing "New Neural
Capture" on the unit announces `NeuralCapture{try_to_show_dialog: true}` and
waits for the host to answer `NeuralCapture{show_dialog: true}` and present the
UI itself. A connected host that stays silent suppresses the on-device wizard;
disconnect to use it. Answering `show_dialog: true` without a UI puts the unit
into the flow (`state: 1`, `model_ab` carrying a cab) with no interface anywhere.
The engine is three internal catalog models: `NC_Recorder` (Progress, Bulk Delay
and Sanity Check meters), `NC_Trainer` (`START TRAINING`, `SET CONDUCTOR`,
`SET NODE`, `CANCEL TRAINING`, `SET SEED`; Progress and Loss meters) and
`NC_Refiner` (`START AUTO REFINE`, `START MANUAL REFINE`, `SET LATENCY`,
`SET AB`, `OUTPUT GAIN`, `EXPORT MODEL`, `IMPORT MODEL`). Placing `NC_Recorder`
on the grid crashes the unit ("Something went wrong", reboot required); it is in
`units.DO_NOT_PROBE`.

**Local backups.** `LocalBackup{CREATE}` is answered by one or more
`LocalBackup{UPDATE, backup_json}` pushes, the last carrying `is_last_chunk`.
Measured 2026-09-09 on CorOS 4.0.1: a 130,178-character document arrived as one
push with the final marker set, in 9.1 s on a cold connection and about 2.9 s
afterwards. Contributed 2026-09-08 on CorOS 4.1.0: 12 chunks (11 of 150,000
characters plus a final 144,890). So 150,000 is a maximum per push, not a chunk
size. `can_apply_backup` never appeared, so the refusal path is unverified.

## 11. Global device settings

Unlike a preset edit, these change the unit: there is nothing to save and nothing
to recall to undo. Each is an ordinary `{action: UPDATE, <field>}` on its own
message type, sparse at the top level, and each is confirmed by writing a value,
reading it back and restoring it.

**Two things to know before trusting a read-back.** State pushes can be partial:
a push after an `UPDATE` may carry only what changed, so a reader waits for one
that contains the field it wants (`settings()` and `mode_cycle()` do). And a read
immediately after a write can return the previous value; allow a settle or
re-read before deciding a write was refused. This produced the master volume
"refusal" below and two other wrong conclusions.

**A submessage write replaces the whole submessage.** Sending
`master_volume_assignment` with only `send12` set left the other three flags
false. `set_master_volume_assignment()` and `set_global_bypass()` read and merge.
Repeated fields keyed by an index are sparse: writing one
`GlobalEQ.parameters{parameter_index, value}` left the other 27 alone.

**Values are quantized**, and port levels are float32. Brightness written as 30
read back 31. Writing `0.769231` stored something measurably different from the
`0.769230783` already there, while writing `10/13` reproduced it exactly.

### 11.1 `GeneralSettings`

One read returns most of the Device Settings and System menus: `screen_brightness`,
`led_brightness`, `dimmed_led_brightness`, the three `enable_*_dimmed` flags,
`lock_screen_and_volume_knob`, `global_bypass_cab` and `global_bypass_ir`,
`scene_block_bypass`, `stomp_mode_auto_assign`, `hold_timing`,
`swap_tempo_tuner_access`, `gig_view_stomp_access_enabled`,
`enable_dynamic_delay_compensation`, `midi_over_usb`, `midi_channel`,
`ignore_duplicate_pc`, `internal_midi_clock_enabled`, `midi_clock_out`,
`power_button_sensitivity`, `master_volume_assignment{out12, out34, send12,
headphones}`, `looper_stomp_assignments`, `cloud_endpoint`, and
`available_disk_space` with `total_disk_space`. The MIDI settings the manual lists
under a MIDI submenu are here, not in `MIDISettings`.

Fifteen fields are confirmed writable, each sent alone and restored: the three
brightnesses, the three dimming toggles, `scene_block_bypass`,
`stomp_mode_auto_assign`, `swap_tempo_tuner_access`,
`enable_dynamic_delay_compensation`, `gig_view_stomp_access_enabled`,
`hold_timing`, `midi_channel`, `midi_over_usb`, `midi_clock_in_enabled`,
`ignore_duplicate_pc` and `disable_internet_connection_check`. All four
`midi_clock_out` values are writable too. Three exceptions:

- **`internal_midi_clock_enabled` refuses.** It stays `true` whatever is sent,
  with `midi_clock_in_enabled` either way.
- **`dimmed_led_brightness` is capped just below `led_brightness`.** Asking for
  100 landed on 25, 9 and 56 as `led_brightness` was 28, 13 and 59.
- **`hold_timing` is an index, not milliseconds.** The unit offers six values,
  500 to 1000 ms in 100 ms steps, and the field is the index (it read 3 while the
  screen showed 800 ms). The unit stores any integer there, so
  `set_hold_timing()` takes `Milliseconds` and checks them.

**`power_option`** (`SHUTDOWN = 0`, `REBOOT = 1`, `STANDBY = 2`, `WAKE_UP = 3`)
and **`reset_wifi_networks`** are commands, not settings. `update_settings()`
refuses both.

**`looper_stomp_assignments` is the "Reassign Looper X Actions" menu**, global
rather than per preset: eight entries indexed by footswitch A to H, each the
Looper X block's own parameter index for the action:

| value | action | value | action |
|---|---|---|---|
| 0 | `RECORD OVERDUB` | 4 | `ONE SHOT` |
| 1 | `PLAY STOP` | 5 | `HALF SPEED` |
| 2 | `UNDO` | 6 | `PUNCH` |
| 3 | `DUPLICATE` | 7 | `REVERSE` |

The factory layout is `[3, 4, 5, 6, 0, 1, 7, 2]`. Writable with
`update_settings(looper_stomp_assignments=[...])`; a host swap of A and H was
seen on the unit's own Looper Actions screen. **The MIDI CC follows the action,
not the footswitch**: with A reassigned to `UNDO` its tile read CC#56, while H
holding `DUPLICATE` read CC#49. So CC#49 `DUPLICATE`, CC#50 `ONE SHOT`, CC#51
`HALF SPEED`, CC#52 `PUNCH IN`, CC#53 `RECORD`, CC#54 `PLAY`, CC#55 `REVERSE`,
CC#56 `UNDO`.

**`scene_block_bypass` changes what `set_bypass` persists.** Its three values are
the manual's three choices, and a host write behaves like a touchscreen edit, not
like a footswitch press:

| mode | touchscreen | footswitch | host `set_bypass` |
|---|---|---|---|
| `ALWAYS_OVERWRITE` | persists | persists | persists |
| `NONSTOMP_OVERWRITE` | persists | discarded | persists |
| `NEVER_OVERWRITE` | discarded | discarded | discarded |

"Discarded" means the write applies and is dropped on the next scene change,
which is indistinguishable from a failed write unless you know the setting. The
unit's own wording for the second option groups MIDI with footswitches. The MIDI
half is untested, since this library has no MIDI path.

### 11.2 `IOSettings`

Sparse and keyed by `input_port_id` and `output_port_id`; writing one port left
the other three byte-identical. Confirmed writable: input `level`, `ground_lift`,
`input_type` and `input_zmode` (impedance); output `level`, `ground_lift` and
`mute`; `usb_port.level`, `hp_select` and `dry_wet`; `midi_port.midi_thru`; the
`xlr1_2_linked` and `out3_4_linked` pairing flags. `hp_port.level` is not
writable. The unit also pushes `IOSettings` with per-port `plugged` flags, useful
ground truth for which jacks are connected.

**`input_port_id` is the `Input` enum, not 1/2/3/4.** Combined ids are
interleaved: Return 1 is 4, Return 2 is 5, 3 is `INPUT_1_2` and 6 is
`RETURN_1_2`.

**An input port's `level` is -12..+60 dB: `dB = -12 + 72 * level`.** Solved from
four owner-set trims read on screen and on the wire at the same moment, all in
the bottom half of the travel, and matching the spec sheet's "+60dB max input
gain". `input_level_db()` and `db_to_input_level()` convert; `units.SETTING_SPANS`
records the evidence. The output and USB spans are not measured, so those levels
take `Encoded` only.

| wire | screen | -12 + 72w |
|---|---|---|
| 0.4055555462837219 | +17.2 | +17.200 |
| 0.40042707324028015 | +16.8 | +16.831 |
| 0.5000885725021362 | +24.0 | +24.006 |
| 0.1666666716337204 | 0.0 | 0.000 |

**Some port fields must travel alone.** Output `mute` and input `input_zmode`
are both writable, and both are silently dropped when they share a port entry
with another field. The USB port packs the same way: `{level, dry_wet}` in one
message landed the level and dropped the dry/wet. `set_input_port()`,
`set_output_port()` and `set_usb_port()` send one field per message.

### 11.3 Master volume

`MasterVolume{UPDATE, volume}` with a normalized 0..1 is writable, confirmed by
eye and by ear: a host write of `0.30` took the overlay to 30 and dropped the
level. `volume` maps to the screen's 0 to 100 as `round(volume * 100)` (wire
0.566115677 displayed 57), and the knob quantizes in steps of 1/121. After a host
write the physical knob soft-takes-over, which is the behaviour the manual
describes for Cortex Control. It is a gain stage of its own: across 114
`MasterVolume` pushes while the knob was turned, no `IOSettings` port level
changed.

`calibrate: true` is an action, not a flag. Sending it opens the full-screen
Master Volume Calibration dialog and waits for a person to sweep the knob. Never
send it beside a level.

### 11.4 Global EQ

**Global EQ parameter layout: 5 per band.** `parameters` is a flat list of 28
`{parameter_index, value}` pairs, sparse by index on write. Band N's controls sit
at `(N - 1) * 5 + offset`:

> `tests/test_client.py` holds this table against `QuadCortex`'s
> `GLOBAL_EQ_BAND_*` constants.

| offset | control | notes |
|---|---|---|
| 0 | GAIN | -12..+12 dB, linear. Measured on screen 2026-09-11: wire 0.0/0.25/0.75/1.0 display -12.0/-6.0/+6.0/+12.0 dB |
| 1 | FREQUENCY | mapping not established |
| 2 | Q | mapping not established |
| 3 | TYPE | a five-option list, `index / 4` |
| 4 | band ENABLE | 1.0 is active, 0.0 bypasses the band (the manual's `EQ BAND BYPASS`) |

Established by changing each of band 1's controls in turn with a scene change
fencing each, then checked against the shipped defaults, which line up as a
five-band parametric EQ should:

```
band 1   gain 0.5   freq 0.142   Q 0.0613   type 1.00 (Lo Shelf)
band 2   gain 0.5   freq 0.207   Q 0.0613   type 0.00 (Peak)
band 3   gain 0.5   freq 0.405   Q 0.0613   type 0.00 (Peak)
band 4   gain 0.5   freq 0.616   Q 0.0613   type 0.00 (Peak)
band 5   gain 0.5   freq 0.729   Q 0.0613   type 0.75 (Hi Shelf)
```

**Indices 25 to 27 are the `OUT` tab**: 25 the overall level (dB mapping not
established), 26 assign to `OUT 1/2` (confirmed on the unit), 27 assign to
`OUT 3/4` (by elimination). `GlobalEQMessage.bypassed` is the whole EQ's switch and the
inverse of the unit's On/Off control: `bypassed: true` is the EQ off, which is
how the observed unit ships. **Filter types** are `0.0` Peak, `0.25` Hi pass,
`0.5` Lo pass, `0.75` Hi Shelf, `1.0` Lo Shelf.
The block EQs share the band polarity and the type-disables-gain rule (section
14.1). A `parameters` block carrying no
`parameter_index` is index 0, and one carrying no `value` is 0.0, because both
are plain scalars.

### 11.5 Footswitch modes

`Mode{UPDATE, mode}` selects a slot; `Mode.mode` is a slot index, not a named
mode. Mode pushes are frequently partial (a mode switch announces `mode` alone),
so `mode_cycle()` waits for a push that contains `available_modes`.

**A `HYBRID` slot is a composite value in `available_modes`.** Merging two modes on
the unit produced `Mode{UPDATE, available_modes{modes: 7, modes: 1}}`, and
sending `available_modes{7, 1}` from the host builds the same thing. The state is
published only once the menu is confirmed with `OK`. A hybrid gives each
footswitch row its own mode, so the composite encodes an ordered pair, read one at
a time off the unit's own indicator:

| value | A-D (top) | E-H (bottom) |
|---|---|---|
| 3 | Preset | Scene |
| 4 | Preset | Stomp |
| 5 | Scene | Preset |
| 6 | Scene | Stomp |
| 7 | Stomp | Preset |
| 8 | Stomp | Scene |

Lexicographic over Preset 0, Scene 1, Stomp 2, so 4 and 7 are the same pairing
in opposite arrangements. `hybrid_mode(top, bottom)` builds them and
`describe_mode()` names any value.

**The range the unit accepts is wider than the range that works.** Writing
`[N, 1]` for N in 3..15: 0 to 9 survive and 10 and above are dropped. **9 is
broken**: its indicator reads "<blank> + Scene" and the footswitches stop
responding, with no error and the value reading back. `set_mode_cycle()` refuses
it. Two structural limits: a cycle holds at most one composite (`[3, 4, 5]` comes
back as `[3]`), and a composite cannot be the only slot (`[7]` alone is refused).

### 11.6 Tuner

`ShowTuner{show}` opens and closes the tuner menu on screen. `Tuner{input_port_id}`
chooses the input: the unit accepts 1 to 5 (both inputs, both returns,
`INPUT_1_2`) plus `USB_5` and `USB_6`, and refuses everything else, `RETURN_1_2`
included. Rejected writes revert to the previous value. The unit's own picker
offers exactly those seven.

**`Tuner.frequency` is the reference pitch as an offset in Hz from 440.** 442 on
the unit announced `frequency: 1.99999809` and 445 announced `5`. `Tuner.mute` is
the menu's `MUTE` preference for silent tuning. `enable_meter` refuses a host
write (it stays `false` and `meter` stays `0.0`), so the needle is not readable
over USB.

**Any host write to the Tuner engages an invisible tuner state.** After a
`Tuner{UPDATE}` carrying `input_port_id` or `mute`, the unit behaves as if the
tuner is open with nothing on screen saying so, and if the mute preference is
true, the outputs are silent. The state survived about 100 recalls, 60 saves and
every scene switch of a 33-minute build. `ShowTuner{show}` does not create or
release it, and `Tuner{READ}` reports every field faithfully while the rig is
silent.

**The disengage message does not exist.** Captured with a person at the unit:
opening the tuner emits `Tuner{UPDATE, frequency: 0}`, one per open; closing it
emits nothing; the unit's own `MUTE` control sends `Tuner{UPDATE, mute: <bool>}`,
byte-identical to `set_tuner_mute()`. Replaying the open announcement,
`Tuner{DELETE}` and `ShowTuner{DELETE}` each left the rig silent. What works from
the host is clearing the mute preference: engaged-but-unmuted is fully audible.
`restore_audio()` does that, at the cost of the player's silent-tuning preference.

### 11.7 Looper, pinning, Gig View

**Looper.** `Looper{READ}` reports a full `status`: `state`, `progress`,
`loop_length`, `free_samples`, `armed`, `in_reverse`, `half_speed`,
`undo_count`, `redo_available` and more. `LooperStatus.state`, mapped by pressing
each transport control in a known order:

| state | meaning |
|---|---|
| 1 | idle / stopped (at rest, and after an `UNDO` removed the loop) |
| 2 | playing |
| 4 | recording, with `loop_length` streaming upward |
| 5 | armed (`RECORD` with no signal; the unit waits for the input to cross the threshold) |
| 6 | overdubbing; pressing again returned to 2 |

`3` has never been observed. `REVERSE` and `HALF SPEED` do not change `state`; they
set `in_reverse` and `half_speed`. Nothing here drives the transport.

**Pinning a model** is `PinnedModels{models: [<id>]}` with no action field; an
`UPDATE` does nothing. The write appends rather than replaces, so pinning
something already pinned leaves two entries. `action: DELETE` with an id removes
every entry for that id.

**Gig View** is `ShowGigView{UPDATE, show}`; `show` has no presence.

**Undo and redo** are drivable: a sparse `UndoRedo{UPDATE, undo: true}`
(`08 01 28 01`) reverses the last grid edit and `redo: true` (`08 01 30 01`)
reapplies it. Measured 2026-09-03 on CorOS 4.0.1 by preset read-back, and by a
contributor on 4.1.0. `undo()` and `redo()` send these messages. `UndoRedo`
also arrives after every accepted grid edit, which makes it an acceptance
signal.

## 12. What the unit announces, and when

### 12.1 The connect burst

Measured from before the handshake with a `connect(before_handshake=...)`
listener on CorOS 4.0.1: first on 2026-08-12, the `Version` count corrected on
2026-08-27 and 2026-09-07, and the closing group re-measured on 2026-09-14 over
three consecutive connections:

| after connect | what arrives |
|---|---|
| 2.0 s | `connect()` returns, having seen the `ResetCommsBuffers` echo and the `Version` messages (section 4.2) |
| 4.9 s | the `ModelRepo` payload, one message of 371 reports |
| 5.1 s | 399 `File` listings at about 1490 reports/s for about 5 s, and most settings types |
| 9 to 11.2 s | `RecallPreset` (the seed), `SetlistPosition`, `PresetDirty`, `Scene`, in that order, inside 3.6 to 6.0 ms (2026-09-14: 11.21 s, 11.08 s and 11.07 s after connect, spread over 6.0, 3.6 and 5.8 ms) |

About 474 messages of 24 distinct types by 15 s. So a listener attached to the
client `connect()` returns is about 3 s too late for the catalog and 8 s too late
for the current preset, which is why `connect(before_handshake=...)` exists. The
arrival time of the closing group varies session to session; its order and spread
are what to build on. **`RecallPreset` is the first of the four, not the last**,
and anything treating it as the end of the burst misses the other three a few
runs in a hundred against a 100 ms poll (`tests/hardware/conftest.py` waits for
all four as `BURST_TAIL`). The seed `RecallPreset` sets `action`, `preset` and
`reason`, and the burst carries no `Grid` pushes.

### 12.2 What a recall pushes

Measured 2026-08-15 across two host recalls, all within about 120 ms of the
request and in this order: `Grid` x 8 to 13 (the new grid, block by block),
`RecallPreset` (with `reason`), `Scene`, `SetlistPosition`, and **no
`PresetDirty` at all**. A recall discards unsaved edits and the unit says nothing
about the flag, so anything tracking it re-reads after a recall.

### 12.3 The edit echo

The unit's echo of a host edit is the opposite of a recall: **a sparse, keyed
delta**. Writing one parameter produced a `Grid` push of 23 bytes, one chain with
`row` set, one model with `column` set, one param. An echo merges straight into a
cached preset. Echo latencies, from `tests/hardware/test_write_echo.py`:

| write | echo latency | predicate |
|---|---|---|
| parameter | 113 to 116 ms | content-matched; the control measurement |
| block placement | 290 to 420 ms | the basis of `set_block(verify=True)`'s window |
| scene label, scene colour | about 2 ms | content-matched, pinned in `tests/test_scene_echo_predicates.py` |
| global settings | about 2 ms | content-matched |
| routing | 9 to 19 ms | provisional: the predicate accepts any chain carrying the target port |
| block bypass | unmeasured | the test skips unless the block already carries a stored bypass entry |

A type-only match on these writes produced a discredited 2 to 11 ms band, so a
number in this range is only as good as the predicate behind it.

A knob turn on the touchscreen announces about 40 `Grid` messages for one edit,
so edit-time traffic is far heavier than steady state.

### 12.4 `PresetDirty` announces a change of the flag, not an edit

Measured 2026-08-14 as a controlled pair inside one connection, the same
`set_param` write made twice on the same block:

| the flag before the write | what the unit sent |
|---|---|
| `false` | a `Grid` echo and a `PresetDirty` carrying `is_dirty: true` |
| `true` | a `Grid` echo, and nothing else |

A save clears the flag (watched flipping across a save). Writing an edited
parameter back to its old value did not clear it within the same connection.
`PresetDirty{READ}` answers as an `UPDATE` in 2 to 11 ms; `is_dirty` has no
presence, so absent is false.

### 12.5 Standby, reboot, shutdown, and device loss

The three departures behave differently:

- **Standby ("Be Right Back") does not disconnect.** The session stays alive (2 ms
  probe answers throughout), and the unit announces it with a partial
  `GeneralSettings` push carrying only `power_option: 2`, then `3` on waking.
  Connecting fresh while it sleeps works. Whether writes are honoured during
  standby is untested.
- **Reboot and shutdown send nothing.** Healthy 3 ms probes to the last moment,
  then reads raise.

**A read raising means the unit is gone; a write raising means nothing.** Over a
145-second healthy session there were 0 read exceptions and 91 write exceptions,
every write "failing" with the status-stage stall. The two can carry byte-identical
text (the first read error after an unplug said `0xE0005000`, like the benign
stall; only the second said "Device is disconnected"), so nothing branches on the
message. The transport retries a read failure once, and two in a row confirm
loss: every entry point then raises `DeviceLostError`, blocked waiters are woken,
and the RX and keepalive threads stop.

**Recovery timings.** After a reboot: about 39 s not enumerated, about 9 s
openable but silent, then a 2 s handshake, about 55 s in total. After a cold boot
about 11.7 s openable but silent; a live host-triggered reboot measured about 17 s.
`connect()` retries the handshake for `handshake_patience` (30 s; 15 s was
measured failing). Lock mode locks the touchscreen and volume knob only: a
parameter write landed and read back exactly while it was engaged.

**`GlobalTempo` streams a pair of messages per beat**, 1.5 s apart at 40 bpm, so
its rate follows the tempo. It is a poor heartbeat and a decent liveness hint.

## 13. Port, instrument, and preset enums

### Input ports (`Chain.in_portid`)

`Chain.in_portid` uses the schema's `GainCalInputPortParameter.InputPortId` enum
verbatim. Ids 0 through 14 were confirmed on hardware (15, `MAX_PORTS`, is
rejected) by diffing presets with known routing against the values read back and
cross-checking the unit's `IOSettings` port list.

| Id | Port | Note |
|---|---|---|
| 0 | EMPTY | chain is fed internally (splitter/mixer), not from a port |
| 1 | Input 1 | rear combo jack is the same port |
| 2 | Input 2 | rear combo jack is the same port |
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

`Chain.out_portid` uses `GainCalOutputPortParameter.OutputPortId` verbatim. Ids
4 (Output 1) and 1 (Output 1/2) were anchored by a preset with known routing; 2
(Output 3/4), 3 (Send 1/2) and 10 (USB 5) were spot-confirmed; 5, 6, 7, 8, 9, 11,
20 and 22 were written and read back verbatim, which shows the unit does not
validate the field but not which jack each reaches. The rest are from the schema.

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

Values 16 to 18 are internal grid routing (feed the next row); 19 feeds several
outputs at once and is a real destination.

### Instrument tag (`ProductData.instrument`)

Guitar 1, Bass 2, Synth 3, Vocal 4, Other 5, each confirmed by setting it on the
unit's own picker and reading the listing back.

Named forms live in `pyquadcortex/protocol/enums.py` (`Input`, `Output`,
`Instrument`, `Setlist`).

## 14. The model catalog (ModelRepo)

`ModelRepo` is read during the connect burst as a readiness gate, and its
payload is the unit's whole block catalog: gzip(tar(`ModelRepo.xml`)), about 46 KB
compressed and 557 KB expanded. Cortex Control reads it as the third message
type of every session, after `ResetCommsBuffers` and `Version`, and the reply
lands about 1.2 s after the session's first message in all three lab captures.

The XML is `<Models><Category id name><Model id name .../></Category></Models>`.

- **`Model/@id` is the wire hash.** Ids are globally unique: category 4
  (Equalizer) holds 4000 to 4007, category 21 (Cabsim Bass) holds 21001 to 21009.
- **`<Parameter>` children are in wire-index order**, each with `min`, `max`,
  `defaultValue`, `units`, `skew`, `steps`, `stepNames`, `type` and more. This is
  what gives a parameter index meaning: writing index 0 of a cab moves no visible
  knob because a cab's own entry lists internal `ir selector` parameters.
- **Parameter values on the wire are normalized 0..1.** Sending `1.0` to a
  `THRESHOLD` whose catalog range is -60..+12 dB made the unit display +12.0 dB.

Attributes that classify a model:

| Attribute | Meaning |
|---|---|
| `sku`, `plugin_id` | purchasable plugin content; a given unit may not have it |
| `hidden`, `internal` | not user-facing; `hidden` also appears on whole categories. Read the value, not the presence: two ordinary amps ship `hidden="false"` (Bogna Uber Clean 1130, Bogna Uber Lead 1131) |
| `replaces` | this model supersedes the listed id(s). Both stay in the catalog and can share a display name (two "Graphic-9" equalizers, 4005 replacing 4002) |

Because the catalog comes from the unit it also covers Neural Captures
(categories 14 and 20), which are user content. The library ships generated
constants only for factory content (414 of the 533 models on the observed unit)
and resolves everything else at runtime. The catalog's attributes the library
cannot yet explain, and what is known about `displayPos`, `<Padding>`, `hidden`
and `stepNames`, are in [domain-model.md](domain-model.md), "Catalog attributes".

### 14.1 One law covers every parameter's scale

```
real = min + (max - min) * wire ** (1 / skew)
```

`skew` has three spellings: a number, `LIN_SKEW` (1.0), and `LOG_SKEW`, which
is the same power law at skew 0.3 and not a logarithmic sweep. Two dirty values
ship (`" 0.4"` with a leading space, and `""`) and fall back to linear. Confirmed
on hardware 2026-08-26 over three blocks in two units:

| block | parameter | skew | wire | predicted | screen |
|---|---|---|---|---|---|
| any cab | `LEVEL`, indexes 2 and 10 | 4.9594844 | 0.01 / 0.50 / 1.00 | -21.82 / 0.00 / 6.00 dB | -21.8 / 0.0 / 6.0 |
| Low-High Cut | `HPF FREQ` | 0.3 | 0.25 | 216.7 Hz | 217 Hz |
| Low-High Cut | `LPF FREQ` | 0.3 | 0.75 | 7678.3 Hz | 7678 Hz |
| Low-High Cut | `OUTPUT` | none | 0.25 | -10.0 dB | -10.0 dB |
| Envelope Filter | `FREQ` | `LOG_SKEW` | 0.25 | 197.4 Hz | 197 Hz |
| Envelope Filter | `RESO` | `LOG_SKEW` | 0.75 | 4.450 | 4.45 |

The two `LOG_SKEW` readings solve independently to exponents 3.3366 and 3.3330,
both `1/0.3`; a true log sweep would have shown 316 Hz and 5.62, and a straight
line 2575 and 7.75. 615 parameters carry a non-linear skew. Every screen reading
taken in this project is a row in `tests/test_scales.py`, asserting the catalog reproduces the
display at its own precision; the lab repository's `doc/scale-readings-campaign.md`
holds the readings and how they were taken.

**`min` and `max` are sometimes a name**, not a number:
`min="MIN_CABSIM_DB"`. The numbers live in `units.FIRMWARE_CONSTANTS`, each with
its evidence. A name the build has never met raises rather than falling back.

| constant | count | parameters | value | how it is known |
|---|---|---|---|---|
| `EQ_DB` | 16 | the band gains, over three EQ models | -12..12 | `steps=241` fixes 0.1 dB steps; measured at four points |
| `CABSIM_DB` | 12 | cab `LEVEL` | -40..6 | a PCOM cab spells the same knob out literally; twelve readings fit the taper within 0.034 dB |
| `FXLOOP_OUT_GAIN_DB` | 9 | `LEVEL`, `SEND LEV`, `THRU` | -40..0 | five points; a send cannot boost |
| `FXLOOP_IN_GAIN_DB` | 6 | `LEVEL`, `RET LEV` | -40..12 | three points; a return can boost |
| `MIXER_DB` | 8 | `LEVEL A/B`, `LEVEL TO A/B`, `MIXER LEVEL`, `VOLUME` | -40..12 | measured at both ends; `LEVEL TO A/B` sit at 10/13, which is 0 dB |
| `TEMPO` | 1 | `TEMPO` | 40..240 | `steps=201` fixes whole bpm; three interior points |
| `EQ_FREQ` | 2 | `FREQUENCY` on both splitter models | 20..20000 | solved from `defaultValue`, `skew` and one wire value |
| `INPUT_TRIM` | 1 | `NC_Recorder OUT LEVEL` | **unknown** | placing the block crashes the unit |

**The block EQs (`4000`, `4001`, `4004`) share two facts with the Global EQ.** A
band's `TYPE` decides whether its `GAIN` means anything: Lo Pass and Hi Pass
disable the control, and a gain written to such a band is stored and ignored. And
`N BYPASS = 1` means the band is on. A disabled band displays `0.0 dB` whatever
it stores, which makes a write look like it never landed.

**Unity for the level parameters is `0.76923077`** (10/13, 0 dB on -40..+12).
`MIXER LEVEL` and `LEVEL TO A/B` read exactly that on every one of the 34 factory
rows carrying them, and lane `VOLUME` on 52 of 68. `UNITY_LEVEL` holds it. A span
needs at least one reading away from the reference: -100..+30 also puts 0 dB at
10/13, and two close points cannot tell the two apart.

**A labelled-end control draws a span the catalog does not state.** 36 parameters
carry `min_string`, `mid_string` and `max_string` together. They are pan-style
controls and the unit draws every one the same way: `50 L` at wire 0.0, `C` at
0.5, `25 R` at 0.75, `50 R` at 1.0 (measured 2026-09-11 on a mono cab's `PAN`, a
stereo cab's `BALANCE` and a Minivoicer's two `PAN`s). The catalog declares that
one drawn control as `-1..1` on 22 parameters, `0..10` on 10, `0..1` on 3 and
`-50..50` on one. `units.LABELLED_END_SPAN` holds the drawn span and the parser
applies it to any parameter carrying all three labels.

**The bottom of a scale is sometimes a word, and the catalog says where numbers
start.** `min_string` is set on 254 parameters (`OFF` on 191, also `-Inf` and
`L`). The unit's numeric entry box states exactly the catalog's `min`..`max` (9
of 9 knobs read), `showAsInteger` says whether it takes whole numbers (9 of 9),
typing the minimum gives the word (9 of 9 across 9 laws), and one entry step above
the minimum is the lowest real number (6 of 6).

So `Parameter.floor` is derived,
with `units.OFF_STEP_INTEGER` and `OFF_STEP_DECIMAL` the only numbers: a lane
`VOLUME` reaches -39.99 dB and a cab `HPF` 21 Hz. The screen cannot settle this
because it rounds (`-40.0 dB` at the lowest real position and `OFF` one step
below), and a knob cannot turn below 1% of travel. `min_string` is trusted per
parameter: 14 cab `LEVEL`s (the PCOM variants and Parallax) omit it, and a
`Parallax` cab `LEVEL` displays -40.0 dB where a labelled cab shows `OFF` at the
same wire 0.0 (typed on 2026-09-12).

Keying the label by law instead would be
wrong: 559 parameters share a law with a labelled one without carrying the label,
and 474 of those are ordinary 0-100% controls whose 0% is a real value. The
`-Inf` law is 20 `type="grMeter"`
readouts, not knobs. The amp `OUTPUT` law (-60..12, skew 3.8018) reads numbers to
-58.1 dB at wire 0.000001, matching the law to 0.03 dB.

**Option names.** `stepNames` carries them on 539 parameters. The 527 with a
fixed list use 113 distinct lists, of which `Off,On` and `OFF,ON` cover 247;
`pyquadcortex.protocol.options` publishes the other 111 as enums and Off/On
parameters take a `bool`. Confirmed: wire 0.25 on a Low-High Cut's `HPF SLOPE`,
option 2 of 9, showed `-12 dB/o`. The unit's own spelling is preserved on the wire
(sixteen `INVERT` parameters offer `Noral,Inverted`). Which lists have been read
off the screen, and where the catalog disagrees with the unit, is in
[domain-model.md](domain-model.md), "Catalog attributes".

**`expAssignable` does not govern a host write.** Fourteen parameters carry
`expAssignable="false"`; a Pattern Tremolo's `STEPS` (one of them) and `DEPTH`
(not) both took a pedal identically and survived a fresh read (ADR-0010 capture,
2026-08-26). It is published as `Parameter.exp_assignable` and nothing acts on it.

## Operation coverage

Every operation the library exposes has been exercised live on the profile named
in its row, and on Quad Cortex, CorOS 4.0.1 where the row names none. "Verified
by" means: **read-back** = state re-read over the protocol and asserted;
**on-unit** = confirmed on the unit's own screen; **captured only** = seen on the
wire, with no independent read-back.

| Operation | Wire shape (brief) | Verified by | Notes |
|---|---|---|---|
| connect handshake | `ResetCommsBuffers` + `Version` UPDATE + `ModelRepo` READ + `Connection` + subscribe READs | read-back | the connect gate; state pushes flow only after it |
| version read | `Version{action: READ}` | read-back | two messages come back; `version()` accepts only one carrying `device_serial_number` or `app_fw_version` (section 4.4) |
| `set_device_name` | `Version{UPDATE, custom_name}` | read-back + on-unit | sparse echo, read-back, and restoration confirmed on CorOS 4.0.1 and 4.1.0 |
| `inhibited_modules` | `CompilerInhibitedModules{READ}` | read-back | explicit false/false reply on CorOS 4.0.1 and 4.1.0; true semantics are schema-derived, not yet observed |
| `create_local_backup` | `LocalBackup{CREATE}` then `LocalBackup{UPDATE, backup_json}` pushes, the last with `is_last_chunk` | captured only | section 10.5. `can_apply_backup` never appeared, so the refusal path is unverified |
| `recall_preset` / `read_preset` | `SetlistPosition{UPDATE, folder_key, position, is_factory, request_id}` then a `RecallPreset` push | read-back | the push echoes the recall's `request_id` |
| `read_current_preset` / `read_current_preset_push` | `RecallPreset{READ, request_id}` | read-back | the live grid, no side effects. The push variant hands back the whole reply with `reason` |
| `loaded_position` | `SetlistPosition{READ, request_id}` | read-back | which slot is loaded; 3 ms measured |
| `list_presets` | `File{action: READ}` then `File{folder{files[] = ProductData}}` | read-back | factory listing gzipped; 256 slots; listings lag after a `File` mutation |
| `switch_scene` | `Scene{UPDATE, selected_scene}` | on-unit | zero-based |
| `set_chain_input` / `reroute_grid_input` | `Grid{UPDATE, preset{chains{row, in_portid}}}` | read-back + on-unit | row-keyed; the only shape that persists input routing |
| `set_param` | `Grid{UPDATE, preset{chains{row, models{column, params{index, param_values{float_value}}}}}}` | read-back | value round-trips 0.0 to 1.0; per-scene values via promote + switch_scene + write |
| `set_bypass` | `Grid{UPDATE, preset{bypass{row, colBypass{column, sceneBypass[scene]{bypass}}}}}` | on-unit | block greyed out on the unit |
| `set_scene_label` / `set_scene_color` | `SceneLabel` / `SceneColor{UPDATE, index, label/color}` | read-back | colour is ARGB uint32; exact round-trip |
| `copy_scene` | `SceneCopy{UPDATE, from_index, to_index, is_swap}` | read-back + on-unit | `from_index` and `is_swap` confirmed; label and colour travel with the state |
| `save_current_preset` | `File{CREATE, folder{key, files{index, name, instrument}}}` | read-back | snapshots the grid; `preset_payload` is ignored |
| `delete_preset` | `File{DELETE, folder{files{key: "<setlist>/<name>.pb"}}}` | one captured `DELETE` + three-session key evidence + read-back | Every occupied entry in those captures exposed that exact key. The API also accepts the listing's `ProductData`, validates that its device-provided key belongs to the named setlist, and sends the key unchanged. Works, but asynchronously: a listing within about 2 s is stale, about 5 s is reliable |
| `move_preset` | `File{MOVE, folder{is_factory: false, is_downloads: false, files{key}}, to_folder{files{index}}}` | one captured `MOVE` + read-back | Source by file path, destination by index; asynchronous like delete. The explicit false flag is present on the captured wire shape. The API accepts either the exact name or a listing `ProductData` and never waits for a listing before sending |
| `set_param_scene_mode` | `Grid{UPDATE, ..., params{index, scene_mode}}` (flag alone) | read-back | a value in the same message voids it |
| `set_chain_output` | `Grid{UPDATE, preset{chains{row, out_portid}}}` | read-back | required for a new chain: the unit never assigns an output on its own |
| `set_param(Mixer(row), ...)` | `Grid{UPDATE, preset{chains{row, mixer{params{index, param_values}}}}}` | read-back | supports per-scene; how factory presets build scenes |
| `disconnect` | `Connection{connected: false}` | sent, unverified | matches Cortex Control's captured behaviour on quit; no state to read back |
| `set_param(Splitter(row), ...)` | `Grid{UPDATE, preset{chains{row, combined_splitter{params{index, param_values}}}}}` | read-back | writes `combined_splitter`, not `splitter[]`; indices follow unified model 10004 |
| `splits` | reads `Chain.split_control_points` | read-back | `split == -1` means serial; `mix == -1` with `split >= 0` is a branch that never rejoins |
| `set_param(Tempo(), ...)` | `Grid{UPDATE, preset{tempoProgramData{params{index, param_values}}}}` | read-back | per-preset tempo, LED and metronome; not row-keyed yet applied |
| `tempo_mode` / `set_tempo_mode` | `GlobalTempo{READ}`, and `GlobalTempo{UPDATE, params{index: 1, param_values}}` | read-back + on-unit | section 8.1. A device setting; the reader waits for a reply carrying parameters |
| `set_param(LaneOutput(row), ...)` | `Grid{UPDATE, preset{chains{row, output_control{hash: 23000, params{index, param_values}}}}}` | read-back | `VOLUME`, `PAN`, `MUTE`, `SOLO` per row; `PAN` 0.5 to 0.0 survived save and read-back. `Db(...)` for `VOLUME` over -40..+12 |
| `move_block` | `GridMove{move{from_row, from_col, to_row, to_col, is_drop}}` | read-back | a cross-row move makes the unit create a branch |
| `set_split` / `clear_split` | `Grid{UPDATE, preset{chains{row, split_control_points{split, mix}}}}` | read-back | activates or clears a row's branch |
| `set_expression_bypass` | `Grid{UPDATE, ..., models{bypass_expression, expression_bypass_info}}` | read-back + on-unit | `type` is `ExpressionSwitchMode`: `STOP` 0, `SWITCH` 1, `HEEL_TOE` 2 |
| `list_folders` | `File{READ}`, collecting every push | read-back | 399 folders on the observed unit |
| `favorites` / `recents` | `RecentsFavorites{READ, is_favorites}` / `{READ}` | read-back | the flag selects the list; the reply never sets it |
| `add_favorite` / `remove_favorite` | `RecentsFavorites{CREATE, is_favorites: true, items{...}}` / `{DELETE, ...}` | read-back + on-unit | one entry per message; the unit echoes the changed entry |
| `tuner` / `show_tuner` / `set_tuner_input` | `Tuner{READ}` / `ShowTuner{UPDATE, show}` / `Tuner{UPDATE, input_port_id}` | read-back | any tuner write engages the invisible tuner state (section 11.6) |
| `set_tuner_reference` | `Tuner{UPDATE, frequency}` | read-back + on-unit | an offset in Hz from 440, taken as `Hertz` |
| `set_tuner_mute` / `restore_audio` | `Tuner{UPDATE, mute}` | read-back + by ear | `restore_audio` clears the preference |
| `looper` | `Looper{READ}` | read-back | full status; transport not driven |
| `set_input_port` / `set_output_port` / `set_usb_port` / `set_midi_thru` / `set_output_pairing` | `IOSettings{UPDATE, settings{...}}` | read-back | sparse and port-keyed, one field per message. The input gain converts `Db` over -12..+60; output and USB levels take `Encoded` |
| `set_output_mute` | `IOSettings{UPDATE, settings{out_port{output_port_id, mute}}}` | read-back + on-unit | must travel alone |
| `set_param_option` | `Grid{UPDATE, ..., params{index, param_values{float_value}}}` | read-back + on-unit | picks a list parameter's option by name; `index / (count - 1)` |
| `captures` / `set_capture` | `File{READ}` on `local_nc_root`; then `Grid{UPDATE, ..., params{index: 5, param_values{string_value}}}` | read-back + on-unit | `file_name` = hash + display name selects the capture |
| `list_irs` / `set_ir` | `File{READ}` with `type: 1`; then `Grid{UPDATE, ...}` writing `IR PATH` and `IR NAME` | read-back + on-unit | the library key and name, on either slot |
| `master_volume` / `set_master_volume` | `MasterVolume{READ}` / `{UPDATE, volume}` | read-back + on-unit + by ear | takes `Encoded`; the screen shows `round(v * 100)`. Never add `calibrate` |
| `pin_model` / `unpin_model` / `pinned_models` | `PinnedModels{models}` with no action / `{DELETE, models}` | read-back + on-unit | pinning appends and can duplicate; `DELETE` removes every entry for an id |
| `delete_setlist` | `File{DELETE, folder{key, name}}` | read-back | removes the setlist and its contents |
| `create_setlist` | `File{CREATE, folder{key: "/media/p4/Presets/<name>", name}}` | read-back + on-unit | setlists are siblings under the presets root |
| `copy_preset` / `duplicate_setlist` | recall then `File{CREATE}` per preset | read-back | compositions; each recalls the source on the unit |
| `duplicate_setlist` (CorOS 4.1) | `File{COPY, folder{key: <source>, is_factory: false}}` | Cortex Control 4.1 binary + contributed hardware read-back | sends once, then stabilizes folder and complete 256-slot preset listings; CorOS 4.0.1 refuses because this shape has not been measured there |
| `set_split_mute` | `Grid{UPDATE, preset{chains{row, splitBypass{bypass}}}}` | read-back | reported back in `mixBypass`; one write sets all eight scenes |
| `set_stomp_assignment` / `clear_stomp_assignment` | `Grid{DELETE, stomp_mode_assignments{row, column}}` then `Grid{UPDATE, ...{stomp_index}}` | read-back + on-unit | the unit's own two-message sequence |
| `set_stomp_momentary` | `Grid{UPDATE, preset{stomp_is_momentary{key, value}}}` | read-back + on-unit | keyed by footswitch; lands only on a switch driving exactly one block |
| `set_stomp_label` | `Grid{UPDATE, preset{stomp_labels` or `single_stomp_labels{key, value}}}` | read-back + on-unit | `single_stomp_labels` is what the unit writes for a single-block switch |
| `set_expression` / `clear_expression` | `Grid{UPDATE, preset{chains{row, models{column, params{index, expression, expression_min, expression_max}}}}}` | read-back + on-unit | pedal 1 or 2; `expression: 0` unassigns. Refuses a lane output's `MUTE` and `SOLO` |
| `expression_assignments(preset)` | reads `params{expression, expression_min, expression_max}` across `models`, `output_control`, `input_control`, `mixer` and `combined_splitter` | fixture | position is the index. Not walked: `chain.splitter` and `Tempo` |
| `set_midi_out` / `set_preset_load_midi_out` | `MIDISettings{UPDATE, general_midi_messages` or `preset_load_messages{messages{source, msg}}}` | read-back | a `Grid` update carrying the preset's own midi fields does nothing |
| `set_param(..., "a string")` | `Grid{UPDATE, ..., params{index, param_values{string_value}}}` | read-back + on-unit | string-valued parameters, e.g. cab microphone selection |
| `param_options` | reads `Param.dynamic_steps` | read-back | the option names of a dynamic list parameter |
| `set_master_volume_assignment` | `GeneralSettings{UPDATE, master_volume_assignment{...}}` | read-back | read-merge-write, because a submessage is replaced wholesale |
| `set_global_bypass` | `GeneralSettings{UPDATE, global_bypass_cab` / `_ir{row1..row4}}` | read-back | global Cab / IR bypass per row |
| `set_global_eq_band` | `GlobalEQ{UPDATE, parameters{parameter_index, value}}` | read-back | the raw door; takes `Encoded` only |
| `set_global_eq` | `GlobalEQ{UPDATE, parameters{parameter_index, value}}` | read-back + on-unit | by band number; `GAIN` takes `Db` over -12..+12 (measured 2026-09-11); `FREQUENCY`, `Q` and the OUT level take `Encoded` |
| `set_mode_cycle` | `Mode{UPDATE, available_modes{modes}}` | read-back | the whole list is replaced; refuses 9 |
| `settings` / `update_settings` | `GeneralSettings{READ}` / `{UPDATE, <fields>}` | read-back | sparse; refuses `power_option` and `reset_wifi_networks` |
| `set_scene_bypass_behavior` | `GeneralSettings{UPDATE, scene_block_bypass}` | read-back | global, and it decides what `set_bypass` persists |
| `io_settings` / `set_input_level` / `set_output_level` | `IOSettings{READ}` / `{UPDATE, settings{in_port` or `out_port{port_id, level}}}` | read-back | also reports impedance, type, ground lift and `plugged` |
| `global_eq` / `set_global_eq_bypassed` | `GlobalEQ{READ}` / `{UPDATE, bypassed}` | read-back | five bands reported as 28 parameters |
| `mode` / `set_mode` | `Mode{READ}` / `{UPDATE, mode}` | read-back | a slot index; `available_modes` lists the configured slots |
| `undo` / `redo` | `UndoRedo{UPDATE, undo: true}` / `{redo: true}` | preset read-back | measured 2026-09-03 on CorOS 4.0.1 and by a contributor on 4.1.0; a bypass edit was reversed and reapplied on disposable preset copies; empty-history behaviour is unknown |
| `preset_dirty` | `PresetDirty{READ}` | request_id echo | answers as `UPDATE` in 2 to 11 ms; `is_dirty` has no presence; pushed unsolicited only when the flag changes |
| `set_gig_view` | `ShowGigView{UPDATE, show}` | read-back + on-unit | `show` has no presence |
| `set_param(LaneInput(row), ...)` | `Grid{UPDATE, preset{chains{row, input_control{hash: 28000, params{index, param_values}}}}}` | read-back | `NOISE REDUCTION`, `BYPASS` and `INPUT GAIN`, per-scene included |
| `free_rows` | reads `models[]` + `Chain.split_control_points` | read-back | excludes the lane row of a branch |
| `wait_for_listing` | repeated `File{READ}` | read-back | polls until a listing settles |
| `write_preset` | `Grid{UPDATE, preset}` | read-back | low-level primitive; applies only row/column-keyed elements. A recalled preset written back does nothing |
| `set_block` | `Grid{UPDATE, preset{chains{row, models{column, hash}}}}` | read-back + on-unit | a placement can be refused silently, for DSP capacity or a port conflict; verified against the echo and a read-back |
| `remove_block` | `Grid{action: DELETE, preset{chains{row, models{column, hash: 0}}}}` | read-back + on-unit | the action marks the removal |
| `catalog` | `ModelRepo{READ}` then `ModelRepo{model_repo_payload}` | read-back | gzip(tar(ModelRepo.xml)) |

## Open questions

- **The two device-filled trailer bytes** (`n+6`) have no known meaning. They do
  not match common CRC-16 variants. Two smaller things beside them: the type
  field's width is not observable, and CortexUSB's decompression skip for types
  32 and 33 was not reproduced on this firmware (section 2.3).
- **`delete_from_library`** on `FileMessage` exists in the schema and was never
  sent.
- **Most output port ids** are schema-derived rather than confirmed against a
  jack (section 13).
- **DSP cost per model** is not published anywhere reachable, and `CPULoad` never
  arrives, so whether a block fits is discovered by placing it.
- **`NC_Recorder`'s `OUT LEVEL` bound** is unknown and will stay so: placing the
  block crashes the unit.
- **What `expAssignable="false"` governs.** It does not stop a host write. The
  touchscreen's own assignment menu is the candidate and has not been checked.
  Whether the unit acts on an assignment stored against such a parameter needs
  audio, not a wire read.
- **What `toggleOn`, `toggleOff` and `toggleStep` mean**, on 212 parameters.
- **Whether a capture id denotes different content on a different unit** needs a
  second unit.
- **Whether host writes are honoured during standby.**
- **Echo latency for a bypass write** and a row-keyed predicate for the routing
  echo (section 12.3).
- **Whether the recall that follows a host undo carries `reason: UNDO`.**
- **Cross-setlist moves**, downloads and plugin folders (`SetlistPosition.is_downloads`,
  `is_plugin`), the IR payload format, and bulk operations are present in the
  schema and unobserved.
- **About half the schema's 71 message types** have never been seen on the wire
  by this project. Their field layouts are known from the schema; their behaviour
  is not.

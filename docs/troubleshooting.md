# Troubleshooting

> Purpose: symptoms a user may meet, what they mean, and what fixes them.

## `DeviceNotFoundError` when it was working a moment ago

If a session was running and then the unit vanishes mid-run, the advice in that
error message does not apply: Cortex Control is quit, the cable is in, and the
unit has booted. What can happen instead is that the unit's USB link dies and
only a full power-down recovers it.

This is field experience from one unit, and the root cause is unknown. It is
recorded because the symptoms mislead.

**What it looks like:** `hid.enumerate()` reports zero Neural DSP interfaces and
`connect()` raises. Reseating the cable changes nothing, and retrying in software
never succeeds (25 attempts over 75 seconds, never visible once).

**How to tell it from a plain disconnection:** the port is flapping, asserting
and dropping a connection several times a second, rather than idle. On macOS:

```bash
log show --last 60s --predicate 'eventMessage CONTAINS "cableChangeOccurred"' \
    --style compact | grep -c cableChangeOccurred
```

Hundreds of events a minute with nobody touching the cable means the connection
is being made and lost repeatedly, so enumeration never completes. About 264
events were seen while attached, against about 1 per minute with the cable out.
If it is quiet with nothing plugged in, the Mac's port and USB stack are fine.

**What fixed it:** a full shutdown of the unit, then power on. A reboot was not
enough, and unplugging at the unit end does not reset its USB controller.

**Then wait about three minutes before re-diagnosing.** After a restart the link
flaps for a while as it settles, and that looks identical to the fault. In one
measurement the unit was still flapping a minute later with zero interfaces, then
enumerated on its own two and a half minutes after the restart.

**What is not established:** the cause. One unit, one host, and only the cable
that shipped with it, so a marginal cable is not ruled out. Onset followed about
20 minutes of continuous heavy write traffic.

## The rig is silent (or clicking) and every read-back looks perfect

See "Settings only your ears can verify" in [api.md](api.md). In short: any host
write to the tuner engages an invisible tuner state, and with the tuner's mute
preference set the outputs go silent with no on-screen cause. `restore_audio()`
releases it. A faint metronome click means the metronome is unmuted (tempo
parameter 4 at 1.0), and the volume control's floor is -60 dB, not silence. None
of this is visible to a read.

## How long a reboot or cold boot takes

Measured on CorOS 4.0.1: a reboot is about 39 s not enumerated, then about 9 s
enumerated but silent, then a 2 s handshake, about 55 s in total. `connect()`
rides through the silent window on its own (`handshake_patience`, 30 s by
default). A cold boot showed about 11.7 s of silent-but-openable. If a unit is
unreachable for minutes, that is not a boot; see the first section.

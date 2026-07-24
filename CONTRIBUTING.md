# Contributing

Thanks for your interest in improving this project. Contributions of all kinds are
welcome - bug reports, fixes, new operations, documentation, and tests.

By submitting a contribution you agree that it is licensed under the project's
[MIT License](LICENSE).

## How contributions work

You do **not** need to ask for access or to be added to the project first. The flow is:

1. **Fork** this repository to your own account.
2. Create a **branch** for your change.
3. Commit your work, push it to your fork, and open a **pull request** against `main`.
4. A maintainer reviews it. Every change is reviewed and approved before it is merged,
   so please be patient and expect a round or two of feedback.

Continuous integration runs the test suite on every pull request. Please make sure it
is green - a red build will block the merge.

## Development setup

You need **Python 3.11+**. [`uv`](https://docs.astral.sh/uv/) is recommended (a `pip`
fallback is shown in the README).

```bash
uv venv && uv pip install -e ".[dev]"
```

The generated protobuf bindings are committed to the repository, so no `protoc` step
is required for a normal checkout. If you are working against an updated device schema,
regenerate them with `scripts/compile_protos.sh` (see the README).

## Running the tests

```bash
.venv/bin/python -m pytest -q
```

The suite is fully **offline** - it needs no Quad Cortex and does not import `hid`
(so no `DYLD_LIBRARY_PATH` prefix is needed, even on macOS), which means you can
develop and test most changes with no hardware attached. Please add or update tests
for any behavior you change.

## Working with hardware

If your change touches the live device path and you want to verify it on a real unit:

- Connect the Quad Cortex over **USB** (not Wi-Fi).
- **Quit Cortex Control first.** It opens the HID interface exclusively, so nothing else
  can talk to the device while it is running.
- The device protocol is **unversioned** and can change across CorOS / Cortex Control
  updates. Re-verify the schema and framing if you are on newer firmware.

## Style

Match the style of the surrounding code. Keep changes focused - unrelated cleanups are
easier to review as separate pull requests. In user-facing text, describe the project
as speaking and re-implementing the device's own protobuf protocol.

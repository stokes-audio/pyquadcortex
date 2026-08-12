#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_SRC="$HERE/protocol/proto"
OUT="$HERE/pyquadcortex/protocol/proto"

# Do NOT mkdir -p here. A stale or mistyped OUT would be created, protoc would
# write into it, this script would print success, and the bindings the package
# actually imports would sit untouched - so the library would keep decoding
# preset and grid payloads against the old descriptors, and wrong parses feed
# writes. set -euo pipefail catches protoc failing; it cannot catch protoc
# succeeding into the wrong place. The __init__.py is the right thing to look
# for: it carries the sys.path shim that makes protoc's sibling imports resolve
# inside a package, and mkdir -p would not bring it back.
if [ ! -f "$OUT/__init__.py" ]; then
  echo "error: $OUT is not the bindings directory (no __init__.py in it)." >&2
  echo "       Fix OUT in this script rather than creating the directory:" >&2
  echo "       a fresh one is missing the sys.path shim the bindings need." >&2
  exit 1
fi
# Prefer the version-matched generator from the venv dev extra (grpcio-tools);
# fall back to system protoc for environments without the venv.
if [ -x "$HERE/.venv/bin/python" ]; then
  "$HERE/.venv/bin/python" -m grpc_tools.protoc -I "$PROTO_SRC" --python_out="$OUT" Preset.proto ProductionAutomation.proto
else
  protoc -I "$PROTO_SRC" --python_out="$OUT" Preset.proto ProductionAutomation.proto
fi
echo "Generated bindings in $OUT"
# "Nothing changed" is a result worth seeing rather than assuming: it means the
# schema edit did not reach the bindings, or protoc wrote somewhere else.
if git -C "$HERE" rev-parse --git-dir >/dev/null 2>&1; then
  CHANGED="$(git -C "$HERE" status --porcelain -- "$OUT")"
  if [ -z "$CHANGED" ]; then
    echo "note: the bindings are byte-identical to what was already committed."
  else
    echo "$CHANGED"
    echo "note: bump the protobuf pin in pyproject.toml in this same commit"
    echo "      (ADR-0001: the gencode and the runtime pin are one unit)."
  fi
fi

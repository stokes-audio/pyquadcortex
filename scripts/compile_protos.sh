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

# Generate into a scratch directory, not straight into OUT. The gencode version
# is a property of the generator and is only knowable by reading its output, so
# the downgrade check below has to happen after protoc runs but before the
# committed bindings are overwritten. Staging is what buys that gap.
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT

# Prefer the version-matched generator from the venv dev extra (grpcio-tools);
# fall back to system protoc for environments without the venv.
if [ -x "$HERE/.venv/bin/python" ]; then
  "$HERE/.venv/bin/python" -m grpc_tools.protoc -I "$PROTO_SRC" --python_out="$STAGE" Preset.proto ProductionAutomation.proto
else
  protoc -I "$PROTO_SRC" --python_out="$STAGE" Preset.proto ProductionAutomation.proto
fi

# Every generated file carries the version of the generator that wrote it:
#   # Protobuf Python Version: 7.35.1
gencode_of() {
  [ -f "$1" ] || return 0
  sed -n 's/^# Protobuf Python Version: *//p' "$1" | head -1
}

# 0 when $1 is strictly older than $2. Numeric field sort rather than sort -V,
# which is not in every POSIX sort.
older_than() {
  [ "$1" = "$2" ] && return 1
  [ "$(printf '%s\n%s\n' "$1" "$2" | sort -t. -k1,1n -k2,2n -k3,3n | head -1)" = "$1" ]
}

# THE GATE. protobuf only validates `runtime >= gencode`, so bindings written by
# an OLDER generator import perfectly and pass every test - they just quietly
# walk the pin backwards, which is the one thing ADR-0001 exists to prevent.
# Refuse rather than write. Whoever ran this wanted new bindings, so leaving the
# old ones in place is not silent either: they get this message instead.
DOWNGRADES=""
MOVED=""
for staged in "$STAGE"/*_pb2.py; do
  name="$(basename "$staged")"
  new="$(gencode_of "$staged")"
  committed="$(gencode_of "$OUT/$name")"
  # No committed file means a binding that is new in this change; nothing to
  # compare it against, and the pin check in tests/test_packaging.py covers it
  # once it lands.
  [ -n "$committed" ] || continue
  if [ -z "$new" ]; then
    DOWNGRADES="$DOWNGRADES
  $name: committed $committed, this generator stamps no version at all"
  elif older_than "$new" "$committed"; then
    DOWNGRADES="$DOWNGRADES
  $name: committed $committed, this generator emits $new"
  elif [ "$new" != "$committed" ]; then
    MOVED="$new"
  fi
done

if [ -n "$DOWNGRADES" ]; then
  echo "error: this generator would DOWNGRADE the committed gencode.$DOWNGRADES" >&2
  echo "" >&2
  echo "       Nothing was written. Older gencode still imports - protobuf only" >&2
  echo "       checks runtime >= gencode - so this would pass the whole suite" >&2
  echo "       while breaking the ADR-0001 coupling between the bindings and" >&2
  echo "       the protobuf pin." >&2
  echo "" >&2
  echo "       grpcio-tools carries its own protoc, so the fix is a newer one:" >&2
  echo "         pip install -U -e '.[dev]'      # or: uv pip install -U -e '.[dev]'" >&2
  echo "" >&2
  echo "       If that already gives the version above, then the grpcio-tools" >&2
  echo "       floor in pyproject.toml is stale - find the release whose protoc" >&2
  echo "       emits the committed gencode and raise the floor to it." >&2
  exit 1
fi

cp "$STAGE"/*_pb2.py "$OUT/"
echo "Generated bindings in $OUT"

# "Nothing changed" is a result worth seeing rather than assuming: it means the
# schema edit did not reach the bindings, or protoc wrote somewhere else.
if git -C "$HERE" rev-parse --git-dir >/dev/null 2>&1; then
  CHANGED="$(git -C "$HERE" status --porcelain -- "$OUT")"
  if [ -z "$CHANGED" ]; then
    echo "note: the bindings are byte-identical to what was already committed."
  else
    echo "$CHANGED"
    if [ -n "$MOVED" ]; then
      echo "note: the gencode moved UP to $MOVED. Set protobuf>=$MOVED in"
      echo "      pyproject.toml in this same commit, and raise the grpcio-tools"
      echo "      floor to the release you just used (ADR-0001: the gencode, the"
      echo "      runtime pin and the generator floor are one unit)."
    else
      echo "note: bump the protobuf pin in pyproject.toml in this same commit"
      echo "      (ADR-0001: the gencode and the runtime pin are one unit)."
    fi
  fi
fi

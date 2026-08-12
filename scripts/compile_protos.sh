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

# protoc exiting 0 having written nothing would otherwise reach the gate as an
# unexpanded glob, sail through it with nothing to compare, and die at the `cp`
# with a bare "No such file or directory". Name the actual problem here.
if [ ! -f "$STAGE/Preset_pb2.py" ]; then
  echo "error: the generator exited 0 but wrote no *_pb2.py into $STAGE." >&2
  echo "       Check whether the .proto files grew a \`package\` statement:" >&2
  echo "       protoc then writes into a subdirectory named after it, and this" >&2
  echo "       script (and the package's flat import layout) expect neither." >&2
  exit 1
fi

# Every generated file carries the version of the generator that wrote it:
#   # Protobuf Python Version: 7.35.1
# Both helpers below are single awk processes on purpose. The obvious spellings
# pipe into `head -1`, and under `set -o pipefail` a producer that gets SIGPIPE
# when head exits early fails the whole pipeline - which, inside `$(...)`, comes
# back as an empty string and reads as "not older". A gate that fails open is
# worse than no gate.
gencode_of() {
  [ -f "$1" ] || return 0
  awk '/^# Protobuf Python Version: /{print $NF; exit}' "$1"
}

# 0 when $1 is strictly older than $2, comparing dot-separated fields
# numerically. Absent fields count as 0, so 7.35 is older than 7.35.1.
older_than() {
  awk -v a="$1" -v b="$2" 'BEGIN {
    fields = split(a, left, ".")
    if (split(b, right, ".") > fields) fields = split(b, right, ".")
    for (i = 1; i <= fields; i++) {
      if (left[i] + 0 < right[i] + 0) exit 0
      if (left[i] + 0 > right[i] + 0) exit 1
    }
    exit 1
  }'
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
  # No file in the tree yet means a binding that is new in this change. There is
  # nothing to compare it against, and the pin check in tests/test_packaging.py
  # covers it once it lands.
  [ -f "$OUT/$name" ] || continue
  # "Missing" and "present but unstamped" are NOT the same answer, and reading
  # them as one is how this gate would let the worst case through: bindings from
  # a pre-stamp protoc carry no version line at all, so treating that as "new
  # file, nothing to compare" would wave in any generator at all. Refuse and say
  # so. Deleting the file is the deliberate way to say "yes, replace this".
  existing="$(gencode_of "$OUT/$name")"
  if [ -z "$existing" ]; then
    DOWNGRADES="$DOWNGRADES
  $name: the copy in the tree carries no version stamp, so nothing here can
    tell whether $new replaces it or downgrades it. Delete it and re-run if
    replacing it is what you mean."
  elif [ -z "$new" ]; then
    DOWNGRADES="$DOWNGRADES
  $name: tree has $existing, this generator stamps no version at all"
  elif older_than "$new" "$existing"; then
    DOWNGRADES="$DOWNGRADES
  $name: tree has $existing, this generator emits $new"
  elif [ "$new" != "$existing" ]; then
    MOVED="$new"
  fi
done

if [ -n "$DOWNGRADES" ]; then
  # "the tree", not "committed": the baseline is the file on disk in OUT, which
  # is not necessarily what is in HEAD.
  echo "error: this generator would DOWNGRADE the gencode in the tree.$DOWNGRADES" >&2
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
  echo "       emits the gencode above and raise the floor to it." >&2
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

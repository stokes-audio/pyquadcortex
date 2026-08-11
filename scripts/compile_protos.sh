#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$0")/.." && pwd)"
PROTO_SRC="$HERE/protocol/proto"
OUT="$HERE/pyquadcortex/protocol/proto"
mkdir -p "$OUT"
# Prefer the version-matched generator from the venv dev extra (grpcio-tools);
# fall back to system protoc for environments without the venv.
if [ -x "$HERE/.venv/bin/python" ]; then
  "$HERE/.venv/bin/python" -m grpc_tools.protoc -I "$PROTO_SRC" --python_out="$OUT" Preset.proto ProductionAutomation.proto
else
  protoc -I "$PROTO_SRC" --python_out="$OUT" Preset.proto ProductionAutomation.proto
fi
echo "Generated bindings in $OUT"

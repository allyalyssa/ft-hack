#!/usr/bin/env bash
# platform/swap_model.sh NAME
#
# Points platform/model.env at a different, already-pulled model, then
# runs platform/smoke_model.sh against it.

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_ENV="$SCRIPT_DIR/model.env"

if [ "$#" -ne 1 ]; then
  echo "usage: $0 NAME   (NAME must already be in \`ollama list\`)" >&2
  exit 1
fi
NAME="$1"

if ! command -v ollama >/dev/null 2>&1; then
  echo "ERROR: ollama is not on PATH" >&2
  exit 1
fi

if ! ollama list | awk 'NR>1 {print $1}' | grep -qxF "$NAME"; then
  echo "ERROR: '$NAME' is not in \`ollama list\`. Pull it first: ollama pull $NAME" >&2
  exit 1
fi

if [ ! -f "$MODEL_ENV" ]; then
  echo "ERROR: $MODEL_ENV does not exist yet. Run platform/setup_gb10.sh first." >&2
  exit 1
fi

if grep -q '^MODEL_NAME=' "$MODEL_ENV"; then
  sed -i.bak "s|^MODEL_NAME=.*|MODEL_NAME=${NAME}|" "$MODEL_ENV"
  rm -f "${MODEL_ENV}.bak"
else
  printf 'MODEL_NAME=%s\n' "$NAME" >> "$MODEL_ENV"
fi

echo "platform/model.env now has:"
cat "$MODEL_ENV"

echo
echo "running smoke test..."
"$SCRIPT_DIR/smoke_model.sh"

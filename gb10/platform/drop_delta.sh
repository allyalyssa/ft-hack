#!/usr/bin/env bash
# platform/drop_delta.sh - runs on the Mac. The live demo drop: copies
# data/exports/delta/* over data/exports/, then pushes with sync_once.sh.

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
DELTA_DIR="$REPO_ROOT/data/exports/delta"
EXPORTS_DIR="$REPO_ROOT/data/exports"

if [ ! -d "$DELTA_DIR" ]; then
  echo "ERROR: $DELTA_DIR does not exist. Role B owns data/exports/delta/." >&2
  exit 1
fi

if [ -z "$(ls -A "$DELTA_DIR")" ]; then
  echo "ERROR: $DELTA_DIR is empty, nothing to drop." >&2
  exit 1
fi

echo "copying $DELTA_DIR/* over $EXPORTS_DIR/ ..."
mkdir -p "$EXPORTS_DIR"
cp -rv "$DELTA_DIR"/. "$EXPORTS_DIR"/

echo "pushing to the GB10..."
"$SCRIPT_DIR/sync_once.sh"

#!/usr/bin/env bash
# platform/sync_once.sh - runs on the Mac. One push of data/exports/ to the
# GB10's inbox. Same env overrides as platform/sync.sh (HOST, USER, REMOTE_DIR).

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SRC="$REPO_ROOT/data/exports/"

HOST="${HOST:-192.168.2.2}"
USER="${USER:-$(id -un)}"
REMOTE_DIR="${REMOTE_DIR:-ft-hack/data/inbox/}"

rsync -az --delete-excluded "$LOCAL_SRC" "${USER}@${HOST}:${REMOTE_DIR}"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] synced $LOCAL_SRC -> ${USER}@${HOST}:${REMOTE_DIR}"

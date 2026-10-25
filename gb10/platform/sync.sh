#!/usr/bin/env bash
# platform/sync.sh - runs on the Mac, not the GB10.
#
# Pushes data/exports/ to the GB10's inbox every 120 seconds, forever, until
# Ctrl-C. For one push, use platform/sync_once.sh. For the live demo drop,
# use platform/drop_delta.sh.
#
# Env overrides:
#   HOST       - default 192.168.2.2 (the GB10 over the direct ethernet link)
#   USER       - default the current user
#   REMOTE_DIR - default ft-hack/data/inbox/
#                If the repo is mounted into the OpenShell sandbox at a
#                different path, override this to match, e.g.:
#                  REMOTE_DIR=/workspace/data/inbox/ platform/sync.sh
#   INTERVAL_S - default 120

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
LOCAL_SRC="$REPO_ROOT/data/exports/"

HOST="${HOST:-192.168.2.2}"
USER="${USER:-$(id -un)}"
REMOTE_DIR="${REMOTE_DIR:-ft-hack/data/inbox/}"
INTERVAL_S="${INTERVAL_S:-120}"

STOP=0
on_interrupt() { STOP=1; }
trap on_interrupt INT TERM

echo "syncing $LOCAL_SRC -> ${USER}@${HOST}:${REMOTE_DIR} every ${INTERVAL_S}s. Ctrl-C to stop."

while [ "$STOP" -eq 0 ]; do
  TS="$(date '+%Y-%m-%d %H:%M:%S')"
  rc=0
  rsync -az --delete-excluded "$LOCAL_SRC" "${USER}@${HOST}:${REMOTE_DIR}" || rc=$?
  if [ "$rc" -eq 0 ]; then
    echo "[$TS] synced"
  elif [ "$STOP" -eq 0 ]; then
    echo "[$TS] rsync failed (exit $rc), will retry in ${INTERVAL_S}s" >&2
  fi

  secs_left=$INTERVAL_S
  while [ "$secs_left" -gt 0 ] && [ "$STOP" -eq 0 ]; do
    sleep 1
    secs_left=$((secs_left - 1))
  done
done

echo "stopped"

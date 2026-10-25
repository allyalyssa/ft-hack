#!/usr/bin/env bash
# Laptop to box: copy an export bundle into the box's data/inbox/ every 2 minutes over the cable.
#
#   platform/sync.sh user@192.168.2.X                    # full export, data/exports/
#   SYNC_SRC=data/delta/ platform/sync.sh user@192.168.2.X   # the live drop
#   BOX_REPO=/workspace platform/sync.sh user@192.168.2.X    # repo somewhere other than ~/ft-hack on the box
set -u
BOX=${1:?usage: platform/sync.sh user@box}
SRC=${SYNC_SRC:-data/exports/}
DEST=${BOX_REPO:-ft-hack}/data/inbox/
EVERY=${SYNC_EVERY:-120}

while true; do
  if rsync -az --exclude .gitkeep "$SRC" "$BOX:$DEST"; then
    echo "$(date +%H:%M:%S) synced $SRC to $BOX:$DEST"
  else
    echo "$(date +%H:%M:%S) sync failed, will retry"
  fi
  sleep "$EVERY"
done

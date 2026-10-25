#!/usr/bin/env sh
# One agent cycle on the box. Cron or the OpenClaw schedule runs this every few minutes:
#   */2 * * * * cd /path/to/ft-hack && mkdir -p out && sh scripts/cycle.sh >> out/cycle.log 2>&1
# Cron has a bare environment: set PYTHON (and MODEL_* if not in platform/model.env) in the crontab if needed.
# Each step is a separate script; a failure or a step over STEP_TIMEOUT seconds stops the cycle and is logged.
set -e
cd "$(dirname "$0")/.."
PYTHON="${PYTHON:-python3}"
LIMIT="${LIMIT:-5}"
STEP_TIMEOUT="${STEP_TIMEOUT:-900}"
mkdir -p out
# agent/actions.py treats --limit 0 as "everything"; a cron cycle must never try to draft the whole queue.
case "$LIMIT" in ''|*[!0-9]*|0) echo "LIMIT must be a positive number, got '$LIMIT'"; exit 1 ;; esac

# Skip this run if the previous cycle is still going, so two cycles never write the queue at once.
if command -v flock >/dev/null 2>&1; then
  exec 9>out/cycle.lock
  flock -n 9 || { echo "$(date -u +%FT%TZ) previous cycle still running, skipped"; exit 0; }
else
  if ! mkdir out/cycle.lock.d 2>/dev/null; then
    echo "$(date -u +%FT%TZ) previous cycle still running (out/cycle.lock.d), skipped"
    exit 0
  fi
  trap 'rmdir out/cycle.lock.d' EXIT
fi

if command -v timeout >/dev/null 2>&1; then
  run() { timeout "$STEP_TIMEOUT" "$@"; }
else
  run() { "$@"; }
fi

step() {
  name="$1"; shift
  start=$(date +%s)
  if run "$@"; then
    echo "$(date -u +%FT%TZ) ok   $name ($(( $(date +%s) - start ))s)"
  else
    echo "$(date -u +%FT%TZ) FAIL $name after $(( $(date +%s) - start ))s"
    exit 1
  fi
}

echo "$(date -u +%FT%TZ) cycle start"
step triage   "$PYTHON" triage/triage.py
step recheck  "$PYTHON" triage/revalidate.py
step disputes "$PYTHON" disputes/disputes.py
step draft    "$PYTHON" agent/actions.py --limit "$LIMIT"
step post     "$PYTHON" channel/post.py
step summary  "$PYTHON" agent/summary.py
echo "$(date -u +%FT%TZ) cycle done"

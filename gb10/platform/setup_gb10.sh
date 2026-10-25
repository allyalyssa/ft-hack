#!/usr/bin/env bash
# platform/setup_gb10.sh
#
# Idempotent setup for the GB10 (Ubuntu-based DGX OS, aarch64, Docker
# preinstalled, systemd). Run as the normal user; it calls sudo itself for
# the steps that need root.
#
# Re-running this script is safe. Every step checks its own state first and
# prints "already done" instead of repeating work.
#
# Env overrides:
#   BIG_MODEL  - the large model to pull in the background. Default
#                gpt-oss:120b (65 GB, tool calling). The organisers asked
#                for the 128 GB to be in use while the agent runs, so the
#                big model is the demo model and stays resident. The 14 GB
#                gpt-oss:20b is the fallback if the download runs late:
#                  BIG_MODEL=gpt-oss:20b bash platform/setup_gb10.sh

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

BIG_MODEL="${BIG_MODEL:-gpt-oss:120b}"
SMALL_MODEL="qwen3:8b"
PULL_LOG=/tmp/pull-big.log
PULL_PID_FILE=/tmp/pull-big.pid
MODEL_ENV="$REPO_ROOT/platform/model.env"

section() { printf '\n==== %s ====\n' "$1"; }

model_pulled() {
  # $1: exact model tag. Matches the NAME column of `ollama list`, header skipped.
  command -v ollama >/dev/null 2>&1 || return 1
  ollama list | awk 'NR>1 {print $1}' | grep -qxF "$1"
}

### 1. Inventory #############################################################
section "Inventory"

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi || echo "(nvidia-smi exited non-zero, continuing)"
else
  echo "nvidia-smi: not found"
fi

if command -v docker >/dev/null 2>&1; then
  docker ps || echo "(docker ps exited non-zero, continuing - check docker group membership)"
else
  echo "docker: not found"
fi

if command -v ollama >/dev/null 2>&1; then
  ollama list || echo "(ollama list exited non-zero, continuing)"
else
  echo "ollama: not installed yet (next step installs it)"
fi

df -h /
free -g

### 2. Ollama install #########################################################
section "Ollama install"

if command -v ollama >/dev/null 2>&1; then
  echo "ollama already installed: $(command -v ollama)"
else
  curl -fsSL https://ollama.com/install.sh | sh
fi

### 3. Systemd override: OLLAMA_HOST=0.0.0.0 ##################################
section "Ollama systemd override (reachable from the sandbox, models stay resident)"

OVERRIDE_DIR=/etc/systemd/system/ollama.service.d
OVERRIDE_FILE="$OVERRIDE_DIR/override.conf"
OVERRIDE_SRC="$SCRIPT_DIR/ollama-override.conf"

if [ -f "$OVERRIDE_FILE" ] && cmp -s "$OVERRIDE_SRC" "$OVERRIDE_FILE"; then
  echo "override already present at $OVERRIDE_FILE, skipping restart"
else
  sudo mkdir -p "$OVERRIDE_DIR"
  sudo cp "$OVERRIDE_SRC" "$OVERRIDE_FILE"
  sudo systemctl daemon-reload
  sudo systemctl restart ollama
  sleep 2
  if ! systemctl is-active --quiet ollama; then
    echo "ERROR: ollama service is not active after restart. Check: sudo systemctl status ollama" >&2
    exit 1
  fi
  echo "override written, ollama restarted and active"
fi

### 4. Small model, foreground ################################################
section "Pull small model ($SMALL_MODEL, foreground)"

if model_pulled "$SMALL_MODEL"; then
  echo "$SMALL_MODEL already pulled"
else
  ollama pull "$SMALL_MODEL"
fi

### 5. Big model, background ##################################################
section "Pull big model ($BIG_MODEL, background)"

if model_pulled "$BIG_MODEL"; then
  echo "$BIG_MODEL already pulled"
elif [ -f "$PULL_PID_FILE" ] && kill -0 "$(cat "$PULL_PID_FILE")" 2>/dev/null; then
  echo "background pull of $BIG_MODEL already running (pid $(cat "$PULL_PID_FILE")), log: $PULL_LOG"
else
  nohup ollama pull "$BIG_MODEL" > "$PULL_LOG" 2>&1 &
  echo $! > "$PULL_PID_FILE"
  echo "started background pull of $BIG_MODEL (pid $!)"
  echo "log: $PULL_LOG  (tail -f $PULL_LOG to watch it)"
fi

### 6. platform/model.env #####################################################
section "platform/model.env"

if [ -f "$MODEL_ENV" ]; then
  echo "$MODEL_ENV already exists, leaving it as-is:"
  cat "$MODEL_ENV"
else
  cat > "$MODEL_ENV" <<EOF
MODEL_BASE_URL=http://127.0.0.1:11434/v1
MODEL_NAME=$SMALL_MODEL
EOF
  echo "wrote $MODEL_ENV:"
  cat "$MODEL_ENV"
fi

### 7. NemoClaw ################################################################
section "NemoClaw"

if command -v nemoclaw >/dev/null 2>&1; then
  echo "nemoclaw already on PATH: $(command -v nemoclaw)"
else
  echo "nemoclaw not found, running Express Install..."
  echo "NOTE: this installer is interactive. See the prompts below this run."
  curl -fsSL https://www.nvidia.com/nemoclaw.sh | bash
fi

cat <<'EOF'

NEXT STEPS, interactive, this script cannot answer these for you:
  - If Express Install just ran, it asks to confirm installing Node, OpenShell
    and the NemoClaw CLI/sandbox. Accept.
  - When it asks for an inference provider, choose Ollama and point it at
    http://127.0.0.1:11434 on this host.
  - When it asks for a sandbox name, use: collections
  - Then, from a shell on this box:
      openshell term                      # into the sandbox
      openclaw tui                        # confirm the agent answers from the local model
      nemoclaw collections policy-add     # lock egress, see platform/openshell_policy.md
  - Mount or clone this repo into the sandbox workspace so triage/ and agent/
    are visible to the agent inside it.
EOF

section "Done"
echo "Re-run this script any time; finished steps are skipped automatically."
echo "Background big-model pull log: $PULL_LOG"

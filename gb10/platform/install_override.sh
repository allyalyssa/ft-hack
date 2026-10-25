#!/usr/bin/env bash
# Installs platform/ollama-override.conf on the GB10 and restarts Ollama. Run on the box.
set -Eeuo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
sudo mkdir -p /etc/systemd/system/ollama.service.d
sudo cp "$here/ollama-override.conf" /etc/systemd/system/ollama.service.d/override.conf
sudo systemctl daemon-reload
sudo systemctl restart ollama
sleep 2
systemctl show ollama -p Environment --no-pager
curl -s http://127.0.0.1:11434/api/version && echo

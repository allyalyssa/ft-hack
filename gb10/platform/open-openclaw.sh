#!/bin/bash
# Opens the OpenClaw web chat for the collections sandbox with its token in the link.
export PATH=$HOME/.local/bin:$HOME/.nvm/versions/node/v22.23.4/bin:$PATH
U=$(nemoclaw collections dashboard-url --quiet 2>/dev/null | grep -Eo "http://[^ ]+" | tail -1)
if [ -z "$U" ]; then echo "could not get the OpenClaw link"; exit 1; fi
for b in xdg-open firefox google-chrome chromium; do
  if command -v $b >/dev/null 2>&1; then $b "$U" >/dev/null 2>&1 & echo "opened with $b"; exit 0; fi
done
echo "no browser found"

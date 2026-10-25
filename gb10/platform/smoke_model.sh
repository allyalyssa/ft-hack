#!/usr/bin/env bash
# platform/smoke_model.sh
#
# Smoke test for the model endpoint in platform/model.env:
#   1. GET {MODEL_BASE_URL}/models
#   2. One chat completion asking for a one-sentence collections email opener
# Prints the reply and the elapsed seconds for the chat completion.
# Exits non-zero on any error, or if the chat completion takes over 20s.

set -Eeuo pipefail
trap 'echo "FAILED at line $LINENO: $BASH_COMMAND" >&2' ERR

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MODEL_ENV="$SCRIPT_DIR/model.env"
TIMEOUT_S=20

if [ ! -f "$MODEL_ENV" ]; then
  echo "ERROR: $MODEL_ENV not found. Run platform/setup_gb10.sh (or platform/swap_model.sh) first." >&2
  exit 1
fi

# model.env is plain KEY=value shell syntax, see platform/model.env.example.
# shellcheck disable=SC1090
set -a
. "$MODEL_ENV"
set +a

: "${MODEL_BASE_URL:?MODEL_BASE_URL not set in $MODEL_ENV}"
: "${MODEL_NAME:?MODEL_NAME not set in $MODEL_ENV}"

echo "MODEL_BASE_URL=$MODEL_BASE_URL"
echo "MODEL_NAME=$MODEL_NAME"

REQUEST_FILE="$(mktemp)"
RESPONSE_FILE="$(mktemp)"
cleanup() { rm -f "$REQUEST_FILE" "$RESPONSE_FILE"; }
trap cleanup EXIT

echo
echo "-- GET $MODEL_BASE_URL/models --"
curl -fsS --max-time "$TIMEOUT_S" "$MODEL_BASE_URL/models"
echo

echo
echo "-- chat completion --"
python3 - "$MODEL_NAME" > "$REQUEST_FILE" <<'PY'
import json
import sys

model = sys.argv[1]
payload = {
    "model": model,
    "messages": [
        {
            "role": "user",
            "content": (
                "Write one sentence to open a collections email about a small "
                "past-due invoice. Firm but polite. Reply with only the sentence."
            ),
        }
    ],
    "stream": False,
}
json.dump(payload, sys.stdout)
PY

SECONDS=0
if ! curl -fsS --max-time "$TIMEOUT_S" "$MODEL_BASE_URL/chat/completions" \
    -H "Content-Type: application/json" \
    -d @"$REQUEST_FILE" \
    -o "$RESPONSE_FILE"; then
  echo "ERROR: chat completion request failed" >&2
  exit 1
fi
ELAPSED=$SECONDS

python3 - "$RESPONSE_FILE" <<'PY'
import json
import sys

with open(sys.argv[1]) as f:
    data = json.load(f)

try:
    print(data["choices"][0]["message"]["content"].strip())
except (KeyError, IndexError, TypeError) as exc:
    print(f"ERROR: unexpected response shape: {exc}\n{json.dumps(data)[:500]}", file=sys.stderr)
    sys.exit(1)
PY

echo
echo "elapsed: ${ELAPSED}s"

if [ "$ELAPSED" -gt "$TIMEOUT_S" ]; then
  echo "ERROR: chat completion took ${ELAPSED}s, over the ${TIMEOUT_S}s budget" >&2
  exit 1
fi

echo "OK, ${ELAPSED}s within the ${TIMEOUT_S}s budget"

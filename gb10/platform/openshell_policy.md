# OpenShell egress policy (role A)

Goal: the only thing that ever leaves the box is the approval message to
Slack (or Telegram). The sandbox also needs one local-network exception,
the host's Ollama port, which is not internet egress but is blocked by
the same policy layer. Sandbox name throughout: `collections`.

## Lock it down

`roles/A-platform.md` and this project's brief name the command as
`nemoclaw collections policy-add`. Fetching `docs.nvidia.com/nemoclaw/latest/`
today (2026-10-25) confirmed the install command and that the product's own
examples reference `slack.com` and `api.telegram.org` as endpoints, but
every guessed CLI reference sub-page (`/cli`, `/quickstart`) returned a 404.
**The exact flags for `policy-add` are unverified.** Draft, confirm with
`nemoclaw collections policy-add --help` on the box first:

```
nemoclaw collections policy-add --endpoint slack.com:443 --access read-only
# Telegram instead of Slack:
nemoclaw collections policy-add --endpoint api.telegram.org:443 --access read-only
```

### Verified fallback: the OpenShell layer underneath

NemoClaw's sandbox is an OpenShell sandbox. Fetching
`docs.nvidia.com/openshell/latest/how-it-works/policies/manage-policies`
today confirmed this syntax directly, quoted as found:

```
openshell policy update collections \
  --rule-name slack_access \
  --binary /usr/bin/curl \
  --add-endpoint slack.com:443:read-only:rest:enforce \
  --wait
```

Endpoint format is `host:port:access_preset:protocol:enforcement_mode`.
Add Telegram the same way, as its own `--add-endpoint` on the same rule or
a second rule:

```
openshell policy update collections \
  --rule-name slack_access \
  --binary /usr/bin/curl \
  --add-endpoint api.telegram.org:443:read-only:rest:enforce \
  --wait
```

Use `--dry-run` instead of `--wait` to preview a change before applying it.

### The Ollama exception

The sandbox reaching the host's Ollama port is local, not public egress,
but it still needs an explicit rule or `agent/actions.py` will fail with
exactly the "OpenShell policy blocking Ollama" failure called out in
`roles/A-platform.md` (risk 3) and `platform/RUNBOOK.md`'s troubleshooting
table:

```
openshell policy update collections \
  --rule-name ollama_local \
  --binary /usr/bin/curl \
  --add-endpoint 127.0.0.1:11434:read-only:rest:enforce \
  --wait
```

Two things in that line are unverified, confirm live:
- `read-only` may be wrong for a chat completion, which is a POST, not a
  read. Check the full preset list with `openshell policy update --help`
  or the Network Rules page before trusting this for the demo.
- If the sandbox has its own network namespace, `127.0.0.1` inside it
  will not reach the host. It may need the host's real LAN IP or a
  gateway address instead; test with `curl` from inside `openshell term`
  before the first end-to-end run.

### The dashboard exception

The OpenClaw skill talks to the host dashboard over HTTP (agent/skills/collections/SKILL.md), so the sandbox also needs the host on port 8090. Use the docker bridge address the skill defaults to:

```
openshell policy update collections \
  --rule-name dashboard_local \
  --binary /usr/bin/curl \
  --add-endpoint 172.17.0.1:8090:read-write:rest:enforce \
  --wait
```

Same caveat as the Ollama rule: confirm the preset name with `openshell policy update --help` and test with `curl http://172.17.0.1:8090/api/summary` from inside `openshell term`.

## Show the policy on screen during the demo

Confirmed commands, same source as above:

```
openshell policy get collections --full
openshell policy list collections
```

Run one of these and leave it on screen right before pulling the uplink
cable, so the judges see the single allowed host for themselves, matching
the demo storyline in `PLAN.md`.

## Sources

- `curl -LsSf https://raw.githubusercontent.com/NVIDIA/OpenShell/main/install.sh | sh`
  and `openshell sandbox create --name demo`, confirmed from the
  `github.com/NVIDIA/OpenShell` README, fetched 2026-10-25.
- `openshell policy get / update / list`, including the `--add-endpoint`
  syntax and the `host:port:preset:protocol:mode` format, confirmed from
  `docs.nvidia.com/openshell/latest/how-it-works/policies/manage-policies`,
  fetched 2026-10-25.
- `nemoclaw collections policy-add`, the command name is what this
  project's own brief and `roles/A-platform.md` specify; its flags were
  not confirmed anywhere on `docs.nvidia.com/nemoclaw/latest/` today. The
  OpenShell fallback above is the one to trust if it does not behave as
  written.

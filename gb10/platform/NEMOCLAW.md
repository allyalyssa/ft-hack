# NemoClaw on the GB10: what runs and how it was fixed

State on 2026-10-25, box `promaxgb10-8825`, user `dell`. Everything below is live on the box.

## What runs

| Piece | Where | Starts on reboot |
|---|---|---|
| Triage + drafting loop, every 2 min | host cron, `platform/crontab.gb10` | yes, cron |
| Approval desk with the floating agent chat | host, `channel/dashboard.py` on `0.0.0.0:8090` | yes, `@reboot` |
| Ollama with `gpt-oss-120b-64k` (chat) and `gpt-oss-20b-64k` (drafting), both pinned | host systemd | yes |
| Relay, sandbox bridge to Ollama | host, `platform/ollama_relay.py` on `172.18.0.1:11436` | yes, `@reboot` |
| OpenClaw agent | NemoClaw sandbox `collections` (OpenShell, docker) | NemoClaw gateway |
| OpenClaw web chat | `http://127.0.0.1:18789` on the box, open with `bash platform/open-openclaw.sh` | NemoClaw forward |

The dashboard's chat panel runs `openshell sandbox exec -- openclaw agent ... -m <question>` per question, in a fresh session, and shows the answer. The agent answers through the collections skill, which only talks to the dashboard's JSON API over `host.openshell.internal:8090`.

## Onboarding answers that worked

`nemoclaw onboard` with: OpenClaw, provider Local Ollama (option 8), model `qwen3:8b`, sandbox `collections`, no web search, no messaging, no resource profile, Balanced policy. Skip Express on the Spark (`NEMOCLAW_NO_EXPRESS=1`), it pulls a second 20 GB model into vLLM that reserves most of the memory.

Before onboarding: the dashboard must not sit on 8080, the OpenShell gateway owns that port.

## The fixes, in order

1. **Credential went stale after `nemoclaw inference set`.** Every model call returned HTTP 500 `credential_unavailable`. `nemoclaw collections rebuild` failed its preflight (GHCR catalog fetch, then a sudo-only port check). Fix: the relay above plus a direct OpenClaw provider.
   - `openshell policy update collections --rule-name local_ollama_relay --binary /usr/local/bin/node --binary /usr/bin/node --binary /usr/bin/curl --binary /usr/local/bin/curl --add-endpoint "host.openshell.internal:11436:read-write:rest:enforce:allowed-ip=172.16.0.0/12" --wait`
   - In `/sandbox/.openclaw/openclaw.json`: provider `local` with `baseUrl http://host.openshell.internal:11436/v1`, `api openai-completions`, models `gpt-oss-120b-64k` and `gpt-oss-20b-64k`, and `agents.defaults.model.primary = local/gpt-oss-120b-64k`.
2. **Dashboard reachable from the sandbox.** `openshell policy update collections --rule-name collections_dashboard --binary /usr/bin/curl --binary /usr/local/bin/curl --add-endpoint "host.openshell.internal:8090:read-write:rest:enforce:allowed-ip=172.16.0.0/12" --wait`
3. **Skill location.** Upload the folder, not the file: `openshell sandbox upload collections agent/skills/collections /sandbox/.openclaw/skills`. Check with `openclaw skills list` in the sandbox.
4. **"exec host=sandbox requires a sandbox runtime".** `openclaw config set tools.exec.host gateway` in the sandbox.
5. **Malformed tool calls and cut-off replies.** OpenClaw's prompt is about 16k tokens and Ollama loaded the model with 16k context. Fixes: `ollama create gpt-oss-20b-64k` and `gpt-oss-120b-64k` from a Modelfile with `PARAMETER num_ctx 65536`; in `openclaw.json` set `tools.toolSearch = false` and `tools.allow = ["exec", "read"]`. The 20b still produced broken tool calls under OpenClaw, the 120b does not, so chat runs on the 120b.
6. **Port 18789 held by an orphaned forward** from the onboarding terminal: kill that `openshell ... forward service collections` process, then `nemoclaw collections recover`.

## Memory

Both models stay loaded (`keep_alive -1`, re-pinned every 3 minutes by cron). About 88 GB of 121 GB in use while the agent runs.

## Not done

- Telegram or Slack through NemoClaw: `nemoclaw collections channels add` triggers the same rebuild that fails here.
- The reboot test. Part of NemoClaw was started from an interactive terminal, so check `nemoclaw collections status` after any reboot.

## Team build on the box (from 16:30)

The box now serves the root build from GitHub main in `~/ft-hack-git`, with the NemoClaw agent added:

| Port | What | Started by |
|---|---|---|
| 8090 | Collex workspace, `app/server.py --port 8090 --approvals-port 8092`; it inlines the agent chat widget and passes `/api/agent/*` to 8091 | `@reboot` in `gb10/platform/crontab.team` |
| 8091 | Agent desk, `channel/agent_desk.py` (same code as `gb10/channel/dashboard.py`): agent chat jobs, the JSON API the OpenClaw skill reads, the widget script | `@reboot` |
| 8092 | Root approvals dashboard, `channel/dashboard.py --port 8092` (8080 belongs to the OpenShell gateway) | `@reboot` |

- The cycle is `platform/run.py --once` every 2 minutes, drafting on `gpt-oss-20b-64k` (`platform/model.env` on the box only).
- The chat panel never makes the agent run commands. The desk looks up the summary, any invoice named in the question or selection, the top 10 awaiting review and the 5 oldest, and hands that to OpenClaw with the question. One pass, about 20 seconds on the 120b.
- Typing `approve INV-...` or `reject INV-... because ...` in the chat is recorded by the desk under the reviewer's name. The agent stays read-only.
- The OpenClaw skill reads `host.openshell.internal:8091` (policy rule `collections_agent_desk`). In the OpenClaw web UI each command still asks for approval, by design.
- `gb10/platform/rollback-to-gb10.sh` puts the box back on the gb10 build.

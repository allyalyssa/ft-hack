> **This folder is the build running on the GB10 right now** (approval desk on :8090 with the floating agent chat, cron loop, NemoClaw sandbox `collections`). It sits beside the root build so nothing at the root changed. `platform/NEMOCLAW.md` has every fix that made NemoClaw work. Pull features from here into the final build.

# Small-Dollar Collections Agent on the GB10

Collections teams work tens of thousands of open invoices, and the $40 ones
get the same queue as the $140,000 ones until nobody gets to them. This
agent triages the small invoices with plain rules, drafts the customer
email, Slack message, internal note, and Salesforce case with a local
model, and posts each package to Slack for one-click approval. Every run
also writes a write-off memo for the invoices policy says to close outright.
Built in one day for the Dell x NVIDIA AI Hackathon, SF, 2026-10-25.

## Architecture

```
 Mac (laptop)                       GB10 (DGX OS, aarch64)
 ------------                       --------------------------------
 data/exports/                      data/inbox/
     |  rsync, direct ethernet          |
     |  every 120s (platform/)          v
     +------------------------->    triage/triage.py --> out/queue.json
                                        |
                                        v
                                    agent/actions.py (OpenClaw in an
                                    OpenShell sandbox, model on Ollama,
                                    127.0.0.1:11434)
                                        |
                                        v
                                    channel/post.py --egress (one host)--> Slack
                                        |
                                        v
                                    local dashboard, http://<gb10-ip>:8090
```

## Quickstart by role

- **A, platform** (this folder): `bash platform/setup_gb10.sh` on the GB10,
  `bash platform/sync.sh` on the Mac. Full checklist: `platform/RUNBOOK.md`.
- **B, data and triage**: `python3 triage/triage.py`, writes `out/queue.json`.
- **C, agent and approval**: `python3 agent/actions.py --limit 5`, writes
  `out/actions/<invoice_id>/` and posts to the channel.
- **D, video and deck**: reads `out/summary.json` for the numbers.

See `PLAN.md` for the brief and `CONTRACT.md` for the file formats every
role reads and writes.

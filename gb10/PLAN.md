# Small-Dollar Collections Agent on the GB10

Dell x NVIDIA AI Hackathon, SF, 2026-10-25. Build 10:00 to 18:00, submissions close 18:00, top 8 demo live at 18:30.
Judging, verbatim from the Luma page: technical execution, business value, local-first design, demo quality.
Hard rules: live demo on the GB10, no remote LLM calls at runtime. Suggested stack: NemoClaw, OpenClaw, OpenShell, a Slack, Discord or Telegram channel.

## What it does

Collections teams work tens of thousands of open invoices. Collectors give the same effort to a $40 invoice as a $140,000 one, and the small ones get lost. This agent owns the small ones.

Every cycle it:
1. Ingests the latest exports dropped onto the box (Salesforce invoice export, the collector's Google Sheet, Slack thread export).
2. Triages with plain rules, no model: past due over 30 days, no touch in the last 8 days, amount under the collector threshold. Three buckets: write off (tiny, aged, per policy), agent works it (small), escalate to a human (over threshold, or disputed).
3. For the agent bucket, the local model drafts the customer email, the customer Slack message, the internal note to the account owner, and the Salesforce case. For the write-off bucket it drafts the write-off memo.
4. Posts each package to the approval channel. A collector approves, edits or rejects with one click. Nothing goes to a customer without approval.
5. Posts a daily summary (heartbeat): queue size, drafted, approved, written off, dollars touched.

Terminology in the original brief, confirmed by Mohammed: AR teams, Slack, invoices, touched. The agent opens Salesforce cases, not pages.

## How it maps to the criteria

| Criterion | What the judges see |
|---|---|
| Technical execution | OpenClaw inside an OpenShell sandbox, local model on Ollama or vLLM, cron-driven loop, deterministic triage with tests, mock CRM with real records written |
| Business value | Mohammed is a CPA who does this work. Numbers on screen: invoices in the long tail, hours per week they cost, dollars written off under policy with a memo trail, dollars recovered by drafts collectors never had time to send |
| Local-first | Model, data, CRM, triage all on the box. OpenShell egress policy allows only the Slack or Telegram API. We pull the uplink cable during the demo and it keeps working off the local dashboard |
| Demo quality | Live drop of a new export, agent wakes on its 2-minute cron, packages appear in Slack, one click approves, Salesforce case updates. Under 3 minutes |

## Roles, three builders plus video

| Role | Owner | Folder | Deliverable by 16:30 |
|---|---|---|---|
| A. Platform | Mohammed (OpenClaw expert) | `platform/` | GB10 reachable from a Mac, model pulled, NemoClaw sandbox running OpenClaw, Ollama bound to 0.0.0.0, egress policy locked, cron job firing, sync from laptop landing in `data/inbox/`, survives a reboot |
| B. Data and triage | Builder 2 | `data/`, `triage/` | Synthetic exports at realistic scale (20k invoices, long tail under $500, cents invoices, aging), `triage.py` producing `out/queue.json`, tests, the headline numbers for the deck |
| C. Agent actions and approval | Builder 3 | `agent/`, `channel/` | OpenClaw skill plus prompts, `actions.py` that drafts the five artifacts per invoice against the local endpoint, mock Salesforce (SQLite plus one-page web view), Slack or Telegram posting, local approval dashboard as fallback |
| D. Video and deck | Builder 4 | `demo/` | Storyline below, 90-second video, 6-slide deck. Pull numbers from `out/summary.json` |

Integration contract is in `CONTRACT.md`. Build to it and the three streams meet without a merge fight.

## Timeline (PDT)

- 11:45 Roles agreed. A starts the model pull immediately, it is the critical path. B and C build against qwen3:8b on their laptops until the box is ready.
- 13:00 Lunch. Triage running on synthetic data. Agent drafting on laptop. Box has the model.
- 14:30 First end-to-end run on the GB10: drop export, cron fires, packages in channel.
- 16:00 Freeze features. Reboot the box once, confirm it comes back on its own. Record the demo video.
- 17:15 Rehearse the live demo twice. Submission package: repo, video, deck.
- 18:00 Submit.

## Memory (organiser request, 11:50)

The organisers want the 128 GB in use while the agent runs. The box keeps every loaded model resident (`OLLAMA_KEEP_ALIVE=-1`, up to 4 models, 32k context): gpt-oss:120b drafts, gpt-oss:20b does the fast per-invoice classification and summaries, qwen3:8b stays as the smoke-test model. That is about 85 GB of weights plus KV cache before the sandbox and the dashboard. `free -g` and `nvidia-smi` go on screen during the demo.

## Risks, in order

1. Model download. The GB10 has no Wi-Fi and nothing is preloaded. Everything comes down the cable from a laptop on venue Wi-Fi or a phone hotspot. Start the pull in the first 15 minutes: qwen3:8b (5 GB) first so B and C can integrate on the box, then gpt-oss:20b (14 GB, tool calling) as the demo model. gpt-oss:120b (65 GB) only if the link is fast and the 20b landed early. Nemotron 3 Super 120B is 87 GB and 30 to 90 seconds a response (NVIDIA's own number), too slow for a live loop.
2. Channel egress. Slack and Telegram need the box to reach the internet through the laptop. Build the local dashboard first, the channel second. The demo works with the dashboard alone.
3. OpenShell policy blocking Ollama. NVIDIA's doc covers it: bind Ollama to 0.0.0.0 and add the policy. Mohammed has done this class of debugging for clients.
4. Prompt quality from a small model. Keep the drafts templated: the model fills facts into a fixed shape, it does not freewheel. Few-shot examples in `agent/prompts/`.

## Demo storyline (for D)

1. The pile. 20,000 open invoices. 14,000 under $500. 3,100 of those have had no touch in 8 days and are over 30 days past due. Nobody gets to them.
2. A new export drops onto the box. The agent wakes on its 2-minute cron. Triage runs in under a second. Three buckets on screen.
3. The local model drafts. Packages land in Slack. Open one: customer email, Slack message, note to the account owner, Salesforce case, all referencing the real invoice facts.
4. One click approves. The Salesforce record updates. The write-off memo for a $0.37 invoice sits in the audit trail.
5. Pull the uplink cable. The next cron fires. It keeps working. The OpenShell policy on screen shows the only thing that ever leaves the box is the approval message.
6. The number: hours a week handed back to collectors, dollars touched that were never going to be touched.

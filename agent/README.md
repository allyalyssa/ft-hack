# Role C: agent actions and approval

Turns `out/queue.json` into drafted packages a collector approves. Python 3.10+, standard library only, no network
except the local model endpoint. Nothing is sent to a customer; approval sets `status.json` to `approved` and that is
what a connected sender would act on.

```
python3 agent/actions.py --limit 5                 # draft the next 5 invoices that need it
python3 agent/actions.py --invoice INV-116288      # draft one
python3 agent/actions.py --dry-run --queue agent/fixtures/queue.sample.json --limit 6
python3 channel/dashboard.py                       # http://<box>:8080, approve / edit / reject
python3 agent/status.py list | show ID | approve ID --by NAME | reject ID --by NAME
python3 agent/summary.py                           # refresh out/summary.json, post the heartbeat
python3 channel/post.py                            # post new packages to Slack or Telegram if configured
```

## What gets drafted

| Bucket | Files in `out/actions/<invoice_id>/` |
|---|---|
| agent | `customer_email.md`, `customer_slack.md`, `internal_note.md`, `salesforce_case.json`, `status.json` |
| writeoff | `writeoff_memo.md`, `internal_note.md`, `salesforce_case.json`, `status.json` |
| escalate | nothing, disputed invoices go to a person |

Each folder also keeps `queue_item.json` (the triage row it was drafted from) so the dashboard can show an invoice after
it leaves the queue. Every case is also a row in `agent/crm_mock/crm.sqlite` (tables `invoices`, `cases`), shown at
`/crm` on the dashboard.

## How a draft is made

1. The invoice facts are rendered as plain `key: value` lines in Python: amounts, dates, days past due and a pay-by date
   (7 days after the snapshot, moved to a weekday). The model never computes a number.
2. One chat call to `MODEL_BASE_URL/chat/completions` returns a JSON object with every text field. The prompt and two
   worked examples per bucket are in `agent/prompts/`.
3. Each field is checked: the invoice number and balance are present, the email has the pay-by date, the Slack message
   is under 600 characters, the owner note asks a question, there is no legal or fee language, and no dollar amount
   appears that is not in the facts. Failing fields get one retry with the problems listed. Anything still failing
   uses a fixed template and is listed in `template_fields` in `salesforce_case.json` and tagged on the dashboard.

## Idempotency

`action_id = <invoice_id>:<case_type>:<snapshot_id>`, where `snapshot_id` is the queue's `snapshot_id` or else its
`as_of`. An invoice is skipped when a person has approved or edited it, or when it already has a draft for the same
`action_id`. A new snapshot redrafts invoices that are still only drafted or were rejected. A per-invoice lock file
keeps overlapping cron runs and the dashboard's Draft now button from drafting the same invoice twice.

## For the box (role A)

- Model: `platform/model.env` with `MODEL_BASE_URL` and `MODEL_NAME`. Environment variables of the same names override
  it, so `MODEL_NAME=gpt-oss:20b python3 agent/actions.py --invoice INV-116288` tests the big model without editing the
  file. qwen3 gets `/no_think`; gpt-oss gets `reasoning_effort: low`.
- Cron: `python3 triage/triage.py && python3 agent/actions.py --limit 5 && python3 channel/post.py`.
- Heartbeat: `python3 agent/summary.py`.
- Dashboard: run `python3 channel/dashboard.py` once and leave it up. It binds 0.0.0.0:8080 so the Mac can open it
  over the cable. If it runs inside the OpenShell sandbox, port 8080 has to be forwarded to the host.
- Skill: copy or link `agent/skills/collections/` into the OpenClaw workspace `skills/` folder.
- Channel (optional): `platform/channel.env` with `SLACK_WEBHOOK_URL`, or `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`,
  and `DASHBOARD_URL`. With no egress it logs one line and carries on.
- Throughput: drafting takes seconds per invoice, so the full queue (4,571 draftable) cannot be drafted before the
  demo. The dashboard lists every queued invoice and any of them can be drafted on the spot with Draft now.

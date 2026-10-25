# Integration contract

Everything is files under the repo root. Role B writes `out/queue.json`, role C reads it. Role A moves files in and schedules the runs. Nobody imports another role's code.

## Inputs, `data/inbox/` (role A syncs these in from the laptop every 2 minutes)

`salesforce_invoices.csv`
```
invoice_id,account_id,account_name,account_owner,account_owner_email,amount,currency,invoice_date,due_date,status,last_touch_date,last_touch_channel,contact_name,contact_email,contact_slack
```
`status` is one of open, partially_paid, disputed, paid. Dates are ISO `YYYY-MM-DD`. `last_touch_date` may be empty.

`collector_sheet.csv`
```
invoice_id,collector,note,note_date,promise_to_pay_date,dispute_flag
```
`dispute_flag` is `true` or `false`. One invoice may have many rows.

`slack_threads.json`
```json
[{"invoice_id": "INV-0001", "channel": "#ar-collections", "messages": [{"ts": "2026-10-20T14:02:00", "user": "jdoe", "text": "..."}]}]
```

`policy.yaml`
```yaml
as_of: 2026-10-25
min_days_past_due: 30
stale_touch_days: 8
write_off_max_amount: 25.00
agent_max_amount: 500.00
currency: USD
```

## Triage output, `out/queue.json` (role B writes, role C reads)

```json
{
  "as_of": "2026-10-25",
  "totals": {"invoices": 20000, "open": 17400, "under_agent_max": 14000, "queued": 3100, "writeoff": 420, "agent": 2500, "escalate": 180, "dollars_queued": 412000.55},
  "items": [
    {
      "invoice_id": "INV-0001",
      "bucket": "agent",
      "reasons": ["past_due_61_days", "no_touch_19_days", "amount_under_500"],
      "invoice": {"...": "the salesforce row as an object"},
      "last_touch": {"date": "2026-10-06", "channel": "email", "source": "salesforce"},
      "notes": [{"collector": "...", "note": "...", "note_date": "..."}],
      "slack": [{"ts": "...", "user": "...", "text": "..."}],
      "conflicts": ["sheet says touched 2026-10-20, salesforce says 2026-10-06"]
    }
  ]
}
```
`bucket` is one of `writeoff`, `agent`, `escalate`. Sorted by amount descending within bucket. `last_touch` is the latest date across all three sources.

## Agent output, `out/actions/<invoice_id>/` (role C writes)

```
customer_email.md       subject line first, blank line, body
customer_slack.md       one message, under 600 characters
internal_note.md        to the account owner, what happened, what we asked, what we need from them
salesforce_case.json    {"invoice_id", "case_type": "collections_outreach"|"writeoff", "summary", "next_action", "next_action_date", "drafted_at", "model"}
writeoff_memo.md        only for the writeoff bucket: policy clause, amount, age, approver line
status.json             {"state": "drafted"|"approved"|"rejected"|"edited", "by": "", "at": ""}
```

`out/summary.json` after every run: counts per bucket, dollars per bucket, drafted, approved, run duration, model name. Role D reads this for the deck.

## Model endpoint (role A provides, role C consumes)

OpenAI-compatible, on the box: `http://127.0.0.1:11434/v1` (Ollama) or `http://127.0.0.1:8000/v1` (vLLM). Model name in `platform/model.env` as `MODEL_NAME`, base URL as `MODEL_BASE_URL`. Role C reads these two variables and nothing else.

## Schedule (role A)

OpenClaw cron, every 2 minutes for the demo: `python triage/triage.py && python agent/actions.py --limit 5`. Heartbeat daily: `python agent/summary.py` posts `out/summary.json` to the channel. Full data run once at start with no limit, so the dashboard has a real backlog to show.

## Snapshot and idempotency (from Alyssa's design in Discord)

Each sync is one snapshot. The sync writes `data/inbox/manifest.json`:
```json
{"snapshot_id": "2026-10-25T1430", "exported_at": "2026-10-25T14:30:00", "source_files": ["salesforce_invoices.csv", "collector_sheet.csv", "slack_threads.json"]}
```
Triage carries `snapshot_id` into `out/queue.json` (top level, next to `as_of`) and uses `exported_at` as the date for all age math when present, else `policy.yaml` `as_of`.

Every drafted action has `action_id = "<invoice_id>:<action>:<snapshot_id>"`, written into `salesforce_case.json` and `status.json`. `actions.py` skips an invoice when an action with the same `action_id` already exists, so a bundle pushed twice never drafts twice. A new snapshot with the invoice still queued produces a new `action_id`, and the dashboard shows the latest one.

The connected side re-checks last touch in the live system before anything is sent. In the demo that check is `status.json` `state == "approved"`, which only the dashboard or the channel can set.

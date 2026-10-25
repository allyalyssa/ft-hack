# Triage

Picks the invoices that need a touch today from the rules in `policy.yaml`. No model.

    python3 triage/triage.py                    # data/inbox/, or data/exports/ when the inbox has no export
    python3 triage/triage.py --input data/exports/delta --out /tmp/delta --actions out/actions
    python3 -m unittest triage/test_triage.py

It joins the Salesforce export, the collector sheet and the Slack threads on `invoice_id`. Last touch is the newest of `last_touch_date`, the latest sheet `note_date`, the latest Slack `ts`, and the `at` of an approved or edited agent package in `out/actions/<invoice_id>/status.json` (source `agent`). An invoice the agent just worked leaves the queue for `stale_touch_days`. Drafted and rejected packages do not count. An invoice is queued when its status is not `paid`, it is at least `min_days_past_due` (30) days past due, it has had no touch for `stale_touch_days` (8) days or was never touched, and its amount is above zero and at most `agent_max_amount` ($500).

Buckets, in order: `writeoff` at or under `write_off_max_amount` ($25), even when disputed. `escalate` if Salesforce says `disputed` or any sheet note has `dispute_flag` true. Everything else is `agent`.

A conflict is a latest sheet note date that differs from Salesforce, or a sheet dispute on an invoice Salesforce shows as open.

When the source folder has `manifest.json`, its `snapshot_id` goes into the outputs and the date of `exported_at` replaces `as_of` for all age math. Without one, `snapshot_id` is `local`.

Every run rewrites:

- `out/queue.json`: the CONTRACT.md shape, writeoff then agent then escalate, largest amount first. Items also carry `days_past_due` and `days_since_touch`, and the file carries the `policy` used. One item per line, so `grep INV-02137 out/queue.json` finds it.
- `out/headline.json`: the slide 2 numbers. Hours assume 6 minutes per touch and one touch per queued invoice per week.

It prints one summary line ending in the elapsed time, about 0.1 seconds for 20,000 invoices.

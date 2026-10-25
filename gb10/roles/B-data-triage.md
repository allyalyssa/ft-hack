# Role B, Data and triage

Goal: a believable 20,000-invoice world and a deterministic triage that produces `out/queue.json` per CONTRACT.md. No model anywhere in this role.

## Generator, `data/generate.py`
- 20,000 invoices across ~1,200 accounts and 12 account owners. Amounts heavy-tailed: ~70% under $500, ~3% under $5 including cents-only invoices ($0.37, $1.12) that should never have been issued. A few hundred over $100,000.
- Status mix: open, partially_paid, disputed, paid. Aging from 0 to 180 days past due.
- `last_touch_date` empty for a realistic share. Collector sheet notes for a subset, some contradicting Salesforce (sheet says touched this week, Salesforce says three weeks ago). Slack threads for ~300 invoices.
- Seeded random so the numbers in the deck stay stable. Writes to `data/exports/`.
- Also `data/generate.py --delta` writes a small new export (200 invoices, a few newly aged past 30 days) so role A can drop it live.

## Triage, `triage/triage.py`
- Reads `data/inbox/` (falls back to `data/exports/` if inbox is empty), joins the three sources on `invoice_id`.
- Latest touch across all sources. Record conflicts.
- Queue if: status not paid, days past due >= `min_days_past_due`, days since last touch >= `stale_touch_days` (or never touched), amount <= `agent_max_amount`.
- Bucket: `writeoff` if amount <= `write_off_max_amount`; `escalate` if `dispute_flag` or status disputed; else `agent`.
- Writes `out/queue.json` with `totals` and `items`, prints a one-line summary. Must run in under 2 seconds on 20k rows. Stdlib only (csv, json, datetime) plus PyYAML if available, else parse the five policy lines by hand.

## Tests, `triage/test_triage.py`
Small fixtures: boundary at exactly 30 days, exactly 8 days, cents invoice, disputed small invoice escalates, paid invoice never queues, conflict detection. `python -m pytest triage/` or plain `python triage/test_triage.py`.

## Headline numbers for role D
Print and save to `out/headline.json`: total invoices, open, under threshold, queued, per bucket counts and dollars, and an hours estimate at 6 minutes per touch. These are the slide-2 numbers.

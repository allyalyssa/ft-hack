# Collections agent

A collections agent that runs offline on an NVIDIA GB10. It finds the small invoices that collectors have lost track
of, drafts the follow-ups and proposes write-offs, all for a person to approve.

## Role B: data and triage

No model is used anywhere in this part. Python 3.10+ and the standard library only (PyYAML is used if installed).

```
python data/generate.py            # 20,000-invoice fake world -> data/exports/
python triage/triage.py            # -> out/queue.json and out/headline.json
python -m pytest triage/           # or: python triage/test_triage.py
```

### Live drop

```
python data/generate.py --delta    # 200 invoices, two days later -> data/delta/
cp data/delta/* data/inbox/
python triage/triage.py            # queue goes from 5,332 to 5,688
```

Triage reads `data/inbox/` when it holds a `salesforce_invoices.csv`, otherwise `data/exports/`. A bundle whose
`manifest.json` says `"kind": "delta"` is laid over `data/exports/` instead of replacing it. Empty the inbox afterwards.

### Inputs (one export bundle)

| File | What it is |
|---|---|
| `salesforce_invoices.csv` | One row per invoice: account, owner, amount, amount_paid, status, due date, last touch, dispute flag |
| `collector_sheet.csv` | Collector notes, one row per touch: invoice_id, collector, touch_date, note |
| `slack_threads.json` | Slack threads about an invoice, with last_message_date |
| `manifest.json` | `kind` (full or delta) and `as_of`, the snapshot date that all ageing is measured from |

### Policy (`policy.yaml`)

An invoice is queued when it is not paid, at least `min_days_past_due` past due, untouched for at least
`stale_touch_days` (or never touched), and its remaining balance is at most `agent_max_amount`.
Buckets: `writeoff` if the balance is at most `write_off_max_amount`, `escalate` if disputed, otherwise `agent`.

The latest touch across Salesforce, the collector sheet and Slack wins. When sources disagree by `stale_touch_days`
or more, the invoice is listed under `conflicts`.

### Outputs

- `out/queue.json`: `as_of`, `policy`, `totals`, `items` (oldest first, with sheet notes and the Slack thread so the
  drafting step has context) and `conflicts`.
- `out/headline.json`: the slide numbers, including hours at `minutes_per_touch` per queued invoice.

## Disputes (the escalate bucket)

The drafting agent leaves disputed invoices for a person. This step makes sure that person gets a worked case
instead of a bare flag. No model; it runs right after triage.

```
python disputes/disputes.py        # -> out/disputes/
python -m pytest disputes/         # or: python disputes/test_disputes.py
```

Cron on the box: `python3 triage/triage.py && python3 disputes/disputes.py && ...` (the rest of the agent cycle).

For each disputed invoice in `out/queue.json`:

- **Reason**, read from the collector-sheet notes and Slack messages triage attached: already paid, usage overage,
  missing PO or vendor portal, invoice paperwork, payment since promised (a stale dispute flag), or no reason recorded.
  The matching lines are kept as evidence.
- **Owner**: deal desk for usage overages, billing ops for "already paid" and paperwork, otherwise the account owner.
- **Priority and deadline**: P1 if the balance is $250+ or 90+ days past due, P2 at 60+ days, else P3; respond within
  2, 5 or 10 business days of the snapshot date.
- **Recommendation**: the playbook step for the reason, or a credit memo when the balance is at or under
  `--concede-max` (default $25), except when the customer says it was paid or has promised to pay.
- **Drafts**: a Salesforce dispute case, a note to the owning team (cc the account owner), and a holding reply to the
  customer that never asks for payment.

Outputs: `out/disputes/<invoice_id>.json`, `out/disputes/salesforce_cases.csv` (one row per case, Data Loader
columns) and `out/disputes/summary.json` (counts and dollars by reason, owner and priority, for the deck).

All names are invented and every email domain uses the reserved `.example` TLD.

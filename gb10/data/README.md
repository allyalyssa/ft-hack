# Data

Synthetic exports in the CONTRACT.md formats. Seed 7, so every run writes the same files (checked on Python 3.9 to 3.14).

    python3 data/generate.py            # data/exports/, 20,000 invoices
    python3 data/generate.py --delta    # data/exports/delta/, the same 20,000 plus 200 new

The full export, as of 2026-10-25, snapshot `2026-10-25T0600`:

- `salesforce_invoices.csv`: 20,000 invoices, 1,200 accounts, 12 account owners. 70% are $500 or less, 3% under $5 (191 under $1), 252 over $100,000. Aging runs 0 to 180 days past due. 35% of open invoices have no `last_touch_date`.
- `collector_sheet.csv`: 7,121 notes on 3,989 invoices. On 200 the sheet is newer than Salesforce. 40 flag a dispute Salesforce still shows as open.
- `slack_threads.json`: 300 threads, 2 to 5 messages each.
- `policy.yaml` and `manifest.json`.

Invoices the demo can point at: INV-02137 ($0.37), INV-04013 ($1.12), INV-03084 ($40.00), INV-06333 ($140,000.00, not queued).

The delta is the later snapshot `2026-10-25T1430`, the way a real export arrives: the full export unchanged, plus INV-20001 to INV-20200 with their sheet rows and Slack threads. 60 of the new invoices are 30 or 31 days past due, and the queue grows from 3,216 to 3,259. `platform/drop_delta.sh` copies it over `data/exports/` and pushes it to the box. `python3 data/generate.py` puts the 06:00 snapshot back.

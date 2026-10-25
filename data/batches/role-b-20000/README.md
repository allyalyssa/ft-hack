# Additional Role B invoice world

Synthetic data only. No model was used. This is a separate batch; the original active export is unchanged.

- 20,000 invoices, INV-140000 through INV-159999; no IDs overlap the root demo export.
- 1,200 accounts and 12 account owners.
- 13,981 invoices below $500 (69.9%); 598 below $5 (3.0%), including 197 below $1.
- 304 invoices above $100,000.
- 7,300 collector notes on 4,073 invoices and 300 Slack threads containing 990 messages.
- Snapshot date: 2026-10-25. Seed: 43.
- Formats follow gb10/CONTRACT.md; use the GB10 Role B triage with this bundle.

## Deterministic triage

`out/queue.json` contains 3,183 queued invoices totaling $441,398.26: 451 write-offs, 2,600 collection follow-ups and 132 escalations. Triage detected 240 source conflicts. `out/headline.json` contains the detailed counts.

## Examples

| Invoice | Amount | Age | Latest Salesforce touch |
|---|---:|---:|---|
| INV-142192 | $0.37 | 95 days past due | Never |
| INV-143086 | $1.12 | 64 days past due | 41 days ago |
| INV-146180 | $40.00 | 47 days past due | Never |
| INV-146486 | $140,000.00 | 45 days past due | 2 days ago |

## Reproduce from the repository root

```powershell
python gb10/data/generate.py --seed 43 --invoice-start 140000 --out data/batches/role-b-20000
python gb10/triage/triage.py --input data/batches/role-b-20000 --out data/batches/role-b-20000/out
```

All five export files were regenerated and SHA-256 compared byte for byte. A second triage run produced identical decisions and totals after excluding its execution timestamp. `validation.json` records the checks and hashes.
